import asyncio
import time
import uuid
import hashlib
import aiohttp
from typing import Optional, Dict, Any
from block import Block
from transaction import Transaction
from ledger import Ledger
from mempool import Mempool
from consensus import Consensus
from rewards import RewardCalculator
from p2p import P2PNetwork
from device_fingerprint import enforce_single_node
import config


class Node:
    def __init__(self, device_id: Optional[str] = None, genesis_address: Optional[str] = None, reward_address: Optional[str] = None, p2p_port: int = 8765, private_key: Optional[str] = None, public_key: Optional[str] = None, skip_device_check: bool = False, data_dir: str = "blockchain_data", use_production_storage: bool = True, testnet_mode: bool = False):
        if not skip_device_check:
            self.device_fingerprint = enforce_single_node()
            self.device_id = self.device_fingerprint.get_device_id()
        else:
            self.device_fingerprint = None
            self.device_id = device_id or str(uuid.uuid4())
        
        self.reward_address = reward_address or f"tmpl{hashlib.sha256(self.device_id.encode()).hexdigest()[:44]}"
        self.private_key = private_key
        self.public_key = public_key
        self.data_dir = data_dir
        self.use_production_storage = use_production_storage
        
        self.ledger = Ledger(data_dir=data_dir, use_production_storage=use_production_storage)
        self.mempool = Mempool()
        
        validator_set = self.ledger.get_validator_set()
        
        # Pass ledger reference for Tendermint proposer selection
        self.consensus = Consensus([self.reward_address], validator_set, ledger=self.ledger)
        self.reward_calculator = RewardCalculator()
        
        seed_nodes = [node for node in config.SEED_NODES if node != f"ws://localhost:{p2p_port}"]
        self.p2p = P2PNetwork(self.device_id, port=p2p_port, seed_nodes=seed_nodes, private_key=private_key, public_key=public_key, testnet_mode=testnet_mode)
        
        self.is_running = False
        self.genesis_address = genesis_address or f"tmpl{'0' * 44}"
        self.p2p_port = p2p_port
        
        # STAGE 3: Sync gating - prevent proposing until fully synced
        self.synced = False
        
        # BOOTSTRAP MODE DETECTION: Check dynamically during runtime
        # This will be checked in mine_blocks() using actual p2p.seed_nodes
        # (allows run_testnet_node.py to override seed_nodes after Node creation)
        self.SYNC_LAG_THRESHOLD = 5  # Blocks behind peers before re-entering sync mode
        
        # Block gossip: Track recently seen blocks to prevent infinite loops
        self.recently_seen_blocks = set()
        
        self.p2p.register_handler("new_transaction", self.handle_new_transaction)
        self.p2p.register_handler("new_block", self.handle_new_block)
        self.p2p.register_handler("announce_node", self.handle_node_announcement)
        self.p2p.register_sync_handler(self.handle_sync_request)
        
        # CRITICAL FIX: Do NOT create genesis here - wait for P2P sync first!
        # Genesis will be created in bootstrap_or_sync() ONLY if no peers respond
        # This prevents new nodes from creating separate chains
        
        if not self.ledger.verify_chain():
            print("WARNING: Chain verification failed on startup!")
        
        on_chain_validators = self.ledger.get_validator_set()
        if on_chain_validators:
            self.consensus.set_validator_set(on_chain_validators)
        
        # AUTO-REGISTER AS VALIDATOR (On-Chain Decentralization!)
        # Create and broadcast validator registration transaction to ALL nodes
        self.pending_validator_registration = None
        
        if self.public_key and self.reward_address and self.private_key:
            # Generate device hash for Sybil resistance
            device_hash = hashlib.sha256(self.device_id.encode()).hexdigest()
            
            # Check if already registered
            if not self.ledger.is_validator_registered(self.reward_address):
                # Create validator registration transaction
                # This will be broadcast to ALL nodes and included in the next block
                nonce = self.ledger.get_nonce(self.reward_address)
                
                reg_tx = Transaction.create_validator_registration(
                    sender=self.reward_address,
                    public_key=self.public_key,
                    device_id=device_hash,
                    timestamp=time.time(),
                    nonce=nonce
                )
                
                # Sign the transaction
                reg_tx.sign(self.private_key)
                
                # Store for broadcasting after node starts
                self.pending_validator_registration = reg_tx
                
                print(f"🎉 Validator registration transaction created!")
                print(f"   Address: {self.reward_address}")
                print(f"   Device: {device_hash[:32]}...")
                print(f"   📡 Will broadcast to network when node starts")
                print(f"   ⛓️ Registration will be on-chain after next block")
            else:
                print(f"✅ Already registered as validator: {self.reward_address}")
                print(f"   Total validators: {self.ledger.get_validator_count()}")
        else:
            print(f"⚠️ Cannot register as validator: Missing wallet credentials")
            print(f"   Create a wallet first: python app/wallet.py")
        
        # CRITICAL FIX: Set callback for online validator detection
        # This allows Ledger to check which validators are actually connected via P2P
        # Used for reward distribution when attestations are unavailable
        self.ledger.set_online_validators_callback(self.get_connected_validators)
    
    async def delayed_registration(self, tx):
        """
        Delayed validator registration with retry logic.
        Waits for block sync before submitting registration transaction.
        """
        await asyncio.sleep(5)  # allow time for block sync
        print("⏳ Delayed registration: submitting validator registration TX...")
        
        if not self.submit_transaction(tx):
            print("❌ Validator registration failed — will retry in 5 seconds")
            await asyncio.sleep(5)
            asyncio.create_task(self.delayed_registration(tx))
        else:
            print("✅ Validator successfully registered AND saved to ledger!")
    
    def get_connected_validators(self) -> set:
        """
        Get validator addresses that are currently connected via P2P.
        
        This is used as a fallback for reward distribution when attestations
        are unavailable. Ensures ONLY ONLINE NODES RECEIVE BLOCK REWARDS.
        
        Returns:
            set: Addresses of validators with active P2P connections
        """
        from crypto_utils import derive_address
        
        connected_validators = set()
        
        # Add self if we're a validator (we're always online)
        if self.reward_address and self.ledger.is_validator_registered(self.reward_address):
            connected_validators.add(self.reward_address)
        
        # Check all connected peers and map their public keys to validator addresses
        for peer_id, public_key in self.p2p.peer_public_keys.items():
            try:
                # Convert public key to address
                address = derive_address(public_key)
                
                # Only include if this address is a registered validator
                if self.ledger.is_validator_registered(address):
                    connected_validators.add(address)
            except Exception:
                # Skip invalid public keys
                continue
        
        return connected_validators
    
    async def handle_new_transaction(self, data: dict, peer_id: str):
        try:
            tx = Transaction.from_dict(data["transaction"])
            
            if not tx.verify():
                return
            
            balances = {addr: bal for addr, bal in self.ledger.balances.items()}
            nonces = {addr: nonce for addr, nonce in self.ledger.nonces.items()}
            
            # Heartbeat and validator_registration transactions don't use nonce system
            # They use timestamp-based deduplication instead
            if tx.tx_type not in ("validator_heartbeat", "validator_registration"):
                expected_nonce = max(nonces.get(tx.sender, 0), self.mempool.get_pending_nonce(tx.sender))
                if tx.nonce != expected_nonce:
                    return
            
            if tx.is_valid(balances):
                self.mempool.add_transaction(tx)
        except Exception:
            pass
    
    async def handle_new_block(self, data: dict, peer_id: str):
        try:
            block = Block.from_dict(data["block"])
            latest = self.ledger.get_latest_block()
            
            # PERMANENT FIX: Handle height gaps to prevent deadlock
            # If we receive a future block, trigger sync to backfill missing blocks
            if latest and block.height > latest.height + 1:
                print(f"⚠️  HEIGHT GAP DETECTED: Received block {block.height}, current is {latest.height}")
                print(f"   Triggering sync to backfill blocks {latest.height + 1} to {block.height - 1}...")
                # Trigger HTTP batch sync to catch up
                asyncio.create_task(self._sync_missing_blocks(latest.height + 1, block.height - 1))
                return
            
            # Skip blocks we already have
            if latest and block.height <= latest.height:
                return
            
            # Normal path: process next sequential block
            if latest and block.height == latest.height + 1:
                if block.previous_hash != latest.block_hash:
                    return
                
                if block.calculate_hash() != block.block_hash:
                    return
                
                proposer_address = block.proposer if hasattr(block, 'proposer') else None
                if not proposer_address or not proposer_address.startswith("tmpl"):
                    return
                
                # CRITICAL FIX: Skip proposer validation during bootstrap period (blocks ≤10)
                # This allows syncing nodes to accept bootstrap blocks from not-yet-registered validators
                # Without this, nodes miss validator_registration transactions and have incomplete validator sets
                # DYNAMIC FALLBACK: Continue bootstrap if no active validators exist (prevents deadlock)
                active_validators_check = self.ledger.get_active_validators()
                is_bootstrap_block = block.height <= 10 or len(active_validators_check) == 0
                
                if not is_bootstrap_block:
                    # After bootstrap: enforce strict proposer validation
                    current_validators = self.ledger.get_validator_set()
                    
                    if proposer_address not in current_validators:
                        print(f"❌ REJECT Block {block.height}: Proposer {proposer_address[:20]}... not in validator set")
                        return
                    
                    # SLOT-BASED VALIDATION: Check proposer against block's slot, not height
                    # This allows height to remain sequential while slots can skip forward
                    block_slot = block.slot if hasattr(block, 'slot') and block.slot is not None else block.height
                    ranked_proposers_for_slot = self.ledger.get_ranked_proposers_for_slot(block_slot, num_ranks=3)
                    
                    if not ranked_proposers_for_slot or proposer_address not in ranked_proposers_for_slot:
                        expected_list = [p[:20]+'...' for p in (ranked_proposers_for_slot or [])]
                        print(f"❌ REJECT Block {block.height} (slot {block_slot}): Invalid proposer {proposer_address[:20]}...")
                        print(f"   Expected one of: {expected_list}")
                        return
                    
                    validator_public_key = self.ledger.get_validator_public_key(proposer_address)
                    if not validator_public_key:
                        print(f"❌ REJECT Block {block.height}: No public key for proposer {proposer_address[:20]}...")
                        return
                    
                    if not block.verify_proposer_signature(validator_public_key):
                        print(f"❌ REJECT Block {block.height}: Invalid proposer signature")
                        return
                
                computed_merkle_root = block.calculate_merkle_root()
                if block.merkle_root != computed_merkle_root:
                    return
                
                temp_balances = {addr: bal for addr, bal in self.ledger.balances.items()}
                temp_nonces = {addr: nonce for addr, nonce in self.ledger.nonces.items()}
                
                # P2P LAYER DEFENSE: Track validator registrations to prevent Sybil attacks
                # Reject blocks with duplicate device_id or public_key BEFORE ledger processing
                temp_registered_devices = set()
                temp_registered_pubkeys = set()
                
                for tx in block.transactions:
                    if not tx.verify():
                        return
                    
                    # P2P LAYER: Validate validator registration transactions
                    if tx.tx_type == "validator_registration":
                        # Check for duplicates with existing validators
                        for existing_addr, data in self.ledger.validator_registry.items():
                            if isinstance(data, dict):
                                if data.get('device_id') == tx.device_id:
                                    print(f"P2P REJECT: Device {tx.device_id[:16]}... already registered")
                                    return
                                if data.get('public_key') == tx.public_key:
                                    print(f"P2P REJECT: Public key already registered")
                                    return
                        
                        # Check for duplicates WITHIN this block (Sybil bypass prevention)
                        if tx.device_id in temp_registered_devices:
                            print(f"P2P REJECT: Block contains duplicate device registration (Sybil attack)")
                            return
                        
                        if tx.public_key in temp_registered_pubkeys:
                            print(f"P2P REJECT: Block contains duplicate pubkey registration (Sybil attack)")
                            return
                        
                        # Track this registration
                        temp_registered_devices.add(tx.device_id)
                        temp_registered_pubkeys.add(tx.public_key)
                        
                        # Validate registration transaction
                        if not tx.is_valid(temp_balances, temp_nonces):
                            return
                        
                        # Update nonce for registration
                        temp_nonces[tx.sender] = temp_nonces.get(tx.sender, 0) + 1
                    
                    else:
                        # Regular transfer transaction
                        if not tx.is_valid(temp_balances, temp_nonces):
                            return
                        
                        temp_balances[tx.sender] -= (tx.amount + tx.fee)
                        temp_balances[tx.recipient] = temp_balances.get(tx.recipient, 0) + tx.amount
                        temp_nonces[tx.sender] = temp_nonces.get(tx.sender, 0) + 1
                
                # P2P blocks: Skip strict validation (historical blocks may have old timing)
                # Only enforce timing for blocks THIS node creates (in mine_blocks function)
                self.ledger.add_block(block, skip_proposer_check=True)
                
                # SYNC LOGGING: Track block reception for debugging sync issues
                print(f"📥 BLOCK RECEIVED: Height {block.height} from peer {peer_id[:8]}... (chain now at {block.height} blocks)")
                
                # BLOCK GOSSIP: Re-broadcast to other peers to ensure full propagation
                # This fixes the issue where nodes in partial mesh don't receive blocks
                block_hash = block.block_hash
                if block_hash not in self.recently_seen_blocks:
                    # Track this block to prevent infinite gossip loops
                    self.recently_seen_blocks.add(block_hash)
                    
                    # Clean up old entries (keep last 100 blocks)
                    if len(self.recently_seen_blocks) > 100:
                        # Convert to list, remove oldest 50, convert back
                        sorted_hashes = list(self.recently_seen_blocks)
                        self.recently_seen_blocks = set(sorted_hashes[-100:])
                    
                    # Re-broadcast to all peers except the sender
                    await self.p2p.broadcast("new_block", {
                        "block": block.to_dict()
                    }, exclude_peer=peer_id)
                
                # CONSENSUS FIX: Use checkpoint-based validator set for deterministic proposer selection
                current_height = self.ledger.get_block_count() - 1
                checkpoint_validators = self.ledger.get_validator_set_at_checkpoint(current_height)
                self.consensus.set_validator_set(checkpoint_validators)
                self.consensus.update_node_activity(proposer_address)
                
                for tx in block.transactions:
                    self.mempool.remove_transaction(tx.tx_hash)
            elif latest and block.height > latest.height + 1:
                await self.p2p.broadcast("sync_request", {
                    "current_height": latest.height
                })
        except Exception:
            pass
    
    async def handle_node_announcement(self, data: dict, peer_id: str):
        try:
            reward_address = data.get("reward_address")
            if reward_address:
                self.consensus.update_node_activity(reward_address)
        except Exception:
            pass
    
    async def handle_sync_request(self, data: dict, peer_id: str, websocket):
        """
        Handle blockchain sync request from a specific peer.
        
        CRITICAL FIX: Use send_to_websocket() with direct websocket reference.
        This bypasses peer_id dictionary lookups that fail during connection churn.
        
        Args:
            data: Sync request data containing current_height
            peer_id: Peer identifier for logging
            websocket: Direct websocket connection for reliable delivery
        """
        try:
            requested_height = data.get("current_height", 0)
            latest = self.ledger.get_latest_block()
            
            print(f"📨 SYNC REQUEST from peer {peer_id[:8]}... (height {requested_height})")
            
            if not latest:
                print(f"⚠️  SYNC RESPONSE: No blocks to send (empty chain)")
                return
            
            if latest.height <= requested_height:
                print(f"ℹ️  SYNC RESPONSE: Peer is up-to-date (latest: {latest.height})")
                return
            
            # Calculate blocks to send
            start_height = requested_height + 1
            blocks_to_send = latest.height - requested_height
            print(f"📤 SYNC RESPONSE: Sending {blocks_to_send} blocks (heights {start_height}-{latest.height}) to peer {peer_id[:8]}...")
            
            # Send ALL blocks directly to websocket (bypasses peer_id lookup!)
            sent_count = 0
            failed_count = 0
            
            for height in range(start_height, latest.height + 1):
                block = self.ledger.get_block_by_height(height)
                if block:
                    send_data: Dict[str, Any] = {"block": block.to_dict()}
                    if self.public_key:
                        send_data["proposer_public_key"] = self.public_key
                    
                    # CRITICAL FIX: Send directly to websocket, not via peer_id lookup
                    success = await self.p2p.send_to_websocket(websocket, "new_block", send_data)
                    
                    if success:
                        sent_count += 1
                    else:
                        failed_count += 1
                        print(f"⚠️  SYNC FAILED: Block {height} delivery failed to websocket")
                    
                    await asyncio.sleep(0.01)  # Small delay to prevent overwhelming peer
            
            print(f"✅ SYNC COMPLETE: Sent {sent_count}/{blocks_to_send} blocks to peer {peer_id[:8]}... ({failed_count} failed)")
            
        except Exception as e:
            print(f"❌ SYNC ERROR: Exception in handle_sync_request - {e}")
            import traceback
            traceback.print_exc()
    
    async def _get_max_peer_height(self) -> int:
        """
        Query all peers via HTTP API to get maximum height in the network.
        This enables proactive catch-up when node falls behind.
        """
        peer_http_urls = []
        
        # Build HTTP URLs from P2P seed nodes
        for seed in self.p2p.seed_nodes:
            if seed.startswith('ws://'):
                host_port = seed.replace('ws://', '').replace('/', '')
                if ':' in host_port:
                    host, port_str = host_port.rsplit(':', 1)
                    try:
                        http_port = int(port_str) + 1
                        peer_http_urls.append(f"http://{host}:{http_port}")
                    except ValueError:
                        pass
        
        # Add localhost ports for local testnet
        for port in [9001, 3001, 3003, 6001]:
            peer_http_urls.append(f"http://localhost:{port}")
        
        max_height = 0
        for peer_url in peer_http_urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{peer_url}/api/health",
                        timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            peer_height = data.get('height', 0)
                            if peer_height > max_height:
                                max_height = peer_height
            except Exception:
                continue
        
        return max_height
    
    async def mine_blocks(self):
        while self.is_running:
            # SCHEDULED BLOCK TIME: Calculate exact scheduled time for next block
            # This prevents clock skew between validators from causing 4-6s gaps
            # All validators agree on scheduled_time = parent.timestamp + BLOCK_TIME
            latest_block = self.ledger.get_latest_block()
            if latest_block:
                current_time = time.time()
                scheduled_time = latest_block.timestamp + config.BLOCK_TIME
                time_to_wait = max(0.0, scheduled_time - current_time)
                
                # STAGE 2: Cap sleep time to prevent excessive delays
                # If we're behind by multiple slots, don't wait - catch up immediately
                if time_to_wait > config.BLOCK_TIME * 2:
                    # We're behind by more than 2 slots - something is wrong
                    slots_behind = int(time_to_wait / config.BLOCK_TIME)
                    print(f"⚠️  TIMING ANOMALY: Behind by {slots_behind} slots ({time_to_wait:.1f}s)")
                    print(f"   Capping sleep to {config.BLOCK_TIME}s to recover")
                    time_to_wait = config.BLOCK_TIME  # Cap to one slot
                
                # Diagnostic logging (every 10th block to avoid spam)
                if latest_block.height % 10 == 0:
                    skew = current_time - latest_block.timestamp - config.BLOCK_TIME
                    print(f"⏰ Timing: scheduled={scheduled_time:.2f}, now={current_time:.2f}, skew={skew:.2f}s")
                
                await asyncio.sleep(time_to_wait)
            else:
                # No blocks yet, use standard sleep
                scheduled_time = None
                await asyncio.sleep(config.BLOCK_TIME)
            
            # CHECKPOINT-BASED VALIDATOR SET: Use finalized validator set for consensus
            # All nodes agree on validator set from last checkpoint, ensuring deterministic
            # proposer selection even when nodes are at different heights during sync
            current_height = self.ledger.get_block_count() - 1
            checkpoint_validators = self.ledger.get_validator_set_at_checkpoint(current_height)
            if checkpoint_validators:
                self.consensus.set_validator_set(checkpoint_validators)
            
            self.consensus.update_node_activity(self.reward_address)
            
            latest_block = self.ledger.get_latest_block()
            if not latest_block:
                continue
            
            # HEIGHT IS SEQUENTIAL: Always increment by 1 (never skip)
            next_height = latest_block.height + 1
            
            # STAGE 3: Health checks before proposing
            peer_count = self.p2p.get_peer_count()
            
            # DYNAMIC BOOTSTRAP DETECTION: Check if seed_nodes are configured
            # Bootstrap nodes (no seeds) can produce blocks with 0 peers
            is_bootstrap = (len(self.p2p.seed_nodes) == 0)
            min_peers_required = 0 if is_bootstrap else 1
            
            # Check if we have enough peers (bootstrap nodes skip this check)
            if peer_count < min_peers_required:
                if next_height % 30 == 0:  # Log every 30 blocks to avoid spam
                    mode = "BOOTSTRAP" if is_bootstrap else "NETWORK"
                    print(f"⚠️  HEALTH CHECK ({mode}): {peer_count} peer(s) connected, MIN_PEERS = {min_peers_required}")
                    if not is_bootstrap:
                        print(f"   Waiting for {min_peers_required}+ peer(s) before producing blocks")
                    else:
                        print(f"   Bootstrap mode: producing blocks without peer requirements")
                await asyncio.sleep(1.0)
                continue
            
            # PROACTIVE CATCH-UP: Before attempting to create blocks, check if we're behind
            # This prevents deadlock where nodes at different heights all skip their turns
            # because they're waiting for blocks from other validators who are also behind
            max_peer_height = await self._get_max_peer_height()
            local_height = latest_block.height
            
            # STAGE 3: Mark as not synced if we fall too far behind
            if max_peer_height > local_height + self.SYNC_LAG_THRESHOLD:
                if self.synced:
                    print(f"⚠️  SYNC STATUS: Falling behind (local: {local_height}, max peer: {max_peer_height})")
                    print(f"   Entering sync mode, stopping block production temporarily")
                    self.synced = False
            
            if max_peer_height > local_height:
                print(f"🔄 CATCH-UP MODE: Local height {local_height}, max peer height {max_peer_height}")
                print(f"   Syncing missing blocks {local_height + 1} to {max_peer_height}...")
                await self._sync_missing_blocks(local_height + 1, max_peer_height)
                
                # STAGE 3: Mark as synced when caught up
                if max_peer_height - local_height <= self.SYNC_LAG_THRESHOLD:
                    if not self.synced:
                        print(f"✅ SYNC STATUS: Caught up! (local: {local_height}, max peer: {max_peer_height})")
                        print(f"   Resuming block production")
                        self.synced = True
                
                # After catch-up, skip this mining cycle to refresh state
                continue
            else:
                # We're at head - mark as synced
                if not self.synced:
                    print(f"✅ SYNC STATUS: At network head (height: {local_height})")
                    self.synced = True
            
            # STAGE 3: Gate proposer participation until synced
            if not self.synced:
                if next_height % 30 == 0:  # Log every 30 blocks to avoid spam
                    print(f"⏸️  NOT SYNCED: Skipping block production until fully synced")
                await asyncio.sleep(config.BLOCK_TIME)
                continue
            
            
            # TIME-SLICED SLOTS CONSENSUS: Deterministic fallback without race conditions
            # Each 3-second slot is divided into 3×1-second windows:
            # Window 0 (0-1s): Primary proposer only
            # Window 1 (1-2s): Fallback #1 only  
            # Window 2 (2-3s): Fallback #2 only
            # 
            # KEY: Blocks are ONLY valid if timestamp falls in correct window for their rank
            # This prevents race conditions when offline validators come back online
            from time_slots import (
                get_realtime_slot, am_i_proposer_now, time_until_my_window
            )
            
            # Get genesis timestamp for window calculations
            genesis_block = self.ledger.get_block_by_height(0)
            if not genesis_block:
                print(f"❌ No genesis block found, cannot determine time windows")
                continue
            
            genesis_timestamp = genesis_block.timestamp
            
            # SLOT IS WALL-CLOCK BASED: Calculate real-time slot independent of chain height
            # This allows the network to "catch up" to current time after bootstrap period
            realtime_slot = get_realtime_slot(genesis_timestamp)
            
            # SAFE CATCH-UP: Find the next slot whose primary window is still open or upcoming
            # This preserves Time-Sliced Windows invariant: strict window enforcement always
            from time_slots import current_slot_and_rank, WINDOW_SECONDS
            current_time = time.time()
            current_slot_check, active_rank = current_slot_and_rank(genesis_timestamp, current_time)
            
            # If we're in the first sub-window (rank 0) of a slot, use that slot
            # Otherwise, advance to the next slot to ensure primary window hasn't passed
            if active_rank == 0:
                current_slot = current_slot_check
            else:
                current_slot = current_slot_check + 1
            
            # Log skipped slots when we jump forward
            if latest_block.slot and current_slot > latest_block.slot + 1:
                skipped_slots = current_slot - latest_block.slot - 1
                print(f"⏩ SLOT SKIP: Jumped from slot {latest_block.slot} to {current_slot}")
                print(f"   Skipped {skipped_slots} empty slot(s) - network catching up to real-time")
                print(f"   Height remains sequential: {latest_block.height} → {next_height}")
                print(f"   Active rank in current slot: {active_rank} (using next slot to ensure primary window available)")
            
            # Get ranked proposers for this slot (primary, fallback1, fallback2)
            ranked_proposers = self.ledger.get_ranked_proposers_for_slot(current_slot, num_ranks=3)
            
            if not ranked_proposers:
                # No validators available - wait for network to stabilize
                if next_height % 10 == 0:
                    print(f"⚠️  No active validators at height {next_height}, waiting...")
                continue
            
            # Check if I'm one of the ranked proposers for this slot
            my_rank = None
            for i, addr in enumerate(ranked_proposers):
                if addr == self.reward_address:
                    my_rank = i
                    break
            
            if my_rank is None:
                # DEBUG: Print proposers to diagnose why nodes aren't being selected
                print(f"🔍 DEBUG: Height {next_height}, Proposers: {[p[:20]+'...' for p in ranked_proposers]}, Me: {self.reward_address[:20]}...")
                # Not my turn to propose - wait for next slot
                await asyncio.sleep(0.1)
                continue
            
            # I'm a ranked proposer! Check if it's currently my window
            # BOOTSTRAP: Use lenient timing for first 10 blocks to handle stale genesis timestamp
            # After block 10, strict Time-Sliced Windows enforcement (preserves safety invariant)
            lenient_bootstrap = next_height <= 10
            
            is_my_turn, _ = am_i_proposer_now(self.reward_address, ranked_proposers, 
                                               genesis_timestamp, current_slot, 
                                               lenient_bootstrap=lenient_bootstrap)
            
            if not is_my_turn:
                # Not my window yet - check when my window opens
                wait_time = time_until_my_window(my_rank, genesis_timestamp, current_slot)
                
                if wait_time > 0 and wait_time < 1.5:
                    # My window is upcoming - wait for it to open
                    print(f"⏰ Rank {my_rank} proposer waiting {wait_time:.2f}s for window")
                    await asyncio.sleep(min(wait_time + 0.1, 0.5))  # Wait with small buffer
                    continue
                else:
                    # My window already passed or too far in future - check if block received
                    current_height = self.ledger.get_block_count() - 1
                    if current_height >= next_height:
                        # Block received from another proposer
                        continue
                    
                    # Window passed but no block - move to next cycle
                    await asyncio.sleep(0.1)
                    continue
            
            # IT'S MY WINDOW! Proceed to create and propose block
            print(f"✅ Rank {my_rank} proposer - it's my window, creating block...")
            
            pending_txs = self.mempool.get_pending_transactions(3000)
            
            valid_txs = []
            total_fees = 0
            temp_balances = {addr: bal for addr, bal in self.ledger.balances.items()}
            temp_nonces = {addr: nonce for addr, nonce in self.ledger.nonces.items()}
            
            for tx in pending_txs:
                # CRITICAL: Skip expired epoch attestations to prevent block rejection
                # Attestations have a deadline and cannot be included after that
                if tx.tx_type == "epoch_attestation":
                    all_validators = set(self.ledger.get_validator_set())
                    is_valid_attestation, reason = self.ledger.attestation_manager.validate_attestation(
                        tx.epoch_number, tx.sender, next_height, all_validators
                    )
                    if not is_valid_attestation:
                        # Skip this expired/invalid attestation
                        continue
                
                if tx.is_valid(temp_balances, temp_nonces) and tx.verify():
                    valid_txs.append(tx)
                    total_fees += tx.fee
                    
                    # Validator registration and heartbeat transactions don't transfer funds
                    # They only register the validator or signal liveness
                    if tx.tx_type not in ("validator_registration", "validator_heartbeat", "epoch_attestation"):
                        # Regular transfer: deduct from sender, add to recipient
                        temp_balances[tx.sender] -= (tx.amount + tx.fee)
                        temp_balances[tx.recipient] = temp_balances.get(tx.recipient, 0) + tx.amount
                        
                        # Update nonce only for regular transactions (not heartbeats/registrations)
                        temp_nonces[tx.sender] = temp_nonces.get(tx.sender, 0) + 1
            
            # CRITICAL FIX: Use active validators (DETERMINISTIC heartbeat check)
            # Only validators with recent heartbeat transactions (last 5 blocks) earn rewards
            # This is deterministic - all nodes see the same blocks/transactions
            active_validators = self.ledger.get_active_validators()
            
            # BOOTSTRAP FIX: During early blocks, no validators may be active yet
            # (genesis validators are status="genesis", new validators have 2-block delay)
            # In this case, credit all rewards to the proposer to prevent lost coins
            if not active_validators:
                print(f"🔧 BOOTSTRAP FIX: No active validators, crediting rewards to proposer {self.reward_address[:20]}...")
                active_validators = [self.reward_address]
            
            print(f"💰 Reward calculation: {len(active_validators)} active validators")
            rewards, block_reward = self.reward_calculator.calculate_reward(
                active_validators, 
                total_fees, 
                self.ledger.total_emitted_pals
            )
            print(f"💰 Rewards calculated: {len(rewards)} recipients, Total reward: {block_reward / 100_000_000:.8f} TMPL")
            
            # CRITICAL FIX (ChatGPT): Clamp timestamp into slot/rank window
            # Previous bug: used min(scheduled_time, time.time()) which created timestamps in the PAST
            # relative to the slot/rank window, causing ledger to reject blocks (stuck at height 1)
            # 
            # Fix: Calculate the exact time-sliced window for (slot, rank) and clamp timestamp into it
            # This ensures the block timestamp is valid for its assigned window and passes ledger validation
            if scheduled_time is None:
                # Genesis block case - no scheduled time yet
                block_timestamp = time.time()
            else:
                # Calculate the time-sliced window bounds for this (slot, rank)
                slot_start = genesis_timestamp + current_slot * config.BLOCK_TIME
                window_start = slot_start + my_rank * WINDOW_SECONDS
                window_end = window_start + WINDOW_SECONDS
                
                # Small epsilon to avoid boundary precision issues (ChatGPT Fix F: increased to 50ms)
                EPS = 0.050
                
                # Pick a timestamp that:
                # (a) is not before scheduled_time (monotonic chain requirement)
                # (b) lies inside [window_start, window_end) (time-sliced window requirement)
                # (c) never goes into the future (clock skew safety)
                now = time.time()
                
                # Start with max(scheduled_time, window_start + epsilon) to ensure we're in the window
                candidate = max(scheduled_time, window_start + EPS)
                # Cap at window_end and current time
                candidate = min(candidate, window_end - EPS, now)
                
                # Safety check: if window already passed (rare race condition), skip this round
                if candidate < window_start or candidate >= window_end:
                    print(f"⚠️  Window already passed for slot {current_slot} rank {my_rank}, skipping")
                    await asyncio.sleep(0.05)
                    continue
                
                block_timestamp = candidate
                
                # Timing diagnostic to track skew reduction after fix
                scheduled_vs_now = now - scheduled_time
                print(f"⏰ Timing: scheduled={scheduled_time:.2f}, now={now:.2f}, skew={scheduled_vs_now:.2f}s")
            
            new_block = Block(
                height=next_height,
                timestamp=block_timestamp,
                transactions=valid_txs,
                previous_hash=latest_block.block_hash,
                proposer=self.reward_address,
                reward=block_reward,
                reward_allocations=rewards,
                slot=current_slot,
                rank=my_rank
            )
            
            if self.private_key:
                new_block.sign_block(self.private_key)
            
            # CRITICAL FIX: Check if block was actually added to ledger
            # add_block returns False if block is rejected (duplicate, invalid, etc.)
            success = self.ledger.add_block(new_block)
            if not success:
                print(f"ℹ️  NOTE: Block {new_block.height} already exists — duplicate attempt skipped (normal behavior)")
                continue  # Skip this cycle and try again
            
            # CRITICAL: Log block creation so we can track chain progression
            print(f"✅ Block {new_block.height} created and added to ledger")
            print(f"   Proposer: {self.reward_address[:20]}...")
            print(f"   Transactions: {len(valid_txs)}, Reward: {block_reward / 100_000_000:.8f} TMPL")
            
            for tx in valid_txs:
                self.mempool.remove_transaction(tx.tx_hash)
            
            broadcast_data: Dict[str, Any] = {"block": new_block.to_dict()}
            if self.public_key:
                broadcast_data["proposer_public_key"] = self.public_key
            await self.p2p.broadcast("new_block", broadcast_data)
            
            print(f"📡 Block {new_block.height} broadcasted to network")
    
    async def announce_presence(self):
        """
        Broadcast validator presence to network for round-robin consensus.
        CRITICAL: Must announce more frequently than BLOCK_TIME to stay "online"
        in consensus tracking. Otherwise round-robin won't include this validator.
        """
        while self.is_running:
            await self.p2p.broadcast("announce_node", {"reward_address": self.reward_address})
            await asyncio.sleep(2)  # Announce every 2 seconds (< BLOCK_TIME of 3s)
    
    async def send_heartbeats(self):
        """
        DEPRECATED: Heartbeat transactions are disabled to prevent mempool flooding.
        
        PROBLEM: With 100K+ validators sending heartbeats every 2 seconds, mempool would be
        flooded with millions of heartbeats, blocking ALL user money transfers from blocks.
        
        SOLUTION: Use P2P announce_presence() for liveness tracking instead.
        Validators announce via lightweight P2P messages, not blockchain transactions.
        """
        # DISABLED: Do not send heartbeat transactions to mempool
        return
    
    async def send_epoch_attestations(self):
        """
        DEPRECATED: Attestation transactions disabled to prevent transaction pool flooding.
        
        PROBLEM: Attestations as transactions would flood mempool with non-payment transactions,
        blocking user money transfers from being included in blocks.
        
        SOLUTION: Validators use announce_presence() P2P messages for liveness tracking.
        Liveness is tracked via:
        1. Recent block proposers (validators who created blocks recently)
        2. Recently activated validators (grace period for new validators)
        3. P2P announce_presence() messages (lightweight, not on-chain)
        
        CONSENSUS: All nodes see the same proposers in finalized blocks, ensuring
        deterministic validator selection without requiring attestation transactions.
        """
        # DISABLED: Do not send attestation transactions to mempool
        return
    
    async def http_batch_sync(self, peer_urls: list):
        """
        HTTP-based batch sync (Tendermint/Cosmos-inspired).
        Downloads blocks in batches via HTTP API instead of websockets.
        More reliable than websocket sync for initial catchup.
        """
        import aiohttp
        
        print(f"🔍 HTTP Batch Sync: Attempting to sync from {len(peer_urls)} peer(s)")
        for i, peer_url in enumerate(peer_urls):
            print(f"🔍 HTTP Batch Sync [{i+1}/{len(peer_urls)}]: Trying {peer_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    # Get peer's current height
                    health_url = f"{peer_url}/api/health"
                    print(f"🔍 HTTP Batch Sync: Fetching {health_url}")
                    async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        print(f"🔍 HTTP Batch Sync: Health check status={resp.status}")
                        if resp.status != 200:
                            print(f"⚠️  HTTP Batch Sync: Health check failed with status {resp.status}")
                            continue
                        health = await resp.json()
                        peer_height = health.get('height', 0)
                        print(f"🔍 HTTP Batch Sync: Peer reports height={peer_height}")
                        
                        my_height = self.ledger.get_block_count() - 1
                        print(f"🔍 HTTP Batch Sync: My height={my_height}, peer height={peer_height}")
                        
                        if my_height >= peer_height:
                            print(f"⏭️  HTTP Batch Sync: Skipping peer (we have {my_height}, peer has {peer_height})")
                            continue
                        
                        print(f"📡 Peer {peer_url} has {peer_height} blocks, starting HTTP batch sync...")
                        
                        # Download blocks in batches of 100
                        current_height = -1  # Start at -1 so first batch starts at 0 (genesis)
                        batch_size = 100
                        
                        while current_height < peer_height:
                            start = current_height + 1
                            end = min(start + batch_size - 1, peer_height)
                            
                            try:
                                async with session.get(
                                    f"{peer_url}/api/blocks/range?start={start}&end={end}",
                                    timeout=aiohttp.ClientTimeout(total=30)
                                ) as blocks_resp:
                                    if blocks_resp.status != 200:
                                        print(f"⚠️  Failed to fetch blocks {start}-{end} from {peer_url}")
                                        break
                                    
                                    data = await blocks_resp.json()
                                    blocks = data.get('blocks', [])
                                    
                                    if not blocks:
                                        print(f"⚠️  No blocks received from {peer_url}")
                                        break
                                    
                                    # Validate and add blocks sequentially
                                    for block_dict in blocks:
                                        block = Block.from_dict(block_dict)
                                        
                                        # Validate block before adding
                                        # CRITICAL: Skip proposer validation during sync to avoid rejecting blocks
                                        # due to stale local checkpoint state (prevents sync deadlock)
                                        if not self.ledger.add_block(block, skip_proposer_check=True):
                                            print(f"❌ Block {block.height} validation failed, stopping sync")
                                            return False
                                        
                                        current_height = block.height
                                    
                                    print(f"✅ HTTP Sync: Downloaded blocks {start}-{end} ({len(blocks)} blocks)")
                                    
                            except Exception as e:
                                print(f"⚠️  Error fetching batch {start}-{end}: {e}")
                                break
                        
                        if current_height >= 0:
                            print(f"🎉 HTTP batch sync complete! Synced to height {current_height} from {peer_url}")
                            return True
                            
            except Exception as e:
                import traceback
                print(f"❌ HTTP Batch Sync: Exception for {peer_url}")
                print(f"   Error type: {type(e).__name__}")
                print(f"   Error message: {str(e)}")
                print(f"   Traceback: {traceback.format_exc()}")
                continue
        
        print("❌ HTTP Batch Sync: All peers failed, returning False")
        return False
    
    async def _fetch_full_chain(self, peer_url: str, session, end_height: int):
        """
        Fetch complete blockchain from genesis to end_height from a peer.
        Used for chain reorganization to get the competing chain.
        
        Args:
            peer_url: HTTP URL of the peer
            session: aiohttp ClientSession
            end_height: Last block height to fetch
            
        Returns:
            List[Block] if successful, None if failed
        """
        print(f"📥 Fetching full competing chain from {peer_url} (genesis to {end_height})...")
        
        CHUNK_SIZE = 100
        all_blocks = []
        
        try:
            current_start = 0  # Start from genesis
            
            while current_start <= end_height:
                current_end = min(current_start + CHUNK_SIZE - 1, end_height)
                
                async with session.get(
                    f"{peer_url}/api/blocks/range?start={current_start}&end={current_end}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        print(f"⚠️  Failed to fetch blocks {current_start}-{current_end}: HTTP {resp.status}")
                        return None
                    
                    data = await resp.json()
                    blocks = data.get('blocks', [])
                    
                    if not blocks:
                        print(f"⚠️  No blocks returned for range {current_start}-{current_end}")
                        return None
                    
                    # Convert to Block objects
                    for block_dict in blocks:
                        all_blocks.append(Block.from_dict(block_dict))
                    
                    current_start = current_end + 1
            
            print(f"✅ Fetched {len(all_blocks)} blocks from competing chain")
            return all_blocks
            
        except Exception as e:
            print(f"⚠️  Error fetching full chain: {e}")
            return None
    
    async def _sync_missing_blocks(self, start_height: int, end_height: int):
        """
        PERMANENT FIX: Backfill missing blocks when height gap detected.
        This prevents permanent deadlock when nodes miss blocks.
        
        Args:
            start_height: First missing block height
            end_height: Last missing block height
        """
        
        print(f"🔄 BACKFILL: Fetching blocks {start_height} to {end_height}...")
        
        # Build list of HTTP endpoints to try
        peer_http_urls = []
        
        # Add all seed nodes
        for seed in self.p2p.seed_nodes:
            if seed.startswith('ws://'):
                host_port = seed.replace('ws://', '').replace('/', '')
                if ':' in host_port:
                    host, port_str = host_port.rsplit(':', 1)
                    try:
                        http_port = int(port_str) + 1
                        http_url = f"http://{host}:{http_port}"
                        peer_http_urls.append(http_url)
                    except ValueError:
                        pass
        
        # Try localhost ports (for local testnet nodes)
        for port in [9001, 3001, 3003, 6001]:
            peer_http_urls.append(f"http://localhost:{port}")
        
        # CRITICAL FIX: Split requests into chunks of 100 blocks (server limit)
        # Server enforces max 100 blocks per request to prevent memory issues
        CHUNK_SIZE = 100
        
        # Fetch missing blocks from peers
        for peer_url in peer_http_urls:
            try:
                async with aiohttp.ClientSession() as session:
                    current_start = start_height
                    success = True
                    
                    # Fetch in chunks of 100 blocks
                    while current_start <= end_height:
                        current_end = min(current_start + CHUNK_SIZE - 1, end_height)
                        
                        async with session.get(
                            f"{peer_url}/api/blocks/range?start={current_start}&end={current_end}",
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            if resp.status != 200:
                                error_text = await resp.text()
                                print(f"⚠️  BACKFILL: HTTP {resp.status} from {peer_url}: {error_text}")
                                success = False
                                break
                            
                            data = await resp.json()
                            blocks = data.get('blocks', [])
                            
                            if not blocks:
                                print(f"⚠️  BACKFILL: No blocks returned for range {current_start}-{current_end}")
                                success = False
                                break
                            
                            # Add blocks sequentially
                            for block_dict in blocks:
                                block = Block.from_dict(block_dict)
                                
                                # FIX: Check if block already exists to avoid race condition
                                # (Block might arrive via gossip while backfill is in progress)
                                if block.height < len(self.ledger.blocks):
                                    # Block already exists, skip silently
                                    continue
                                
                                if self.ledger.add_block(block, skip_proposer_check=True):
                                    print(f"✅ BACKFILL: Added block {block.height}")
                                else:
                                    # FORK DETECTION: If block rejected, check if it's due to previous_hash mismatch (fork)
                                    latest = self.ledger.get_latest_block()
                                    if latest and block.height == latest.height + 1 and block.previous_hash != latest.block_hash:
                                        print(f"🔀 FORK DETECTED at height {block.height}!")
                                        print(f"   Local chain hash: {latest.block_hash}")
                                        print(f"   Competing chain hash: {block.previous_hash}")
                                        print(f"   Attempting chain reorganization...")
                                        
                                        # Fetch full competing chain from this peer (genesis to end)
                                        try:
                                            competing_chain = await self._fetch_full_chain(peer_url, session, end_height)
                                            if competing_chain:
                                                # Trigger reorganization - let fork choice decide which chain wins
                                                reorg_success, reorg_msg = self.ledger.reorganize_to_chain(competing_chain)
                                                if reorg_success:
                                                    print(f"✅ {reorg_msg}")
                                                    # After successful reorg, try to continue syncing
                                                    current_start = len(self.ledger.blocks)
                                                    break
                                                else:
                                                    print(f"⚠️  Reorganization rejected: {reorg_msg}")
                                                    print(f"   Current chain is canonical - continuing with local chain")
                                                    return
                                            else:
                                                print(f"⚠️  Could not fetch competing chain for reorganization")
                                                return
                                        except Exception as e:
                                            print(f"⚠️  Error during fork resolution: {e}")
                                            return
                                    else:
                                        print(f"⚠️  BACKFILL: Failed to add block {block.height} - validation rejected this block")
                                        print(f"   (Check logs above for REJECT message with details)")
                                        print(f"   Trying next peer...")
                                        success = False
                                        break
                            
                            # Move to next chunk
                            current_start = current_end + 1
                    
                    # If we successfully synced all chunks from this peer, we're done!
                    if success and current_start > end_height:
                        print(f"🎉 BACKFILL COMPLETE: Synced blocks {start_height} to {end_height}")
                        return
                        
            except Exception as e:
                print(f"⚠️  BACKFILL: Error fetching from {peer_url}: {e}")
                continue
        
        # If we got here, ALL peers failed to provide valid blocks
        # This could be due to:
        # 1. Network partition (all peers unreachable)
        # 2. Local database corruption
        # 3. Being on a minority fork
        print(f"❌ BACKFILL FAILED: Could not sync blocks {start_height} to {end_height} from any peer")
        print(f"")
        print(f"⚠️  SYNC STUCK - Possible causes:")
        print(f"   1. Network partition (all peers unreachable)")
        print(f"   2. Local database corruption")
        print(f"   3. On a minority fork")
        print(f"")
        print(f"🔧 RECOVERY: If this persists, manually reset database:")
        print(f"   1. Stop this node")
        print(f"   2. Delete: {self.data_dir}")
        print(f"   3. Restart node (will resync from genesis)")
        
        # Do NOT auto-delete - too dangerous (could be temporary network issue)
    
    async def bootstrap_or_sync(self):
        """
        Hybrid sync: HTTP batch sync + websocket real-time sync.
        
        Based on Tendermint/Cosmos approach:
        1. HTTP batch sync for initial catchup (reliable, fast)
        2. Websocket sync for real-time updates once caught up
        
        This solves the websocket connection issues during initial sync.
        """
        print("🔄 Starting blockchain sync...")
        
        # Check if we already have blocks (node restart scenario)
        if self.ledger.get_block_count() > 0:
            print(f"✅ Already have {self.ledger.get_block_count()} blocks, skipping sync")
            return
        
        # Wait a moment for P2P connections to establish
        await asyncio.sleep(2)
        
        # Check if we have any peers
        peer_count = self.p2p.get_peer_count()
        if peer_count == 0:
            print("⚠️  No peers connected - creating genesis block (bootstrap node)")
            genesis_block = Block.create_genesis_block(self.genesis_address)
            self.ledger.add_block(genesis_block)
            return
        
        print(f"📡 Connected to {peer_count} peer(s), starting HTTP batch sync...")
        
        # Build HTTP URLs for seed nodes (assuming HTTP port = P2P port + 1)
        peer_http_urls = []
        print(f"🔍 Building HTTP URLs from {len(self.p2p.seed_nodes)} seed node(s)")
        for seed in self.p2p.seed_nodes:
            print(f"🔍 Seed node: {seed}")
            # Convert ws://localhost:9000 -> http://localhost:9001
            if seed.startswith('ws://'):
                host_port = seed.replace('ws://', '').replace('/', '')
                if ':' in host_port:
                    host, port_str = host_port.rsplit(':', 1)
                    try:
                        http_port = int(port_str) + 1
                        http_url = f"http://{host}:{http_port}"
                        peer_http_urls.append(http_url)
                        print(f"🔍 Generated HTTP URL: {http_url}")
                    except ValueError:
                        print(f"⚠️  Failed to parse port from {seed}")
        
        print(f"🔍 Total HTTP URLs generated: {len(peer_http_urls)}")
        
        # Try HTTP batch sync first (Tendermint-style)
        if peer_http_urls:
            print(f"🔍 Starting HTTP batch sync with {len(peer_http_urls)} URL(s)")
            sync_success = await self.http_batch_sync(peer_http_urls)
            if sync_success:
                print(f"✅ HTTP batch sync complete! Chain height: {self.ledger.get_block_count()}")
                return
        
        # Fallback: Create genesis if HTTP sync failed
        print("⚠️  HTTP batch sync failed - creating genesis block")
        genesis_block = Block.create_genesis_block(self.genesis_address)
        self.ledger.add_block(genesis_block)
    
    
    async def start(self):
        self.is_running = True
        
        # Log connection information for troubleshooting
        print(f"\n{'='*60}")
        print(f"🚀 TIMPAL Node Starting")
        print(f"{'='*60}")
        print(f"📡 P2P Port: {self.p2p_port}")
        print(f"🌐 Seed Nodes: {self.p2p.seed_nodes if self.p2p.seed_nodes else 'None (Bootstrap mode)'}")
        
        # Determine and log node mode
        if len(self.p2p.seed_nodes) == 0:
            print(f"\n🔥 [BOOTSTRAP MODE]")
            print(f"   This node is acting as the genesis/bootstrap node.")
            print(f"   It will create blocks without requiring peer connections.")
            print(f"   Other validators should connect to: ws://YOUR_IP:{self.p2p_port}")
        else:
            print(f"\n🌐 [NETWORK MODE]")
            print(f"   This node will connect to the existing testnet.")
            print(f"   Attempting to connect to {len(self.p2p.seed_nodes)} seed node(s)...")
        print(f"{'='*60}\n")
        
        await self.p2p.connect_to_seeds()
        
        # CRITICAL: Bootstrap or sync BEFORE validator registration
        # This ensures new nodes sync the blockchain from network FIRST
        # instead of creating their own genesis blocks
        await self.bootstrap_or_sync()
        
        # Refresh validator set after sync using checkpoint-based approach
        # This ensures consensus uses finalized validator sets for deterministic proposer selection
        current_height = self.ledger.get_block_count() - 1
        checkpoint_validators = self.ledger.get_validator_set_at_checkpoint(current_height)
        if checkpoint_validators:
            self.consensus.set_validator_set(checkpoint_validators)
        
        # Broadcast pending validator registration transaction (if any) with delayed retry
        if self.pending_validator_registration:
            print(f"📡 Starting delayed validator registration...")
            asyncio.create_task(self.delayed_registration(self.pending_validator_registration))
            self.pending_validator_registration = None  # Clear to avoid double-registration
        
        await asyncio.gather(
            self.mine_blocks(),
            self.p2p.start_server(),
            self.announce_presence(),
            self.send_heartbeats(),
            self.send_epoch_attestations(),
            self.p2p.peer_discovery_loop()
        )
    
    def stop(self):
        self.is_running = False
        if self.use_production_storage and self.ledger.production_storage:
            self.ledger.production_storage.close()
            print(f"🔒 Node {self.device_id[:8]}... production storage closed")
    
    def submit_transaction(self, tx: Transaction) -> bool:
        balances = {addr: bal for addr, bal in self.ledger.balances.items()}
        nonces = {addr: nonce for addr, nonce in self.ledger.nonces.items()}
        
        # Debug logging for transfer transactions
        is_transfer = tx.tx_type not in ("validator_heartbeat", "validator_registration", "epoch_attestation")
        
        # Heartbeat, epoch_attestation, and validator_registration transactions don't use nonce system
        # They use timestamp/epoch-based deduplication instead
        if is_transfer:
            expected_nonce = max(nonces.get(tx.sender, 0), self.mempool.get_pending_nonce(tx.sender))
            if tx.nonce != expected_nonce:
                print(f"❌ TX REJECT: Nonce mismatch - expected {expected_nonce}, got {tx.nonce}")
                return False
        
        if not tx.is_valid(balances):
            if is_transfer:
                print(f"❌ TX REJECT: Invalid - balance check failed (bal: {balances.get(tx.sender, 0)}, need: {tx.amount + tx.fee})")
            return False
        
        if not tx.verify():
            if is_transfer:
                print(f"❌ TX REJECT: Signature verification failed")
            return False
        
        if not self.mempool.add_transaction(tx):
            if is_transfer:
                print(f"❌ TX REJECT: Mempool rejected (duplicate or full?)")
            return False
        
        asyncio.create_task(self.p2p.broadcast("new_transaction", {"transaction": tx.to_dict()}))
        
        if is_transfer:
            print(f"✅ TX ACCEPTED: {tx.sender[:20]}... → {tx.recipient[:20]}... ({tx.amount / 1e12:.4f} TMPL)")
        
        return True
    
    def get_balance(self, address: str) -> int:
        return self.ledger.get_balance(address)
    
    def get_latest_blocks(self, count: int = 10):
        blocks = self.ledger.blocks[-count:]
        return [b.to_dict() for b in reversed(blocks)]
    
    def get_block_by_height(self, height: int):
        block = self.ledger.get_block_by_height(height)
        return block.to_dict() if block else None
    
    def get_stats(self):
        return {
            "device_id": self.device_id,
            "block_count": self.ledger.get_block_count(),
            "total_emitted": self.ledger.total_emitted_pals / (10 ** 8),
            "total_emitted_pals": self.ledger.total_emitted_pals,
            "pending_transactions": self.mempool.size(),
            "active_nodes": len(self.consensus.get_online_nodes()),
            "peer_count": self.p2p.get_peer_count(),
            "p2p_port": self.p2p_port,
            "reward_address": self.reward_address
        }
