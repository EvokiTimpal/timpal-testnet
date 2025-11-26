import asyncio
import time
import uuid
import hashlib
import aiohttp
from typing import Optional, Dict, Any
from app.block import Block
from app.transaction import Transaction
from app.ledger import Ledger
from app.mempool import Mempool
from app.consensus import Consensus
from app.rewards import RewardCalculator
from app.p2p import P2PNetwork
from app.device_fingerprint import enforce_single_node
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
    
    def get_connected_validators(self) -> set:
        """
        Get validator addresses that are currently online and connected.
        
        TIMPAL PHILOSOPHY: Every node that is online and helping the network
        receives rewards per block, regardless of who proposed the block.
        
        This is used as a FALLBACK for reward distribution when epoch attestations
        are unavailable. We include ALL recently-seen validators via P2P announcements.
        
        Returns:
            set: Addresses of validators seen online recently via P2P
        """
        import time
        connected_validators = set()
        
        # TIMPAL POLICY: All online validators receive rewards
        # Include self (the proposer) - we know we're online
        if self.reward_address and self.ledger.is_validator_registered(self.reward_address):
            connected_validators.add(self.reward_address)
        
        # Include validators seen recently via P2P announce_node messages
        # Validators broadcast their address every 2 seconds; consider online if seen in last 10s
        ONLINE_TIMEOUT = 10  # seconds
        current_time = time.time()
        
        if hasattr(self, 'consensus') and self.consensus and hasattr(self.consensus, 'last_seen'):
            for validator_addr, last_seen_time in self.consensus.last_seen.items():
                if current_time - last_seen_time < ONLINE_TIMEOUT:
                    # Validator was seen recently via P2P - include in rewards
                    if self.ledger.is_validator_registered(validator_addr):
                        connected_validators.add(validator_addr)
        
        return connected_validators
    
    async def _get_peer_height(self, peer_url: str) -> int:
        """
        Get the current blockchain height of a peer via HTTP API.
        
        Args:
            peer_url: HTTP URL of peer (e.g., http://ip:port)
            
        Returns:
            Block height of peer, or None if unavailable
        """
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{peer_url}/stats", timeout=aiohttp.ClientTimeout(total=2)) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('height', data.get('current_height'))
        except Exception:
            pass
        return None
    
    async def handle_new_transaction(self, data: dict, peer_id: str):
        try:
            tx = Transaction.from_dict(data["transaction"])
            
            if not tx.verify():
                print(f"❌ TX REJECT: Signature verification failed for {tx.tx_type} from {tx.sender[:20]}...")
                return
            
            balances = {addr: bal for addr, bal in self.ledger.balances.items()}
            nonces = {addr: nonce for addr, nonce in self.ledger.nonces.items()}
            
            # Heartbeat and validator_registration transactions don't use nonce system
            # They use timestamp-based deduplication instead
            if tx.tx_type not in ("validator_heartbeat", "validator_registration", "epoch_attestation"):
                expected_nonce = max(nonces.get(tx.sender, 0), self.mempool.get_pending_nonce(tx.sender))
                if tx.nonce != expected_nonce:
                    print(f"❌ TX REJECT: Nonce mismatch for {tx.tx_type} from {tx.sender[:20]}... (expected {expected_nonce}, got {tx.nonce})")
                    return
            
            if tx.is_valid(balances):
                added = self.mempool.add_transaction(tx)
                if tx.tx_type == "validator_registration":
                    if added:
                        print(f"✅ VALIDATOR REGISTRATION received and added to mempool from {tx.sender[:20]}...")
                        print(f"   Mempool size: {len(self.mempool.pending_transactions)} transactions")
                    else:
                        print(f"⚠️  VALIDATOR REGISTRATION rejected by mempool (duplicate?) from {tx.sender[:20]}...")
            else:
                print(f"❌ TX REJECT: Validation failed for {tx.tx_type} from {tx.sender[:20]}...")
        except Exception as e:
            print(f"❌ TX ERROR: {str(e)} for transaction from peer {peer_id}")
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
                    # After bootstrap: enforce proposer is registered validator
                    current_validators = self.ledger.get_validator_set()
                    
                    if proposer_address not in current_validators:
                        print(f"❌ REJECT Block {block.height}: Proposer {proposer_address[:20]}... not in validator set")
                        return
                    
                    # SIGNATURE VALIDATION: Always verify proposer signature (prevents forgery)
                    # This is the CRITICAL security check - proposer must prove ownership
                    validator_public_key = self.ledger.get_validator_public_key(proposer_address)
                    if not validator_public_key:
                        print(f"❌ REJECT Block {block.height}: No public key for proposer {proposer_address[:20]}...")
                        return
                    
                    if not block.verify_proposer_signature(validator_public_key):
                        print(f"❌ REJECT Block {block.height}: Invalid proposer signature")
                        return
                    
                    # VRF VALIDATION: Use PARENT block's historical frame for deterministic validation
                    # The proposer for block N is determined by state at block N-1 (parent).
                    # Solution: Use parent's stored liveness set + epoch seed to compute expected proposer.
                    # This is deterministic because all nodes have the same parent block.
                    block_slot = block.slot if hasattr(block, 'slot') and block.slot is not None else block.height
                    parent_height = block.height - 1
                    
                    # Get parent block's historical state for VRF computation
                    from app.historical_state import HistoricalStateBuilder
                    
                    # Get parent block's validator frame (contains epoch_seed + liveness data)
                    # CRITICAL: epoch_seed is stored in ValidatorStateFrame which is NEVER evicted
                    parent_frame = self.ledger.historical_state_log.get_frame(parent_height)
                    
                    # Try to compute expected proposer from parent's historical state
                    expected_proposers = None
                    liveness_set = None
                    epoch_seed = None
                    
                    # Get liveness set AND epoch_seed from parent frame
                    # CRITICAL: epoch_seed is stored in ValidatorStateFrame (never evicted)
                    # This is more reliable than AM snapshot which can be evicted from cache
                    if parent_frame:
                        if parent_frame.liveness_filter_state:
                            liveness_set = parent_frame.liveness_filter_state.combined_liveness_set
                        epoch_seed = parent_frame.epoch_seed
                    
                    if epoch_seed and liveness_set:
                        # Compute deterministic VRF proposer queue from parent state
                        # Pass sorted list for deterministic ordering across all nodes
                        sorted_committee = tuple(sorted(liveness_set))
                        expected_proposers = HistoricalStateBuilder.compute_proposer_queue_for_height(
                            committee=set(sorted_committee),
                            epoch_seed=epoch_seed,
                            block_height=block.height
                        )
                    
                    if expected_proposers:
                        # We have deterministic expected proposers from parent state
                        # Allow all ranked proposers (not just top 3) for full fallback chain
                        if proposer_address not in expected_proposers:
                            expected_list = [p[:20]+'...' for p in expected_proposers[:3]]
                            print(f"❌ REJECT Block {block.height} (slot {block_slot}): Invalid proposer {proposer_address[:20]}...")
                            print(f"   Expected one of: {expected_list}")
                            return
                    else:
                        # No parent historical state - must sync via HTTP first
                        # P2P path requires historical state for deterministic VRF validation
                        # This prevents consensus divergence from non-deterministic liveness filters
                        print(f"⚠️ No historical state for parent block {parent_height} - triggering sync")
                        asyncio.create_task(self._sync_missing_blocks(parent_height, block.height))
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
                # CRITICAL FIX: Only mark as synced if we have peers OR we're the bootstrap node
                # This prevents validator nodes from creating independent chains when disconnected
                # max_peer_height can be 0 in two cases:
                #   1. Bootstrap node with no seeds (GOOD - should create blocks)
                #   2. Validator node with no peer connections (BAD - should wait for peers)
                has_peers = max_peer_height > 0 or peer_count > 0
                can_be_synced = is_bootstrap or has_peers
                
                if not self.synced and can_be_synced:
                    if is_bootstrap:
                        print(f"✅ SYNC STATUS: Bootstrap node at height {local_height} (no peers required)")
                    else:
                        print(f"✅ SYNC STATUS: At network head (height: {local_height}, peers: {peer_count})")
                    self.synced = True
                elif not self.synced and not can_be_synced:
                    # Validator node with no peers - keep waiting
                    if next_height % 30 == 0:
                        print(f"⏸️  WAITING FOR PEERS: Cannot sync without peer connections (height: {local_height})")
                        print(f"   Seed nodes: {self.p2p.seed_nodes}")
                    await asyncio.sleep(1.0)
                    continue
            
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
                    
                    # Debug: Log validator registration inclusions
                    if tx.tx_type == "validator_registration":
                        print(f"📝 Including VALIDATOR_REGISTRATION in block {next_height} from {tx.sender[:20]}...")
                    
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
            rewards, total_reward_pals, block_reward_pals = self.reward_calculator.calculate_reward(
                active_validators, 
                total_fees, 
                self.ledger.total_emitted_pals
            )
            print(f"💰 Rewards calculated: {len(rewards)} recipients, Total reward: {total_reward_pals / 100_000_000:.8f} TMPL")
            
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
                reward=block_reward_pals,  # Only newly minted coins, NOT fees
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
            print(f"   Transactions: {len(valid_txs)}, Reward: {total_reward_pals / 100_000_000:.8f} TMPL")
            
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
                        
                        # SECURITY: Never sync genesis block from network (prevents eclipse attacks)
                        # Genesis must be created locally and validated against CANONICAL_GENESIS_HASH
                        # Network sync ALWAYS starts from block 1
                        
                        # CRITICAL FIX: Create genesis BEFORE syncing if we have no blocks
                        # This ensures Block 1 can be validated (requires Block 0 to exist)
                        if self.ledger.get_block_count() == 0:
                            print(f"🔒 Creating local genesis block before sync...")
                            genesis = Block.create_genesis(config.GENESIS_VALIDATOR)
                            if not self.ledger.add_block(genesis, skip_proposer_check=True):
                                print(f"❌ Failed to create genesis block")
                                return False
                            print(f"✅ Genesis block created locally")
                        
                        current_height = self.ledger.get_block_count() - 1  # Start from current height
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
        Fetch complete blockchain from block 1 to end_height from a peer.
        Used for chain reorganization to get the competing chain.
        
        SECURITY: Never fetches genesis block (height 0) from network.
        Genesis must be created locally and validated against CANONICAL_GENESIS_HASH.
        
        Args:
            peer_url: HTTP URL of the peer
            session: aiohttp ClientSession
            end_height: Last block height to fetch
            
        Returns:
            List[Block] if successful, None if failed
        """
        print(f"📥 Fetching full competing chain from {peer_url} (block 1 to {end_height})...")
        
        CHUNK_SIZE = 100
        all_blocks = []
        
        try:
            # SECURITY: Never sync genesis from network (prevents eclipse attacks)
            current_start = 1  # Start from block 1, skip genesis
            
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
        PRODUCTION-GRADE SEQUENTIAL SYNC: Backfill missing blocks with strict validation.
        
        CRITICAL FIXES (resolves "expected X, got Y" errors):
        1. Track global sync progress - don't restart from original height when switching peers
        2. Query each peer's actual max height before requesting blocks
        3. Strict sequential validation - blocks must be added in exact order
        4. Comprehensive diagnostics for debugging sync failures
        
        Args:
            start_height: First missing block height
            end_height: Target height (adjusted based on peer availability)
        """
        
        # SECURITY: Never sync genesis block from network (prevents eclipse attacks)
        # Instead, create genesis locally BEFORE syncing
        if start_height == 0:
            print(f"⚠️  SECURITY: Creating genesis locally (not from network)")
            print(f"   Genesis validated against CANONICAL_GENESIS_HASH")
            
            # Create genesis locally if it doesn't exist
            if self.ledger.get_block_count() == 0:
                genesis = Block.create_genesis(config.GENESIS_VALIDATOR)
                if not self.ledger.add_block(genesis, skip_proposer_check=True):
                    print(f"❌ Failed to create genesis block")
                    return
                print(f"✅ Genesis block created locally")
            
            start_height = 1  # Now sync from block 1
        
        print(f"\n{'='*60}")
        print(f"🔄 PRODUCTION SYNC INITIATED")
        print(f"{'='*60}")
        print(f"📊 Target Range: blocks {start_height} → {end_height} ({end_height - start_height + 1} blocks)")
        print(f"🔒 Current Chain Height: {len(self.ledger.blocks) - 1}")
        
        # Build list of HTTP endpoints to try
        peer_http_urls = []
        
        # PRIORITY 1: Use explicit HTTP_SEEDS from config (most reliable)
        if hasattr(config, 'HTTP_SEEDS') and config.HTTP_SEEDS:
            peer_http_urls.extend(config.HTTP_SEEDS)
            print(f"📡 Using {len(config.HTTP_SEEDS)} HTTP seed(s) from config")
        
        # PRIORITY 2: Convert WS seed nodes to HTTP (fallback)
        for seed in self.p2p.seed_nodes:
            if seed.startswith('ws://'):
                host_port = seed.replace('ws://', '').replace('/', '')
                if ':' in host_port:
                    host, port_str = host_port.rsplit(':', 1)
                    try:
                        http_port = int(port_str) + 1
                        http_url = f"http://{host}:{http_port}"
                        if http_url not in peer_http_urls:  # Avoid duplicates
                            peer_http_urls.append(http_url)
                    except ValueError:
                        pass
        
        if not peer_http_urls:
            print(f"❌ SYNC FAILED: No peer HTTP endpoints available")
            print(f"   Configure HTTP_SEEDS in config or pass --seed to node")
            return
        
        print(f"📡 Available Peers: {len(peer_http_urls)}")
        
        # CRITICAL: Track global sync progress (persists across peer retries)
        # This prevents re-requesting blocks that were already successfully added
        current_sync_height = start_height
        CHUNK_SIZE = 100  # Server max limit per request
        
        # Try each peer until sync succeeds
        for peer_idx, peer_url in enumerate(peer_http_urls):
            try:
                async with aiohttp.ClientSession() as session:
                    # STEP 1: Query peer's actual blockchain height BEFORE requesting blocks
                    # This prevents "expected X, got Y" errors from requesting non-existent blocks
                    try:
                        async with session.get(
                            f"{peer_url}/api/blockchain/info",
                            timeout=aiohttp.ClientTimeout(total=5)
                        ) as resp:
                            if resp.status == 200:
                                info = await resp.json()
                                peer_height = info.get('height', 0)
                                
                                print(f"\n📡 Peer {peer_idx + 1}/{len(peer_http_urls)}: {peer_url}")
                                print(f"   Peer Height: {peer_height}")
                                print(f"   Sync Progress: {current_sync_height} / {end_height}")
                                
                                # Skip peers that are behind our current sync progress
                                if current_sync_height > peer_height:
                                    print(f"   ⏭️  Peer behind sync progress, trying next peer")
                                    continue
                                
                                # Adjust target to peer's actual height
                                peer_end_height = min(end_height, peer_height)
                                
                                if current_sync_height > peer_end_height:
                                    print(f"   ⏭️  No new blocks available from this peer")
                                    continue
                                    
                                print(f"   🎯 Will sync {current_sync_height} → {peer_end_height} from this peer")
                            else:
                                print(f"\n⚠️  Peer {peer_idx + 1}: Cannot query height (HTTP {resp.status}), trying next peer")
                                continue
                    except Exception as e:
                        print(f"\n⚠️  Peer {peer_idx + 1}: Cannot reach ({e}), trying next peer")
                        continue
                    
                    # STEP 2: Fetch blocks sequentially in chunks
                    peer_success = True
                    chunks_synced = 0
                    
                    while current_sync_height <= peer_end_height:
                        chunk_end = min(current_sync_height + CHUNK_SIZE - 1, peer_end_height)
                        
                        print(f"   📦 Chunk {chunks_synced + 1}: Fetching blocks {current_sync_height}-{chunk_end}")
                        
                        async with session.get(
                            f"{peer_url}/api/blocks/range?start={current_sync_height}&end={chunk_end}",
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status != 200:
                                error_text = await resp.text()
                                print(f"      ❌ HTTP {resp.status}: {error_text}")
                                peer_success = False
                                break
                            
                            data = await resp.json()
                            blocks = data.get('blocks', [])
                            
                            if not blocks:
                                # Check if we already have these blocks
                                current_chain_height = len(self.ledger.blocks) - 1
                                if current_chain_height >= chunk_end:
                                    # We already have all blocks in this range, advance past it
                                    print(f"      ✓ Already have blocks up to {current_chain_height}, advancing")
                                    current_sync_height = current_chain_height + 1
                                    chunks_synced += 1
                                    continue
                                else:
                                    print(f"      ⚠️  Peer returned no blocks for this range")
                                    peer_success = False
                                    break
                            
                            # STRICT SEQUENTIAL VALIDATION: Add blocks in exact order
                            blocks_added_in_chunk = 0
                            for block_dict in blocks:
                                block = Block.from_dict(block_dict)
                                
                                # STRICT: Verify block height matches expected sequence
                                expected_height = len(self.ledger.blocks)
                                if block.height != expected_height:
                                    # This is NOT an error - peer might be on different fork or ahead
                                    # Skip this block and continue (we'll handle forks below)
                                    if block.height < expected_height:
                                        # Block already exists, skip silently
                                        continue
                                    elif block.height > expected_height:
                                        # Gap detected - this should trigger fork detection
                                        print(f"      ⚠️  Gap: expected height {expected_height}, got {block.height}")
                                        peer_success = False
                                        break
                                
                                # Attempt to add block
                                if self.ledger.add_block(block, skip_proposer_check=True):
                                    blocks_added_in_chunk += 1
                                    # Update sync progress tracker
                                    current_sync_height = block.height + 1
                                else:
                                    # FORK DETECTION: Block validation failed
                                    latest = self.ledger.get_latest_block()
                                    
                                    # Check if it's a fork (different previous_hash)
                                    if latest and block.height == latest.height + 1 and block.previous_hash != latest.block_hash:
                                        print(f"\n      🔀 FORK DETECTED at height {block.height}!")
                                        print(f"         Local chain: ...→ {latest.block_hash[:16]}")
                                        print(f"         Peer chain:  ...→ {block.previous_hash[:16]}")
                                        print(f"         Fetching peer's full chain for reorganization...")
                                        
                                        # Fetch full competing chain from this peer
                                        try:
                                            competing_chain = await self._fetch_full_chain(peer_url, session, peer_end_height)
                                            if competing_chain:
                                                # Trigger reorganization - fork choice decides winner
                                                reorg_success, reorg_msg = self.ledger.reorganize_to_chain(competing_chain)
                                                if reorg_success:
                                                    print(f"         ✅ Reorganization successful: {reorg_msg}")
                                                    # Update sync progress to new chain tip
                                                    current_sync_height = len(self.ledger.blocks)
                                                    break  # Exit block loop, continue with next chunk
                                                else:
                                                    print(f"         ⚠️  Reorg rejected: {reorg_msg}")
                                                    print(f"         Local chain is canonical, peer is on wrong fork")
                                                    peer_success = False
                                                    break  # Try next peer
                                            else:
                                                print(f"         ❌ Could not fetch competing chain")
                                                peer_success = False
                                                break  # Try next peer
                                        except Exception as e:
                                            print(f"         ❌ Fork resolution failed: {e}")
                                            peer_success = False
                                            break  # Try next peer
                                    else:
                                        # Block failed validation for non-fork reason (invalid signature, etc.)
                                        print(f"      ❌ Block {block.height} rejected by validation")
                                        print(f"         Peer may have invalid blocks, trying next peer")
                                        peer_success = False
                                        break  # Try next peer
                            
                            if not peer_success:
                                break  # Exit chunk loop
                            
                            # Ensure we advance past this chunk even if all blocks already existed
                            current_chain_height = len(self.ledger.blocks) - 1
                            if current_sync_height <= chunk_end and current_chain_height >= chunk_end:
                                # We already have these blocks, advance past the chunk
                                current_sync_height = chunk_end + 1
                            
                            # Chunk successfully added
                            chunks_synced += 1
                            print(f"      ✅ Added {blocks_added_in_chunk} blocks, now at height {current_chain_height}")
                            
                            # Check if we've reached the target
                            if current_sync_height > peer_end_height:
                                break  # Done with this peer
                    
                    # Check if we successfully synced to target from this peer
                    if peer_success and current_sync_height > end_height:
                        print(f"\n{'='*60}")
                        print(f"✅ SYNC COMPLETE")
                        print(f"{'='*60}")
                        print(f"📊 Synced blocks {start_height} → {end_height}")
                        print(f"🔒 Final Chain Height: {len(self.ledger.blocks) - 1}")
                        print(f"📦 Total Chunks: {chunks_synced}")
                        return
                        
            except Exception as e:
                print(f"\n⚠️  Peer {peer_idx + 1}: Exception during sync: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        # If we got here, ALL peers failed to provide valid blocks
        print(f"\n{'='*60}")
        print(f"❌ SYNC FAILED - All peers exhausted")
        print(f"{'='*60}")
        print(f"📊 Sync Status:")
        print(f"   Requested: {start_height} → {end_height}")
        print(f"   Achieved:  {start_height} → {current_sync_height - 1}")
        print(f"   Gap:       {end_height - current_sync_height + 1} blocks remaining")
        print(f"")
        print(f"🔍 Possible causes:")
        print(f"   1. Network partition (all peers unreachable)")
        print(f"   2. Local chain on wrong fork")
        print(f"   3. Peer chains have invalid blocks")
        print(f"   4. Genesis mismatch (eclipse attack prevention)")
        print(f"")
        print(f"🔧 Recovery options:")
        print(f"   • Wait for network connectivity to improve")
        print(f"   • Check VPS genesis node is running: 143.110.129.211:9000")
        print(f"   • Verify local genesis matches canonical")
        print(f"   • Last resort: Delete blockchain data and resync from genesis")
        print(f"")
        
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
            genesis_block = Block.create_genesis_block(self.genesis_address, self.public_key)
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
        genesis_block = Block.create_genesis_block(self.genesis_address, self.public_key)
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
        
        # CRITICAL FIX: Re-check validator registration AFTER sync completes
        # The initial check (line 85) may have been against pre-initialized local ledger
        # After sync, we have the real blockchain state and need to verify again
        if self.public_key and self.reward_address and self.private_key:
            if not self.ledger.is_validator_registered(self.reward_address):
                # Not registered on the synced chain! Create pending registration
                device_hash = hashlib.sha256(self.device_id.encode()).hexdigest()
                nonce = self.ledger.get_nonce(self.reward_address)
                
                reg_tx = Transaction.create_validator_registration(
                    sender=self.reward_address,
                    public_key=self.public_key,
                    device_id=device_hash,
                    timestamp=time.time(),
                    nonce=nonce
                )
                reg_tx.sign(self.private_key)
                
                # Set pending registration for broadcasting below
                self.pending_validator_registration = reg_tx
                
                print(f"\n🎉 POST-SYNC: Validator registration transaction created!")
                print(f"   Address: {self.reward_address}")
                print(f"   Device: {device_hash[:32]}...")
                print(f"   Will broadcast to network in 2 seconds...")
            else:
                print(f"\n✅ POST-SYNC: Already registered as validator: {self.reward_address}")
                print(f"   Total validators on chain: {self.ledger.get_validator_count()}")
        
        # Broadcast pending validator registration transaction (if any)
        # CRITICAL FIX: Create fresh transaction with new timestamp to avoid duplicate hash
        if self.pending_validator_registration:
            print(f"🔄 Preparing to broadcast validator registration...")
            print(f"   Current P2P connections: {len(self.p2p.peers)}")
            await asyncio.sleep(2)  # Wait for P2P connections to establish
            
            # CRITICAL: Regenerate registration with fresh timestamp to ensure unique tx_hash
            # This prevents duplicate-hash rejection if previous broadcast wasn't mined
            device_hash = hashlib.sha256(self.device_id.encode()).hexdigest()
            nonce = self.ledger.get_nonce(self.reward_address)
            
            print(f"   Regenerating registration with fresh timestamp...")
            print(f"   Address: {self.reward_address[:20]}...")
            print(f"   Nonce: {nonce}")
            
            fresh_reg_tx = Transaction.create_validator_registration(
                sender=self.reward_address,
                public_key=self.public_key,
                device_id=device_hash,
                timestamp=time.time(),  # FRESH timestamp = unique hash
                nonce=nonce
            )
            fresh_reg_tx.sign(self.private_key)
            
            print(f"   TX Hash: {fresh_reg_tx.tx_hash[:32]}...")
            print(f"   Signature verified: {fresh_reg_tx.verify()}")
            
            # Add to our own mempool
            added = self.mempool.add_transaction(fresh_reg_tx)
            print(f"   Added to local mempool: {added}")
            if not added:
                print(f"   ⚠️  Mempool rejected - checking why...")
                print(f"   Current mempool size: {len(self.mempool.pending_transactions)}")
                print(f"   TX already in mempool: {fresh_reg_tx.tx_hash in self.mempool.pending_transactions}")
            
            # Broadcast to network
            print(f"   Broadcasting to {len(self.p2p.peers)} peer(s)...")
            await self.p2p.broadcast("new_transaction", {
                "transaction": fresh_reg_tx.to_dict()
            })
            
            print(f"📡 Validator registration broadcast to network!")
            print(f"   TX Hash: {fresh_reg_tx.tx_hash[:32]}...")
            print(f"   Waiting for inclusion in next block...")
            
            self.pending_validator_registration = None  # Clear after broadcasting
        
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
