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
    def __init__(self, device_id: Optional[str] = None, genesis_address: Optional[str] = None, reward_address: Optional[str] = None, p2p_port: int = 8765, private_key: Optional[str] = None, public_key: Optional[str] = None, skip_device_check: bool = False, data_dir: str = "blockchain_data", use_production_storage: bool = True, testnet_mode: bool = False, is_genesis_node: bool = False):
        if not skip_device_check:
            self.device_fingerprint = enforce_single_node()
            self.device_id = self.device_fingerprint.get_device_id()
        else:
            self.device_fingerprint = None
            self.device_id = device_id or str(uuid.uuid4())
        
        if not reward_address:
            raise ValueError("[FATAL] reward_address is required (must come from wallet).")
        self.reward_address = reward_address
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
        
        # TIMPAL 10-BLOCK REWARD CUTOFF: Set reward_address on P2P for handshake
        # This allows the P2P layer to include reward_address in the initial handshake
        # so the receiving node can track peer-to-validator mapping for liveness detection
        self.p2p.reward_address = self.reward_address
        
        self.is_running = False
        # CRITICAL: Never allow a zero-address fallback (phantom genesis validator)
        if genesis_address:
            self.genesis_address = genesis_address
        elif is_genesis_node:
            self.genesis_address = self.reward_address
        else:
            self.genesis_address = None
        self.p2p_port = p2p_port
        
        # STAGE 3: Sync gating - prevent proposing until fully synced
        self.synced = False
        
        # GENESIS NODE FLAG: Only the genesis node can create the genesis block locally
        # All other nodes MUST sync block 0 from the network
        self.is_genesis_node = is_genesis_node
        
        # BOOTSTRAP MODE DETECTION: Check dynamically during runtime
        # This will be checked in mine_blocks() using actual p2p.seed_nodes
        # (allows run_testnet_node.py to override seed_nodes after Node creation)
        self.SYNC_LAG_THRESHOLD = 5  # Blocks behind peers before re-entering sync mode
        
        # SYNC COOLING PERIOD: Prevent node from proposing blocks until fully synced + cooling period
        # This ensures a node doesn't start participating in consensus until it has:
        # 1. Fully synced 100% of blocks from the network
        # 2. Observed a few more blocks being produced (cooling period)
        # This prevents race conditions where a partially-synced node tries to propose
        self.SYNC_COOLING_BLOCKS = 5  # Number of blocks to wait after sync before becoming active
        self.initial_sync_complete_height = None  # Height when initial sync completed
        self.cooling_complete_height = None  # Height when cooling period ends
        
        # EXPLICIT SYNC PHASE STATE MACHINE (prevents flip-flop bugs)
        # Phases: "SYNCING" -> "COOLING" -> "ACTIVE"
        # - SYNCING: Node is catching up to network head
        # - COOLING: Node is at head but waiting for cooling period
        # - ACTIVE: Node is fully ready to participate in consensus
        # Once ACTIVE, node stays ACTIVE unless it falls significantly behind (>10 blocks)
        self.sync_phase = "SYNCING"  # Start in syncing phase
        self.SEVERE_LAG_THRESHOLD = 10  # Only drop from ACTIVE if this far behind
        
        # VALIDATOR QUORUM REQUIREMENT: Prevent solo block production during network partitions
        # When a node is isolated, it should NOT produce blocks (creates divergent chain history)
        # This prevents the scenario where two isolated nodes each build their own chain,
        # then can't reconcile when reconnected due to finality checkpoints
        self.MIN_VALIDATORS_FOR_CONSENSUS = 2  # Minimum online validators required to propose blocks
        
        # TIMING OPTIMIZATION: Cache peer height to reduce HTTP overhead
        # Only check peer height every N blocks instead of every mining cycle
        self._cached_peer_height = 0
        self._peer_height_check_interval = 5  # Check every 5 blocks
        
        # ISOLATION DETECTION: Track last external block received
        # This is a CRITICAL safety mechanism to prevent private chain growth.
        # Even if P2P connections appear healthy, if we're not receiving blocks from
        # other validators, we are effectively isolated and must NOT produce blocks.
        # 
        # The P2P connection check alone is insufficient because:
        # 1. WebSocket connections can appear alive but be effectively dead
        # 2. peer_validator_addresses may contain stale entries
        # 3. We might be connected to a peer on a different fork
        #
        # This check ensures: "Am I actually receiving blocks from the network?"
        self._last_external_block_height = 0  # Height of last block received from another validator
        self._last_external_block_time = 0.0  # Timestamp when we last received an external block
        self._external_block_timeout = 30.0  # Seconds without external blocks before considering isolated
        self._external_block_lag_threshold = 3  # Max blocks we can be ahead of last external block
        
        # STALL DETECTION: Safety net to escape cooling-based deadlocks
        # If the chain doesn't advance for too long while in SYNCING/COOLING,
        # automatically return to ACTIVE to break the deadlock.
        # This prevents scenarios where both validators demote and neither can propose.
        self._last_block_height_change_time = time.time()  # When chain height last changed
        self._last_known_height = 0  # Last known chain height
        self._stall_threshold = self._external_block_timeout * 3  # 90 seconds by default
        
        # Block gossip: Track recently seen blocks to prevent infinite loops
        self.recently_seen_blocks = set()
        
        self.p2p.register_handler("new_transaction", self.handle_new_transaction)
        self.p2p.register_handler("new_block", self.handle_new_block)
        self.p2p.register_handler("announce_node", self.handle_node_announcement)
        self.p2p.register_sync_handler(self.handle_sync_request)
        self.p2p.register_on_peer_connected(self.handle_peer_connected)
        
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
        
        # TIMPAL 10-BLOCK REWARD CUTOFF: Register P2P callbacks for validator liveness
        # 
        # HOW IT WORKS (deterministic despite using P2P):
        # 1. P2P detects validator disconnect → calls mark_validator_offline(addr, height)
        # 2. This sets offline_since_height in validator_registry (ON-CHAIN state)
        # 3. get_online_validators_deterministic() checks offline_since_height
        # 4. After 10 blocks offline, validator is excluded from rewards
        # 5. When validator reconnects → calls mark_validator_online(addr)
        # 6. This clears offline_since_height, validator resumes receiving rewards
        #
        # WHY THIS IS DETERMINISTIC:
        # - offline_since_height is stored in validator_registry (on-chain)
        # - All nodes see the same offline_since_height for each validator
        # - The 10-block cutoff is calculated from on-chain data only
        # - P2P is only used to UPDATE the on-chain state, not to calculate rewards
        self.p2p.register_validator_liveness_callbacks(
            on_offline=self._on_validator_offline,
            on_online=self._on_validator_online
        )
        print(f"🔗 LIVENESS: Registered P2P callbacks for validator liveness tracking")
    
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
    
    def _on_validator_offline(self, validator_address: str):
        """
        Callback when P2P detects a validator has disconnected.
        
        TIMPAL 10-BLOCK REWARD CUTOFF:
        - Sets offline_since_height in validator_registry
        - After 10 blocks offline, validator is excluded from rewards
        - Proposer rights stop immediately (VRF skips offline validators)
        
        Args:
            validator_address: Address of the validator that disconnected
        """
        current_height = self.ledger.get_block_count()
        print(f"🔴 LIVENESS CALLBACK: _on_validator_offline called for {validator_address[:20]}... at height {current_height}")
        self.ledger.mark_validator_offline(validator_address, current_height)
    
    def _on_validator_online(self, validator_address: str):
        """
        Callback when P2P detects a validator has reconnected.
        
        TIMPAL 10-BLOCK REWARD CUTOFF:
        - Clears offline_since_height in validator_registry
        - If validator returns within 10 blocks, they never lost rewards
        - If they were offline > 10 blocks, rewards resume immediately
        
        Args:
            validator_address: Address of the validator that reconnected
        """
        print(f"🟢 LIVENESS CALLBACK: _on_validator_online called for {validator_address[:20]}...")
        self.ledger.mark_validator_online(validator_address)
    
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
            
            # SYNC DIAGNOSTICS: Track block reception during sync
            current_height = latest.height if latest else 0
            if block.height <= 100 or block.height % 500 == 0:
                print(f"📥 BLOCK RECEIVED: height {block.height} from peer {peer_id[:8]}... (current: {current_height})")
            
            # PERMANENT FIX: Handle height gaps to prevent deadlock
            # If we receive a future block, trigger sync to backfill missing blocks
            if latest and block.height > latest.height + 1:
                gap_size = block.height - latest.height - 1
                if gap_size <= 100:
                    # Small gap - just log and wait for blocks to arrive
                    if block.height % 1000 == 0:  # Only log occasionally
                        print(f"⚠️  Small gap: received block {block.height}, waiting for {latest.height + 1}")
                else:
                    # Large gap - trigger P2P sync request
                    print(f"⚠️  HEIGHT GAP DETECTED: Received block {block.height}, current is {latest.height}")
                    print(f"   Requesting P2P sync for missing {gap_size} blocks...")
                    # Use P2P sync instead of HTTP (HTTP not available through Replit proxy)
                    await self.p2p.broadcast("sync_request", {"current_height": latest.height})
                return
            
            # Skip blocks we already have
            if latest and block.height <= latest.height:
                return
            
            # SYNC FIX: Handle empty chain case (syncing node with no blocks)
            # When latest is None, we need to accept blocks starting from height 0 or 1
            if not latest:
                # Empty chain - accept genesis block (height 0) or first block (height 1) 
                if block.height == 0 or block.height == 1:
                    print(f"📦 SYNC: Accepting first block (height {block.height}) on empty chain")
                    # Validate hash integrity
                    if block.calculate_hash() != block.block_hash:
                        print(f"❌ REJECT: Block {block.height} has invalid hash")
                        return
                    # Add block to ledger (skip proposer check for sync)
                    success = self.ledger.add_block(block, skip_proposer_check=True)
                    if success:
                        print(f"✅ SYNC: Added block {block.height} to ledger")
                    else:
                        print(f"❌ SYNC: Failed to add block {block.height}")
                    return
                else:
                    # Future block on empty chain - queue for later or trigger genesis sync
                    if block.height <= 100:
                        print(f"⚠️  SYNC: Received block {block.height} but chain is empty, need block 0/1 first")
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
                # DYNAMIC FALLBACK: Continue bootstrap if validator_set is empty (prevents deadlock on fresh sync)
                # NOTE: Use get_validator_set() not get_active_validators() - we care about "do I know my
                # validator set yet?" not "are validators currently online by liveness metrics?"
                current_validators = self.ledger.get_validator_set()
                is_bootstrap_block = block.height <= 10 or len(current_validators) == 0
                
                if not is_bootstrap_block:
                    # After bootstrap: enforce proposer is registered validator
                    
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
                
                # ISOLATION DETECTION: Track external blocks received from other validators
                # This is CRITICAL for detecting network isolation - if we're not receiving
                # blocks from other validators, we should NOT produce blocks ourselves.
                # This check is more reliable than just checking P2P connection state.
                if proposer_address != self.reward_address:
                    self._last_external_block_height = block.height
                    self._last_external_block_time = time.time()
                
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
    
    async def handle_peer_connected(self, peer_address: str):
        """
        Called AFTER handshake completes (not on connection).
        
        CRITICAL FIX: Now triggered by P2P layer after announce_node exchange is complete,
        ensuring genesis has already processed our handshake before we send sync_request.
        """
        try:
            current_height = self.ledger.get_block_count()
            if current_height == 0 and not self.is_genesis_node:
                print(f"🔄 POST-HANDSHAKE SYNC: Handshake with {peer_address} complete, requesting blockchain...")
                # Send -1 to indicate "I have no blocks, send me everything including genesis"
                await self.p2p.broadcast("sync_request", {"current_height": -1})
        except Exception as e:
            print(f"⚠️  Error in handle_peer_connected: {e}")
    
    async def handle_sync_request(self, data: dict, peer_id: str, websocket):
        """
        Handle blockchain sync request from a specific peer.
        
        OPTIMIZED FOR RELIABLE SYNC:
        - Smaller batch size (20 blocks) to avoid overwhelming connections
        - Longer delays between blocks to prevent buffer overflow
        - More tolerant of failures (connection may be slow, not dead)
        
        Args:
            data: Sync request data containing current_height
            peer_id: Peer identifier for logging
            websocket: Direct websocket connection for reliable delivery
        """
        try:
            requested_height = data.get("current_height", 0)
            latest = self.ledger.get_latest_block()
            
            print(f"📨 SYNC REQUEST from peer {peer_id[:8]}... (height {requested_height})")
            
            # DIAGNOSTIC: Send immediate acknowledgment so second node knows request was received
            await self.p2p.send_to_websocket(websocket, "sync_ack", {
                "genesis_height": latest.height if latest else 0,
                "requested_height": requested_height,
                "blocks_available": (latest.height - requested_height) if latest else 0
            })
            print(f"📤 SYNC ACK sent to peer {peer_id[:8]}...")
            
            if not latest:
                print(f"⚠️  SYNC RESPONSE: No blocks to send (empty chain)")
                return
            
            if latest.height <= requested_height:
                print(f"ℹ️  SYNC RESPONSE: Peer is up-to-date (latest: {latest.height})")
                return
            
            # Calculate blocks to send
            # CRITICAL FIX: When requested_height is -1 (no blocks) or 0, start from block 0 (genesis)
            # This ensures syncing nodes receive the genesis block first
            if requested_height < 0:
                start_height = 0  # Start from genesis
            else:
                start_height = requested_height + 1  # Start from next block after what peer has
            
            blocks_to_send = latest.height - start_height + 1
            print(f"📤 SYNC RESPONSE: Sending {blocks_to_send} blocks (heights {start_height}-{latest.height}) to peer {peer_id[:8]}...")
            
            # SYNC OPTIMIZATION:
            # - Smaller batches (20 blocks) to avoid overwhelming connections
            # - More tolerant of failures (25 allowed vs 10 before)
            # - Longer delays between blocks
            sent_count = 0
            failed_count = 0
            consecutive_failures = 0
            batch_size = 20  # Reduced from 100 for proxied WebSocket friendliness
            max_consecutive_failures = 5  # Abort only on consecutive failures
            max_total_failures = 25  # More tolerant of intermittent failures
            
            print(f"📦 SYNC STARTING: Will send {blocks_to_send} blocks (batch logging every {batch_size})...")
            
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
                        consecutive_failures = 0  # Reset on success
                    else:
                        failed_count += 1
                        consecutive_failures += 1
                        if failed_count <= 5:  # Only log first few failures
                            print(f"⚠️  SYNC FAILED: Block {height} delivery failed (consecutive: {consecutive_failures})")
                        if failed_count == 5:
                            print(f"⚠️  (Suppressing further failure logs...)")
                        
                        # Abort on consecutive failures (connection likely dead)
                        if consecutive_failures >= max_consecutive_failures:
                            print(f"❌ SYNC ABORTED: {consecutive_failures} consecutive failures, connection likely dead")
                            print(f"   Peer can re-request sync from height {start_height + sent_count - 1}")
                            # CRITICAL FIX: Return immediately to release control back to main loop
                            # This ensures block production resumes even if peers disconnect mid-sync
                            try:
                                await websocket.close()
                            except Exception:
                                pass  # Ignore close errors on dead connection
                            return
                        
                        # Abort on too many total failures
                        if failed_count >= max_total_failures:
                            print(f"❌ SYNC ABORTED: Too many total failures ({failed_count})")
                            # CRITICAL FIX: Return immediately to release control back to main loop
                            try:
                                await websocket.close()
                            except Exception:
                                pass  # Ignore close errors on dead connection
                            return
                        
                        # Wait longer after failure before retrying
                        await asyncio.sleep(0.1)
                    
                    # Progress logging every batch_size blocks
                    if sent_count > 0 and sent_count % batch_size == 0:
                        progress = (sent_count / blocks_to_send) * 100
                        print(f"📦 SYNC PROGRESS: {sent_count}/{blocks_to_send} blocks ({progress:.1f}%)")
                    
                    # KEEPALIVE: Send ping every 50 blocks to prevent connection timeout
                    # More frequent than before (was 500) for proxied connections
                    if sent_count > 0 and sent_count % 50 == 0:
                        try:
                            await websocket.ping()
                        except Exception:
                            pass  # Ignore ping errors, block send will fail if connection is dead
                    
                    # Longer delay to avoid overwhelming proxied connections
                    # 10ms delay = ~100 blocks/second max throughput
                    await asyncio.sleep(0.01)
            
            if sent_count > 0:
                print(f"✅ SYNC COMPLETE: Sent {sent_count}/{blocks_to_send} blocks to peer {peer_id[:8]}... ({failed_count} failed)")
            else:
                print(f"❌ SYNC FAILED: Could not send any blocks to peer {peer_id[:8]}...")
            
        except Exception as e:
            print(f"❌ SYNC ERROR: Exception in handle_sync_request - {e}")
            import traceback
            traceback.print_exc()
    
    def _get_http_urls_from_seeds(self) -> list:
        """
        Convert WebSocket seed URLs to HTTP API URLs.
        
        Supports:
        - ws://host:port -> http://host:port+1
        - wss://host:port -> https://host:port+1
        
        Returns:
            List of HTTP URLs for API access
        """
        http_urls = []
        
        # Add explicit HTTP seeds from config if available
        if hasattr(config, 'HTTP_SEEDS') and config.HTTP_SEEDS:
            http_urls.extend(config.HTTP_SEEDS)
        
        # Convert WebSocket seeds to HTTP
        for seed in self.p2p.seed_nodes:
            try:
                if seed.startswith('wss://'):
                    # Secure WebSocket -> HTTPS
                    # wss://host:port -> https://host:port+1
                    host_port = seed.replace('wss://', '').replace('/', '')
                    if ':' in host_port:
                        host, port_str = host_port.rsplit(':', 1)
                        http_port = int(port_str) + 1
                        http_urls.append(f"https://{host}:{http_port}")
                        
                elif seed.startswith('ws://'):
                    # Plain WebSocket -> HTTP
                    # ws://host:port -> http://host:port+1
                    host_port = seed.replace('ws://', '').replace('/', '')
                    if ':' in host_port:
                        host, port_str = host_port.rsplit(':', 1)
                        http_port = int(port_str) + 1
                        http_urls.append(f"http://{host}:{http_port}")
            except (ValueError, IndexError):
                continue
        
        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in http_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        return unique_urls
    
    async def _get_max_peer_height(self) -> int:
        """
        Query all peers via HTTP API to get maximum height in the network.
        This enables proactive catch-up when node falls behind.
        
        Supports both ws:// and wss:// seed nodes.
        """
        peer_http_urls = self._get_http_urls_from_seeds()
        
        max_height = 0
        for peer_url in peer_http_urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{peer_url}/api/health",
                        timeout=aiohttp.ClientTimeout(total=5)
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
            # ANTI-FORK: Wait for sync to complete before mining
            # This prevents nodes from creating blocks on wrong chain
            if not self.synced and self.ledger.get_block_count() == 0:
                await asyncio.sleep(1.0)
                continue
            
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
            # 
            # CRITICAL FIX: Bootstrap nodes (no seeds) skip catch-up entirely
            # They are the source of truth and should never pause for "higher peers"
            #
            # STATE-AWARE PEER HEIGHT REFRESH:
            # - When SYNCING or COOLING: ALWAYS refresh peer height (we need accurate data to catch up)
            # - When ACTIVE: Only refresh every N blocks (optimization to reduce HTTP overhead)
            #
            # PREVIOUS BUG: Peer height was only refreshed when next_height % interval == 0.
            # If the node was stuck (not producing blocks), next_height stayed constant,
            # so max_peer_height was never refreshed, causing the node to think it was
            # synced when it was actually behind.
            if is_bootstrap:
                max_peer_height = 0  # Bootstrap node is always at head
            elif self.sync_phase != "ACTIVE":
                # ALWAYS refresh when not fully synced - we need accurate peer height to catch up
                max_peer_height = await self._get_max_peer_height()
                self._cached_peer_height = max_peer_height
                if next_height % 10 == 0:
                    print(f"🔄 SYNC REFRESH: local={latest_block.height}, peer={max_peer_height}, phase={self.sync_phase}")
            elif next_height % self._peer_height_check_interval == 0:
                # ACTIVE phase: only refresh every N blocks to reduce HTTP overhead
                max_peer_height = await self._get_max_peer_height()
                self._cached_peer_height = max_peer_height
            else:
                # ACTIVE phase: use cached peer height
                max_peer_height = self._cached_peer_height
            local_height = latest_block.height
            
            # ============================================================
            # EXPLICIT SYNC PHASE STATE MACHINE
            # ============================================================
            # Phases: "SYNCING" -> "COOLING" -> "ACTIVE"
            # - SYNCING: Node is catching up to network head
            # - COOLING: Node is at head but waiting for cooling period
            # - ACTIVE: Node is fully ready to participate in consensus
            #
            # KEY DESIGN: Once ACTIVE, node stays ACTIVE unless it falls
            # SEVERELY behind (>SEVERE_LAG_THRESHOLD blocks). This prevents
            # flip-flopping that caused the fork at block 85.
            # ============================================================
            
            # ============================================================
            # STALL DETECTION: Track chain height changes for deadlock escape
            # ============================================================
            current_time = time.time()
            if local_height != self._last_known_height:
                self._last_block_height_change_time = current_time
                self._last_known_height = local_height
            
            time_since_height_change = current_time - self._last_block_height_change_time
            
            # Bootstrap nodes are always ACTIVE (they are the source of truth)
            if is_bootstrap:
                self.sync_phase = "ACTIVE"
                self.synced = True
            
            # ============================================================
            # STALL ESCAPE: Safety net to break cooling-based deadlocks
            # ============================================================
            # If chain hasn't advanced for too long while in SYNCING/COOLING,
            # automatically return to ACTIVE to break the deadlock.
            # This prevents scenarios where both validators demote and neither can propose.
            if self.sync_phase in ("SYNCING", "COOLING") and not is_bootstrap:
                if time_since_height_change > self._stall_threshold:
                    print(f"🚨 STALL DETECTED: Chain height {local_height} unchanged for {time_since_height_change:.1f}s")
                    print(f"   Stall threshold: {self._stall_threshold}s, current phase: {self.sync_phase}")
                    print(f"   ESCAPING DEADLOCK: Forcing transition to ACTIVE phase")
                    self.sync_phase = "ACTIVE"
                    self.synced = True
                    self.cooling_complete_height = None
                    self._last_block_height_change_time = current_time  # Reset stall timer
            
            # PHASE TRANSITIONS based on current state
            if self.sync_phase == "ACTIVE":
                # ACTIVE -> SYNCING: Only if we fall SEVERELY behind
                # Small lags are normal and shouldn't trigger re-cooling
                if max_peer_height > local_height + self.SEVERE_LAG_THRESHOLD:
                    print(f"⚠️  SEVERE LAG DETECTED: local={local_height}, peer={max_peer_height}")
                    print(f"   Dropping from ACTIVE to SYNCING phase")
                    self.sync_phase = "SYNCING"
                    self.synced = False
                    self.initial_sync_complete_height = None
                    self.cooling_complete_height = None
                # Otherwise stay ACTIVE - small lags are fine
                
            elif self.sync_phase == "COOLING":
                # COOLING -> ACTIVE: When cooling period is complete
                if self.cooling_complete_height is not None and local_height >= self.cooling_complete_height:
                    print(f"✅ COOLING COMPLETE: Now ACTIVE at height {local_height}")
                    self.sync_phase = "ACTIVE"
                # COOLING -> SYNCING: If we fall behind during cooling
                elif max_peer_height > local_height + self.SYNC_LAG_THRESHOLD:
                    print(f"⚠️  FELL BEHIND DURING COOLING: local={local_height}, peer={max_peer_height}")
                    print(f"   Returning to SYNCING phase")
                    self.sync_phase = "SYNCING"
                    self.synced = False
                    self.initial_sync_complete_height = None
                    self.cooling_complete_height = None
                    
            else:  # SYNCING phase
                # SYNCING -> COOLING: When we catch up to network head
                has_peers = max_peer_height > 0 or peer_count > 0
                can_transition = is_bootstrap or has_peers
                
                if can_transition and max_peer_height <= local_height + self.SYNC_LAG_THRESHOLD:
                    if not is_bootstrap:
                        # Non-bootstrap: transition to COOLING
                        print(f"✅ SYNC COMPLETE at height {local_height}")
                        self.sync_phase = "COOLING"
                        self.synced = True
                        self.initial_sync_complete_height = local_height
                        self.cooling_complete_height = local_height + self.SYNC_COOLING_BLOCKS
                        print(f"🧊 COOLING PERIOD: Will become ACTIVE at height {self.cooling_complete_height}")
                        print(f"   Waiting for {self.SYNC_COOLING_BLOCKS} more blocks before proposing...")
                    else:
                        # Bootstrap: skip cooling, go directly to ACTIVE
                        self.sync_phase = "ACTIVE"
                        self.synced = True
            
            # Handle catch-up sync if behind
            if max_peer_height > local_height:
                if self.sync_phase != "ACTIVE":
                    print(f"🔄 CATCH-UP MODE: Local height {local_height}, max peer height {max_peer_height}")
                    print(f"   Syncing missing blocks {local_height + 1} to {max_peer_height}...")
                await self._sync_missing_blocks(local_height + 1, max_peer_height)
                # After catch-up, skip this mining cycle to refresh state
                continue
            
            # Handle no peers case for non-bootstrap nodes
            if self.sync_phase == "SYNCING" and not is_bootstrap:
                has_peers = max_peer_height > 0 or peer_count > 0
                if not has_peers:
                    if next_height % 30 == 0:
                        print(f"⏸️  WAITING FOR PEERS: Cannot sync without peer connections (height: {local_height})")
                        print(f"   Seed nodes: {self.p2p.seed_nodes}")
                    await asyncio.sleep(1.0)
                    continue
            
            # CONSENSUS GATE: Only ACTIVE nodes can propose blocks
            consensus_ready = (self.sync_phase == "ACTIVE")
            
            if not consensus_ready:
                if next_height % 10 == 0:  # Log every 10 blocks to track progress
                    cooling_remaining = (self.cooling_complete_height - local_height) if self.cooling_complete_height else "N/A"
                    print(f"⏸️  NOT CONSENSUS-READY: phase={self.sync_phase}, local={local_height}, "
                          f"max_peer={max_peer_height}, cooling_until={self.cooling_complete_height}, "
                          f"blocks_remaining={cooling_remaining}")
                await asyncio.sleep(config.BLOCK_TIME)
                continue
            
            # ============================================================
            # P2P ISOLATION CHECK: Prevent solo block production
            # ============================================================
            # CRITICAL FIX: Use REAL-TIME P2P connectivity, not on-chain historical data.
            # 
            # Previous bug: get_online_validators_deterministic() uses on-chain data
            # (recent proposers, attestations) which doesn't reflect current connectivity.
            # When isolated, a node could still see 2 validators as "online" based on
            # past block history, so it kept producing blocks → FORK.
            #
            # New approach: Check actual P2P connections to other validators.
            # - peer_validator_addresses: Maps peer_id -> validator_address for live connections
            # - If we have 0 validator peers, we are ISOLATED and must stop producing
            #
            # Bootstrap nodes are exempt: they are the "source of truth" and must be able
            # to produce blocks even when alone (to bootstrap the network).
            # ============================================================
            
            # Store quorum metrics for reuse (both in isolation check AND after successful proposal)
            # This ensures consistent logic throughout the mining loop
            has_p2p_quorum = False
            connected_validator_peers = 0
            effective_online = 1  # At minimum, we count ourselves
            required_online = 1
            total_validators = 0
            
            if not is_bootstrap:
                # Get total validators from checkpoint
                total_validators = len(checkpoint_validators) if checkpoint_validators else 0
                
                # Count validators we're actually connected to via P2P RIGHT NOW
                connected_validator_peers = len(self.p2p.peer_validator_addresses)
                
                # Include self in the count (we know we're online)
                effective_online = connected_validator_peers + 1
                
                # Require at least MIN_VALIDATORS_FOR_CONSENSUS validators
                required_online = min(self.MIN_VALIDATORS_FOR_CONSENSUS, total_validators)
                
                # Determine if we have P2P quorum
                has_p2p_quorum = (effective_online >= required_online) and (connected_validator_peers > 0)
                
                # Compute external block metrics for isolation detection
                current_time = time.time()
                time_since_external_block = current_time - self._last_external_block_time
                blocks_ahead_of_external = next_height - self._last_external_block_height
                
                # PERIODIC DEBUG LOGGING: Log isolation metrics every 50 blocks
                # This helps diagnose issues without flooding logs
                if next_height % 50 == 0:
                    print(f"[ISOLATION DEBUG] h={local_height}, next={next_height}, "
                          f"last_ext={self._last_external_block_height}, "
                          f"ext_age={time_since_external_block:.1f}s, "
                          f"ahead={blocks_ahead_of_external}, "
                          f"peers={connected_validator_peers}, "
                          f"eff={effective_online}/{required_online}, "
                          f"quorum={has_p2p_quorum}")
                
                if total_validators >= 2:
                    if effective_online < required_online:
                        # We are ISOLATED - demote to SYNCING and stop producing
                        if self.sync_phase == "ACTIVE":
                            print(f"🚨 ISOLATION DETECTED: Only {effective_online} validator(s) reachable "
                                  f"(need {required_online}), demoting from ACTIVE to SYNCING")
                            self.sync_phase = "SYNCING"
                            self.cooling_complete_height = None
                        
                        if next_height % 10 == 0:
                            print(f"⏸️  P2P QUORUM NOT MET: connected_validators={connected_validator_peers}, "
                                  f"effective_online={effective_online}, required={required_online}, "
                                  f"total_validators={total_validators}")
                            print(f"   Waiting for P2P connections to other validators before proposing...")
                            print(f"   peer_validator_addresses: {list(self.p2p.peer_validator_addresses.values())[:3]}")
                        await asyncio.sleep(config.BLOCK_TIME)
                        continue
                
                # ============================================================
                # EXTERNAL BLOCK RECEPTION CHECK: Prevent private chain growth
                # ============================================================
                # CRITICAL FIX v4 (Dec 2025): Only demote when ACTUALLY AHEAD of peers.
                # 
                # PREVIOUS BUG: Both validators would demote to SYNCING when they
                # hadn't received external blocks, even when at the same height.
                # This caused a permanent deadlock where neither could propose.
                #
                # NEW RULE: External block timeout should ONLY trigger demotion when:
                #   local_height > max_peer_height
                # Meaning the node is actually ahead (potential private chain).
                #
                # If heights are equal or peers are ahead, do NOT demote.
                # ============================================================
                
                # Check 1: Time-based isolation (haven't received external block in N seconds)
                # Only apply after we've received at least one external block
                # CRITICAL FIX: Only demote if we're actually AHEAD of peers
                if self._last_external_block_time > 0 and time_since_external_block > self._external_block_timeout:
                    # Only demote if we're actually ahead of peers (potential private chain)
                    if local_height > max_peer_height:
                        if self.sync_phase == "ACTIVE":
                            print(f"🚨 EXTERNAL BLOCK TIMEOUT: No blocks from other validators in {time_since_external_block:.1f}s "
                                  f"(threshold: {self._external_block_timeout}s)")
                            print(f"   Last external block: height {self._last_external_block_height}, "
                                  f"local_height: {local_height}, max_peer_height: {max_peer_height}")
                            print(f"   We are AHEAD of peers - demoting from ACTIVE to SYNCING to prevent private chain growth")
                            self.sync_phase = "SYNCING"
                            self.cooling_complete_height = None
                        
                        if next_height % 10 == 0:
                            print(f"⏸️  EXTERNAL BLOCK TIMEOUT: {time_since_external_block:.1f}s since last external block (ahead of peers)")
                        await asyncio.sleep(config.BLOCK_TIME)
                        continue
                    else:
                        # Not ahead of peers - don't demote, just log occasionally
                        if next_height % 30 == 0:
                            print(f"ℹ️  EXTERNAL BLOCK TIMEOUT but NOT ahead of peers: local={local_height}, peer={max_peer_height}")
                            print(f"   Not demoting - waiting for network to produce blocks")
                
                # Check 2: Combined height + time isolation check
                # Only fire when BOTH: significantly ahead AND haven't heard from network recently
                # CRITICAL FIX: Also require being ahead of max_peer_height
                height_threshold = 3  # Must be > 3 blocks ahead of last external
                time_threshold = 2 * config.BLOCK_TIME  # Must be > 2 block times since last external
                
                if (self._last_external_block_height > 0 and 
                    blocks_ahead_of_external > height_threshold and 
                    time_since_external_block > time_threshold and
                    local_height > max_peer_height):  # CRITICAL: Must be ahead of peers
                    if self.sync_phase == "ACTIVE":
                        print(f"🚨 EXTERNAL BLOCK LAG: {blocks_ahead_of_external} blocks ahead of last external "
                              f"AND {time_since_external_block:.1f}s since last external block")
                        print(f"   Last external: height {self._last_external_block_height}, local: {local_height}, peer: {max_peer_height}")
                        print(f"   We are AHEAD of peers - demoting from ACTIVE to SYNCING to prevent private chain growth")
                        self.sync_phase = "SYNCING"
                        self.cooling_complete_height = None
                    
                    if next_height % 10 == 0:
                        print(f"⏸️  EXTERNAL BLOCK LAG: ahead={blocks_ahead_of_external}, age={time_since_external_block:.1f}s")
                    await asyncio.sleep(config.BLOCK_TIME)
                    continue
                
                # ============================================================
                # CRITICAL SAFETY: No consecutive self-proposals when AHEAD of peers
                # ============================================================
                # This guards against private chain growth when a node is isolated.
                # A non-bootstrap validator CANNOT build on top of a block it proposed
                # IF it is ahead of its peers (potential private chain scenario).
                # 
                # CRITICAL FIX (Dec 2025): Only apply this rule when actually ahead of peers.
                # Previously, this rule was unconditional, which caused:
                # - Empty slots when VRF assigned same validator as primary consecutively
                # - Fallback to rank 1 proposers, adding delays
                # - ~6 second average block time instead of 3 seconds
                #
                # NEW RULE: Only block consecutive self-proposals when local_height > max_peer_height
                # This preserves safety (prevents private chains) while allowing normal
                # consecutive proposals when the network is healthy and synchronized.
                # ============================================================
                if latest_block.proposer == self.reward_address and local_height > max_peer_height:
                    if next_height % 10 == 0:
                        print(f"⏸️  CONSECUTIVE SELF-PROPOSAL BLOCKED: Last block {latest_block.height} was proposed by us")
                        print(f"   We are AHEAD of peers (local={local_height}, peer={max_peer_height})")
                        print(f"   Blocking consecutive proposal to prevent private chain growth")
                    await asyncio.sleep(config.BLOCK_TIME)
                    continue
            
            # TIME-SLICED WINDOWS (TSW): Deterministic proposer fallback without forks.
            #
            # CRITICAL FIX: Anchor windows to the *parent block timestamp*, not wall-clock
            # time derived from genesis. This keeps all nodes aligned even if blocks are
            # occasionally delayed by network jitter.
            from time_slots import am_i_proposer_now_relative, time_until_my_window_relative

            # Slot should be deterministic and derived from chain progression.
            # Use one slot per height (slot == height) to avoid clock-skew forks.
            current_slot = next_height

            # Get ranked proposers for this slot (primary, fallback1, fallback2)
            ranked_proposers = self.ledger.get_ranked_proposers_for_slot(current_slot, num_ranks=3)
            
            if not ranked_proposers:
                # No validators available - wait for network to stabilize
                if next_height % 10 == 0:
                    print(f"⚠️  No active validators at height {next_height}, waiting...")
                continue
            
            # SINGLE-VALIDATOR FAST PATH: Skip window logic when alone
            # When there's only one validator, there's no need to coordinate windows
            # This prevents unnecessary slot skipping and timing delays
            is_single_validator = (len(ranked_proposers) == 1 and ranked_proposers[0] == self.reward_address)
            
            if is_single_validator:
                # Single validator mode: always my turn, no window coordination needed
                my_rank = 0
                is_my_turn = True
            else:
                # Multi-validator mode: use chain-anchored time-sliced windows.
                is_my_turn, my_rank = am_i_proposer_now_relative(
                    self.reward_address,
                    ranked_proposers,
                    parent_timestamp=latest_block.timestamp,
                )

                if my_rank < 0:
                    # Not a proposer for this slot.
                    await asyncio.sleep(0.1)
                    continue
                
                if not is_my_turn:
                    # Not my window yet - check when my window opens
                    wait_time = time_until_my_window_relative(my_rank, latest_block.timestamp)
                    
                    if wait_time > 0 and wait_time < 1.5:
                        # My window is upcoming - wait for it to open
                        # TIMING OPTIMIZATION: Reduced buffer from 0.1s to 0.05s and cap from 0.5s to 0.4s
                        # This shaves ~50-100ms per block without risking out-of-window rejections
                        print(f"⏰ Rank {my_rank} proposer waiting {wait_time:.2f}s for window")
                        await asyncio.sleep(min(wait_time + 0.05, 0.4))  # Wait with small buffer
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
            
            # TIMPAL PHILOSOPHY: ALL ONLINE VALIDATORS RECEIVE EQUAL BLOCK REWARDS
            # 
            # DETERMINISTIC LIVENESS: Uses ONLY on-chain data (no P2P state):
            # 1. Recent block proposers (last N blocks)
            # 2. Newly registered validators (grace period)
            # 3. Attestation holders (epoch-based liveness proof)
            #
            # This ensures ALL NODES compute the SAME set of online validators
            # → same reward_allocations → same block hash → NO FORKS
            active_validators = self.ledger.get_online_validators_deterministic(next_height)
            
            # SAFETY: If no validators detected (bootstrap), credit proposer to avoid lost coins
            if not active_validators:
                print(f"🔧 BOOTSTRAP: No on-chain liveness yet, crediting proposer")
                active_validators = [self.reward_address]
            
            print(f"💰 Equal rewards to {len(active_validators)} online validators (deterministic)")
            rewards, total_reward_pals, block_reward_pals = self.reward_calculator.calculate_reward(
                active_validators, 
                total_fees, 
                self.ledger.total_emitted_pals
            )
            per_validator = total_reward_pals // len(active_validators) if active_validators else 0
            print(f"💰 Block reward: {total_reward_pals / 100_000_000:.8f} TMPL ({per_validator / 100_000_000:.8f} each)")
            
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
                # using CHAIN-ANCHORED windows relative to the parent block.
                from time_slots import relative_window_bounds
                window_start, window_end = relative_window_bounds(latest_block.timestamp, my_rank)
                
                # Small epsilon to avoid boundary precision issues (ChatGPT Fix F: increased to 50ms)
                EPS = 0.050
                
                # Pick a timestamp that:
                # (a) is not before scheduled_time (monotonic chain requirement)
                # (b) lies inside [window_start, window_end) (time-sliced window requirement)
                # (c) never goes into the future (clock skew safety)
                now = time.time()
                
                # BOOTSTRAP EXCEPTION: First 10 blocks use current time (window validation relaxed)
                BOOTSTRAP_HEIGHT = 10
                if next_height <= BOOTSTRAP_HEIGHT:
                    block_timestamp = now
                    print(f"🔧 BOOTSTRAP: Using current time for block {next_height} (grace period)")
                else:
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
            
            # NOTE: We intentionally do NOT update _last_external_block_height here.
            # 
            # CRITICAL BUG FIX: Previously, we updated _last_external_block_height after
            # successful proposals "with P2P quorum". This was WRONG because:
            # 1. P2P quorum check can be fooled by stale peer_validator_addresses entries
            # 2. Once we start updating our own "external" tracker with our own blocks,
            #    the isolation checks (timeout, height lag) become useless
            # 3. This allowed a node to build 78+ blocks on a private chain while isolated
            #
            # _last_external_block_height should ONLY be updated when receiving blocks
            # from OTHER validators (in handle_new_block and HTTP sync), never for our
            # own proposals. This ensures the isolation checks remain meaningful.
            
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
                        
                        # CRITICAL: Only genesis node creates genesis locally
                        # All other nodes MUST sync block 0 from network to ensure same chain
                        if self.ledger.get_block_count() == 0:
                            if self.is_genesis_node:
                                # Genesis node: create block 0 locally
                                print(f"🔥 GENESIS NODE: Creating genesis block locally...")
                                genesis = Block.create_genesis(config.GENESIS_VALIDATOR)
                                if not self.ledger.add_block(genesis, skip_proposer_check=True):
                                    print(f"❌ Failed to create genesis block")
                                    return False
                                print(f"✅ Genesis block created locally (hash: {genesis.block_hash[:16]}...)")
                            else:
                                # Non-genesis node: sync block 0 from network
                                print(f"📥 NON-GENESIS: Syncing block 0 from network...")
                                try:
                                    # Use the correct API endpoint: /api/blocks/range
                                    async with session.get(
                                        f"{peer_url}/api/blocks/range?start=0&end=0",
                                        timeout=aiohttp.ClientTimeout(total=10)
                                    ) as block_resp:
                                        if block_resp.status == 200:
                                            data = await block_resp.json()
                                            blocks = data.get('blocks', [])
                                            if not blocks:
                                                print(f"⚠️  No genesis block in response from {peer_url}")
                                                continue
                                            
                                            genesis = Block.from_dict(blocks[0])
                                            
                                            # Validate against canonical hash
                                            if hasattr(config, 'CANONICAL_GENESIS_HASH') and config.CANONICAL_GENESIS_HASH:
                                                if genesis.block_hash != config.CANONICAL_GENESIS_HASH:
                                                    print(f"❌ SECURITY: Peer genesis {genesis.block_hash[:16]}... doesn't match canonical {config.CANONICAL_GENESIS_HASH[:16]}...")
                                                    continue  # Try next peer
                                                print(f"✅ Genesis validated against CANONICAL_GENESIS_HASH")
                                            
                                            if not self.ledger.add_block(genesis, skip_proposer_check=True):
                                                print(f"❌ Failed to add genesis block from network")
                                                return False
                                            print(f"✅ Genesis block synced from network (hash: {genesis.block_hash[:16]}...)")
                                        else:
                                            print(f"⚠️  Failed to fetch block 0 from {peer_url} (status {block_resp.status})")
                                            continue
                                except Exception as e:
                                    print(f"⚠️  Error fetching genesis: {e}")
                                    continue
                        
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
                                        
                                        # ISOLATION DETECTION: Track external blocks received during HTTP sync
                                        # Blocks from HTTP sync are always from other validators (we're syncing)
                                        proposer = block.proposer if hasattr(block, 'proposer') else None
                                        if proposer and proposer != self.reward_address:
                                            self._last_external_block_height = block.height
                                            self._last_external_block_time = time.time()
                                        
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
        
        CRITICAL FIX: Prepends local genesis to the fetched chain so that
        chain indices match block heights. This is required for _find_fork_point
        to work correctly in reorganize_to_chain.
        
        Args:
            peer_url: HTTP URL of the peer
            session: aiohttp ClientSession
            end_height: Last block height to fetch
            
        Returns:
            List[Block] if successful (starting from genesis), None if failed
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
            
            # CRITICAL FIX: Validate that peer's block 1 chains to our genesis
            # This ensures we're on the same network before attempting reorg
            local_genesis = self.ledger.get_block_by_height(0)
            if not local_genesis:
                print(f"❌ Cannot validate competing chain: no local genesis block")
                return None
            
            if all_blocks and all_blocks[0].height == 1:
                peer_block_1 = all_blocks[0]
                if peer_block_1.previous_hash != local_genesis.block_hash:
                    print(f"❌ GENESIS MISMATCH DETECTED!")
                    print(f"   Local genesis hash:  {local_genesis.block_hash[:16]}...")
                    print(f"   Peer block 1 prev:   {peer_block_1.previous_hash[:16]}...")
                    print(f"   Peer is on a DIFFERENT CHAIN - cannot reorganize")
                    print(f"   This usually means the peer has stale/old chain data")
                    print(f"   Solution: Delete chain data on the mismatched node and resync")
                    return None
                
                # CRITICAL FIX: Prepend local genesis to make chain indices match heights
                # This is required for _find_fork_point to work correctly
                # chain[0] must be height 0, chain[1] must be height 1, etc.
                print(f"✅ Genesis validated - prepending local genesis to competing chain")
                all_blocks.insert(0, local_genesis)
            
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
        
        # CRITICAL: Handle genesis block based on node type
        if start_height == 0:
            if self.ledger.get_block_count() == 0:
                if self.is_genesis_node:
                    # Genesis node: create block 0 locally
                    print(f"🔥 GENESIS NODE: Creating genesis block locally...")
                    genesis = Block.create_genesis(config.GENESIS_VALIDATOR)
                    if not self.ledger.add_block(genesis, skip_proposer_check=True):
                        print(f"❌ Failed to create genesis block")
                        return
                    print(f"✅ Genesis block created locally (hash: {genesis.block_hash[:16]}...)")
                else:
                    # Non-genesis node: sync block 0 from network (handled below with other blocks)
                    print(f"📥 NON-GENESIS: Will sync block 0 from network...")
                    # Don't skip block 0 - include it in the sync range
            else:
                # Genesis already exists, start from block 1
                start_height = 1
        
        print(f"\n{'='*60}")
        print(f"🔄 PRODUCTION SYNC INITIATED")
        print(f"{'='*60}")
        print(f"📊 Target Range: blocks {start_height} → {end_height} ({end_height - start_height + 1} blocks)")
        print(f"🔒 Current Chain Height: {len(self.ledger.blocks) - 1}")
        
        # Build list of HTTP endpoints to try (supports ws://, wss://, and explicit HTTP_SEEDS)
        peer_http_urls = self._get_http_urls_from_seeds()
        
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
                                    
                                    # ISOLATION DETECTION: Track external blocks received during sync
                                    # Blocks from sync are always from other validators
                                    proposer = block.proposer if hasattr(block, 'proposer') else None
                                    if proposer and proposer != self.reward_address:
                                        self._last_external_block_height = block.height
                                        self._last_external_block_time = time.time()
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
        print(f"   • Check seed node is running and reachable")
        print(f"   • Verify local genesis matches canonical")
        print(f"   • Last resort: Delete blockchain data and resync from genesis")
        print(f"")
        
        # Do NOT auto-delete - too dangerous (could be temporary network issue)
    
    async def bootstrap_or_sync(self):
        """
        Robust blockchain sync with HTTP-first strategy.
        
        SYNC STRATEGY:
        1. Try HTTP batch sync first (most reliable for historical blocks)
        2. Fall back to P2P WebSocket sync if HTTP fails
        3. Use P2P only for live head blocks after initial sync
        
        CRITICAL ANTI-FORK RULE:
        - Only nodes started with --genesis flag can create block 0
        - All other nodes MUST sync from the network
        - If sync fails but peers exist, wait and retry - NEVER fork
        """
        print("🔄 Starting blockchain sync...")
        
        # Check if we already have blocks (node restart scenario)
        # IMPORTANT: Do NOT set self.synced = True here - let mine_blocks verify against peers
        # This ensures the node goes through proper sync verification and cooling period
        # even on restart, preventing a stale node from immediately proposing blocks
        if self.ledger.get_block_count() > 0:
            print(f"✅ Already have {self.ledger.get_block_count()} blocks on disk")
            print(f"   Will verify sync status against peers in mining loop")
            # Do NOT set self.synced = True - let mine_blocks + peer height decide
            return
        
        # Wait a moment for P2P connections to establish
        await asyncio.sleep(2)
        
        # Check if we have any peers
        peer_count = self.p2p.get_peer_count()
        
        # GENESIS NODE: Only --genesis flag allows local genesis creation
        if self.is_genesis_node:
            if peer_count == 0:
                print("🔥 GENESIS MODE: Creating genesis block (--genesis flag)")
                genesis_block = Block.create_genesis_block(self.genesis_address, self.public_key)
                self.ledger.add_block(genesis_block)
                self.synced = True
                return
            else:
                print("⚠️  GENESIS MODE but peers exist - syncing from network instead")
        
        # NETWORK NODE: Must sync from peers
        if peer_count == 0:
            print("❌ ERROR: No peers connected and not a genesis node!")
            print("   Cannot create genesis block without --genesis flag")
            print("   Please check seed node is running and try again")
            # Do NOT create genesis - wait for peers
            return
        
        print(f"📡 Connected to {peer_count} peer(s), starting sync...")
        
        # STRATEGY 1: HTTP batch sync (preferred for historical blocks)
        # More reliable for syncing large amounts of historical data
        http_urls = self._get_http_urls_from_seeds()
        if http_urls:
            print(f"🌐 HTTP SYNC: Trying HTTP batch sync from {len(http_urls)} endpoint(s)...")
            for url in http_urls:
                print(f"   - {url}")
            
            http_success = await self.http_batch_sync(http_urls)
            if http_success:
                block_count = self.ledger.get_block_count()
                print(f"✅ HTTP sync successful! Synced {block_count} blocks")
                # Do NOT set self.synced = True here - let mine_blocks verify against peers
                # and enforce cooling period before allowing block production
                print(f"   Will verify sync status and start cooling period in mining loop")
                return
            else:
                print(f"⚠️  HTTP sync failed, falling back to P2P WebSocket sync...")
        else:
            print(f"⚠️  No HTTP endpoints available, using P2P WebSocket sync...")
        
        # STRATEGY 2: P2P WebSocket sync (fallback)
        # Less reliable over proxied connections but works for direct connections
        print(f"📡 P2P SYNC: Requesting blockchain via WebSocket...")
        
        # Request sync via P2P WebSocket
        # Send -1 to indicate "I have no blocks, send me everything including genesis"
        await self.p2p.broadcast("sync_request", {"current_height": -1})
        
        # Wait for blocks to arrive via P2P
        max_wait_seconds = 60  # Increased timeout for proxied connections
        check_interval = 2
        waited = 0
        last_block_count = 0
        stall_count = 0
        
        print(f"⏳ Waiting for blocks from peers (up to {max_wait_seconds}s)...")
        
        while waited < max_wait_seconds:
            await asyncio.sleep(check_interval)
            waited += check_interval
            
            block_count = self.ledger.get_block_count()
            
            # Check if we're making progress
            if block_count > last_block_count:
                print(f"📦 P2P SYNC PROGRESS: Received {block_count} blocks...")
                last_block_count = block_count
                stall_count = 0
            else:
                stall_count += 1
            
            # If we have blocks and sync has stalled, we might be done
            if block_count > 0 and stall_count >= 3:
                print(f"✅ P2P sync complete! Received {block_count} blocks (sync stalled, assuming complete)")
                # Do NOT set self.synced = True here - let mine_blocks verify against peers
                # and enforce cooling period before allowing block production
                print(f"   Will verify sync status and start cooling period in mining loop")
                return
            
            # Re-broadcast sync request every 15 seconds if no progress
            if waited % 15 == 0 and waited > 0 and stall_count > 0:
                print(f"🔄 Re-requesting sync (waited {waited}s, have {block_count} blocks, stalled {stall_count}x)...")
                current_height = block_count - 1 if block_count > 0 else -1
                await self.p2p.broadcast("sync_request", {"current_height": current_height})
        
        # Check final state
        final_block_count = self.ledger.get_block_count()
        if final_block_count > 0:
            print(f"✅ P2P sync finished with {final_block_count} blocks (timeout reached)")
            # Do NOT set self.synced = True here - let mine_blocks verify against peers
            # and enforce cooling period before allowing block production
            print(f"   Will verify sync status and start cooling period in mining loop")
        else:
            print(f"⚠️  Sync timed out after {max_wait_seconds}s with no blocks")
            print("   Blocks may still arrive - node will continue running")
            print("   If no blocks arrive, check seed node connectivity")
    
    
    async def _broadcast_validator_registration(self):
        """
        Broadcast validator registration transaction to the network.
        
        CRITICAL FIX: This runs as a concurrent task alongside P2P server startup.
        This allows the P2P server to start and establish connections while we wait.
        
        Uses get_peer_count() which includes BOTH inbound and outbound peers,
        since outbound connections (to seeds) are what matter for new validators.
        """
        print(f"🔄 Preparing to broadcast validator registration...")
        
        # Wait for P2P connections (either inbound or outbound)
        # CRITICAL FIX: Use get_peer_count() which counts BOTH inbound and outbound peers
        # The previous code only checked self.p2p.peers (inbound), but outbound connections
        # to seeds are stored in self.p2p.outbound_peers
        max_wait_for_peers = 30  # seconds
        wait_interval = 2
        waited = 0
        while self.p2p.get_peer_count() == 0 and waited < max_wait_for_peers:
            print(f"   Waiting for P2P connections... ({waited}s/{max_wait_for_peers}s)")
            await asyncio.sleep(wait_interval)
            waited += wait_interval
        
        peer_count = self.p2p.get_peer_count()
        print(f"   P2P connections ready: {peer_count} peer(s) (inbound: {len(self.p2p.peers)}, outbound: {len(self.p2p.outbound_peers)})")
        
        if peer_count == 0:
            print(f"   ⚠️  No peers connected after {max_wait_for_peers}s - will broadcast anyway")
            print(f"   Registration may need to be retried manually")
        
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
        print(f"   Broadcasting to {peer_count} peer(s)...")
        await self.p2p.broadcast("new_transaction", {
            "transaction": fresh_reg_tx.to_dict()
        })
        
        print(f"📡 Validator registration broadcast to network!")
        print(f"   TX Hash: {fresh_reg_tx.tx_hash[:32]}...")
        print(f"   Waiting for inclusion in next block...")
        
        self.pending_validator_registration = None  # Clear after broadcasting

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
        
        # Start registration broadcast as a concurrent task so it runs alongside P2P server
        # This allows the P2P server to start and accept connections while we wait for peers
        registration_task = None
        if self.pending_validator_registration:
            registration_task = asyncio.create_task(self._broadcast_validator_registration())
        
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
