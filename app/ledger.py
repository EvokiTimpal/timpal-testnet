import json
import os
import tempfile
import time
from typing import Dict, List, Optional, Union, Tuple, Set
from app.block import Block
from app.transaction import Transaction
from app.fork_choice import ForkChoice
from app.validator_economics import ValidatorEconomics
from app.attestation import AttestationManager
from app.vrf import VRFManager
from app.historical_state import (
    HistoricalStateLog,
    HistoricalStateBuilder,
    ValidatorStateFrame,
    EpochSnapshot,
    HistoricalStateRecord
)
from app.sqlite_historical_storage import SQLiteHistoricalStorage
import config


class Ledger:
    def __init__(self, data_dir: str = "blockchain_data", use_production_storage: bool = False, read_only: bool = False):
        self._closed = False
        self.data_dir = data_dir
        self.use_production_storage = use_production_storage
        self.read_only = read_only  # Suppress reward warnings for read-only contexts (e.g., block explorer)
        self.production_storage = None
        self.crash_recovery = None
        
        self.balances: Dict[str, int] = {}
        self.nonces: Dict[str, int] = {}
        self.blocks: List[Block] = []
        self.total_emitted_pals: int = 0
        self.validator_set: List[str] = list(config.GENESIS_VALIDATORS.keys())
        
        # FINALITY CHECKPOINTS: Store validator set snapshots at finalized heights
        # Key: checkpoint_height (0, 100, 200, ...), Value: List[str] of validator addresses
        # This ensures all nodes agree on proposer selection regardless of current height
        self.validator_set_checkpoints: Dict[int, List[str]] = {
            0: list(config.GENESIS_VALIDATORS.keys())  # Genesis checkpoint
        }
        
        # Fork choice and finality mechanism with 51% attack prevention
        # Pass get_balance function for coin-weighted attack verification
        self.fork_choice = ForkChoice(get_balance_func=self.get_balance)
        
        # CRITICAL SECURITY: Economic Sybil defense via deposits
        self.validator_economics = ValidatorEconomics()
        
        # Initialize validator registry with genesis validators
        # CRITICAL: Genesis validators are PLACEHOLDERS - they're marked 'genesis' not 'active'
        # This ensures they're excluded from all active validator operations
        # New format: Dict[str, Dict] with keys: public_key, device_id, status, registered_at
        # Old format (backward compatibility): Dict[str, str] (address -> public_key)
        self.validator_registry: Dict[str, Union[str, Dict]] = {}
        for addr, pubkey in config.GENESIS_VALIDATORS.items():
            self.validator_registry[addr] = {
                'public_key': pubkey,
                'device_id': f"genesis_{addr[:16]}",  # Genesis validators use special device_id
                'status': 'genesis',  # CRITICAL: NOT 'active' - they're placeholders
                'registered_at': 0,  # Genesis time
                'registration_height': 0,  # Genesis block
                'activation_height': 0,  # Immediately active at genesis
                'deposit_amount': 0,  # No deposit for genesis validators
                'voting_power': 1,  # Equal voting power
                'proposer_priority': 0  # Tendermint priority tracking
            }
            # Initialize validator status in economics (active during grace period, no deposit yet)
            self.validator_economics.mark_active(addr)
        
        # VALIDATOR HEARTBEAT TRACKING: Track latest heartbeat height for each validator
        # Key: validator_address, Value: block_height of last heartbeat
        # Used for pool-based proposer selection with liveness checking
        self.validator_heartbeats: Dict[str, int] = {}
        
        # ATTESTATION MANAGER: Scalable validator liveness tracking via rotating committees
        # Replaces continuous heartbeats with epoch-based attestations for 100K+ validators
        self.attestation_manager = AttestationManager(
            epoch_length=config.EPOCH_LENGTH,
            attestation_window=config.ATTESTATION_WINDOW,
            committee_size=config.ATTESTATION_COMMITTEE_SIZE
        )
        
        # VRF MANAGER: Verifiable Random Function for secure proposer selection
        # Integrates with attestation committees for O(1) verification at 100K+ scale
        self.vrf_manager = VRFManager()
        
        # HISTORICAL STATE LOG: Track validator state at each height for deterministic VRF
        # CRITICAL FOR REORG: Enables reconstruction of exact validator set at any height
        # for deterministic VRF proposer validation during chain reorganization
        # Uses SQLite for ACID guarantees and crash consistency
        self.historical_state_log = SQLiteHistoricalStorage(
            db_path=os.path.join(data_dir, "history", "historical_state.db"),
            auto_migrate=True
        )
        
        # Track the previous frame for delta computation
        self._previous_validator_frame: Optional[ValidatorStateFrame] = None
        self._previous_historical_record: Optional[HistoricalStateRecord] = None
        
        # ROUND-BASED TIMEOUT CERTIFICATES: Tendermint-style consensus for liveness
        # Track which round each height is on (round increments when timeout certificate accepted)
        self.current_round_by_height: Dict[int, int] = {}  # height -> round
        
        # Collect timeout votes from validators (votes are temporary, cleared after certificate creation)
        # Key format: f"{height}_{round}_{proposer}" -> List[TimeoutVote]
        self.timeout_votes_cache: Dict[str, List] = {}
        
        # Prevent replay attacks: track used timeout certificate hashes
        self.used_timeout_certificates: Set[str] = set()
        
        # LIVENESS COMMITTEE: Track availability certificates and timeout releases
        # Committee size: 200-300 validators per height (constant for scalability)
        self.liveness_committee_cache: Dict[int, List[str]] = {}  # height -> committee members
        self.availability_certificates: Dict[int, any] = {}  # height -> AvailabilityCertificate
        self.timeout_release_certificates: Dict[int, any] = {}  # height -> TimeoutReleaseCertificate
        
        # Availability handshake state
        self.pending_availability_acks: Dict[str, List] = {}  # f"{height}_{proposer}" -> [AvailabilityAck]
        self.pending_timeout_notices: Dict[str, List] = {}  # f"{height}_{proposer}" -> [TimeoutNotice]
        
        # Warning throttle: Only log "No epoch attestations" once per hour to reduce spam
        self._last_attestation_warning: float = 0
        
        # CRITICAL FIX: Callback to check which validators are connected via P2P
        # Used for reward distribution fallback when attestations are unavailable
        # This ensures ONLY ONLINE NODES RECEIVE BLOCK REWARDS (TIMPAL policy)
        self._online_validators_callback = None
        
        os.makedirs(data_dir, exist_ok=True)
        
        if self.use_production_storage:
            from storage_basic import BlockchainStorage, CrashRecovery
            self.production_storage = BlockchainStorage(data_dir)
            self.crash_recovery = CrashRecovery(self.production_storage)
            
            recovery_report = self.crash_recovery.check_and_recover()
            if recovery_report['crash_detected']:
                print(f"⚠️  Crash detected, recovery performed: {recovery_report}")
        
        self.load_state()
        
        if self.use_production_storage and self.production_storage:
            if self.get_block_count() == 0:
                print("📝 Production storage: No blocks found, will save genesis on first save_state()")
            else:
                print(f"✅ Production storage: Loaded {self.get_block_count()} blocks")
    
    def set_online_validators_callback(self, callback):
        """
        Set callback function to get currently online validators from P2P network.
        
        This is used as fallback for reward distribution when attestations are unavailable.
        Ensures ONLY ONLINE NODES RECEIVE BLOCK REWARDS (TIMPAL policy).
        
        Args:
            callback: Function that returns set of validator addresses with active P2P connections
        """
        if callback is not None and not callable(callback):
            raise TypeError("Callback must be callable or None")
        self._online_validators_callback = callback
    
    def mark_validator_offline(self, address: str, current_height: int):
        """
        Mark a validator as offline when P2P detects they've disconnected.
        
        TIMPAL 10-BLOCK REWARD CUTOFF:
        - P2P detection is instant (gossip, peer reports, VRF confirmations)
        - Rewards stop after 10 consecutive blocks offline
        - Validator is automatically reinstated when they return
        
        Args:
            address: Validator address to mark offline
            current_height: Current blockchain height when offline was detected
        """
        if address not in self.validator_registry:
            return
        
        data = self.validator_registry[address]
        if not isinstance(data, dict):
            return
        
        # Only mark offline if not already marked
        if data.get('offline_since_height') is None:
            data['offline_since_height'] = current_height
            print(f"⚠️  Validator {address[:20]}... marked OFFLINE at height {current_height}")
    
    def mark_validator_online(self, address: str):
        """
        Mark a validator as online when P2P detects they've reconnected.
        
        TIMPAL 10-BLOCK REWARD CUTOFF:
        - If validator returns within 10 blocks, they never lose rewards
        - If they were offline > 10 blocks, rewards resume immediately on return
        
        Args:
            address: Validator address to mark online
        """
        if address not in self.validator_registry:
            return
        
        data = self.validator_registry[address]
        if not isinstance(data, dict):
            return
        
        # Clear offline status
        if data.get('offline_since_height') is not None:
            offline_height = data.get('offline_since_height')
            data['offline_since_height'] = None
            print(f"✅ Validator {address[:20]}... marked ONLINE (was offline since height {offline_height})")
    
    def is_validator_offline_for_rewards(self, address: str, current_height: int) -> bool:
        """
        Check if a validator has been offline long enough to lose rewards.
        
        CONSENSUS SAFETY FIX: This function is DISABLED to prevent forks.
        
        PROBLEM: The 10-block cutoff relied on `offline_since_height` which was set
        by P2P events (mark_validator_offline). Different nodes see different P2P
        events at different times, so they would compute different offline_since_height
        values → different reward allocations → different block hashes → FORK.
        
        TEMPORARY FIX: Always return False (all registered validators get rewards).
        This maintains consensus safety by ensuring all nodes compute identical
        reward allocations based purely on on-chain data.
        
        TODO: Implement purely on-chain liveness detection (e.g., "has proposed in
        last N blocks") to restore the 10-block cutoff without P2P dependency.
        
        Args:
            address: Validator address to check
            current_height: Current blockchain height
            
        Returns:
            Always False (disabled for consensus safety)
        """
        # DISABLED: P2P-based offline detection causes forks
        # All registered validators receive rewards until on-chain liveness is implemented
        return False
    
    def add_block(self, block: Block, skip_proposer_check: bool = False, use_historical_validators: bool = False) -> bool:
        """
        Add a block to the blockchain with comprehensive validation.
        Returns True if block was added, False if rejected.
        Raises ValueError only for critical errors that indicate system corruption.
        
        Args:
            block: Block to add
            skip_proposer_check: If True, skip strict proposer validation (used during sync)
                                This allows nodes to catch up without rejecting blocks due to
                                stale local checkpoint state
            use_historical_validators: If True, compute validator set based on block height
                                      for VRF proposer validation during chain reorganization.
                                      Uses activation_height to determine which validators
                                      were active when the block was originally created.
        """
        # CRITICAL SECURITY #1: Prevent duplicate blocks at same height
        if block.height < len(self.blocks):
            print(f"REJECT: Block at height {block.height} already exists (chain has {len(self.blocks)} blocks)")
            print(f"ℹ️  Duplicate proposer window detected — block already accepted (normal behavior)")
            return False
        
        # CRITICAL SECURITY #2: Enforce sequential heights (no gaps)
        expected_height = len(self.blocks)
        if block.height != expected_height:
            print(f"REJECT: Invalid block height - expected {expected_height}, got {block.height}")
            return False
        
        # CRITICAL SECURITY #3: Validate previous_hash to prevent chain forks
        if block.height > 0:  # Genesis block (height 0) has no previous
            latest = self.get_latest_block()
            if not latest:
                print(f"REJECT: No previous block exists for height {block.height}")
                return False
            if block.previous_hash != latest.block_hash:
                print(f"REJECT: Invalid previous_hash - expected {latest.block_hash}, got {block.previous_hash}")
                return False
            
            # CRITICAL SECURITY #3a: Validate timestamp is sequential
            if block.timestamp < latest.timestamp:
                print(f"REJECT: Block timestamp {block.timestamp} is before previous block {latest.timestamp}")
                return False
            
            # CRITICAL SECURITY #3b: Enforce minimum block time (prevent rapid block spam)
            # Blocks must be at least BLOCK_TIME seconds apart to maintain consistent 3s intervals
            # Allow small tolerance (-0.5s) for clock drift between validators
            # EXCEPTION: Skip validation during sync (historical blocks may have old timing)
            if not skip_proposer_check:
                min_timestamp = latest.timestamp + config.BLOCK_TIME
                MIN_BLOCK_TIME_TOLERANCE = 0.5  # Allow 0.5s tolerance for network/clock drift
                
                if block.timestamp < min_timestamp - MIN_BLOCK_TIME_TOLERANCE:
                    time_delta = block.timestamp - latest.timestamp
                    print(f"REJECT: Block created too quickly (time delta: {time_delta:.2f}s)")
                    print(f"  Parent timestamp: {latest.timestamp}")
                    print(f"  Block timestamp:  {block.timestamp}")
                    print(f"  Minimum required: {config.BLOCK_TIME}s (with {MIN_BLOCK_TIME_TOLERANCE}s tolerance)")
                    return False
            
            # CRITICAL SECURITY #3c: Reject blocks from far future (prevent time manipulation)
            # CLOCK DRIFT TOLERANCE: Only enforce for locally produced blocks (not during sync)
            # This allows nodes with different system clocks to sync the chain
            # Synced blocks are validated by chain continuity (sequential timestamps, previous_hash)
            import time
            current_time = time.time()
            if not skip_proposer_check:
                # Strict check for blocks THIS node produces
                if block.timestamp > current_time + config.MAX_FUTURE_TIMESTAMP_DRIFT:
                    print(f"REJECT: Block timestamp {block.timestamp} is too far in future (current: {current_time})")
                    return False
            else:
                # Lenient check for synced blocks - only warn, don't reject
                # Chain continuity (sequential timestamps) provides sufficient validation
                time_diff = block.timestamp - current_time
                if time_diff > 60:  # More than 1 minute in future
                    print(f"⚠️  CLOCK DRIFT: Block {block.height} timestamp is {time_diff:.0f}s ahead of local time - syncing anyway")
            
            # TIME-SLICED SLOTS VALIDATION: Ensure block timestamp is within assigned window
            # This prevents race conditions when offline validators come back online
            # Only the correct (slot, rank) can propose in their assigned time window
            # BOOTSTRAP EXCEPTION: Skip for first 10 blocks to handle delayed genesis startup
            BOOTSTRAP_HEIGHT = 10
            if not skip_proposer_check and hasattr(block, 'slot') and hasattr(block, 'rank'):
                from time_slots import validate_block_window
                
                # Get genesis timestamp
                genesis_block = self.get_block_by_height(0)
                if not genesis_block:
                    print(f"REJECT: Cannot validate window - no genesis block found")
                    return False
                
                genesis_timestamp = genesis_block.timestamp
                
                # BOOTSTRAP: Skip strict window validation for first N blocks
                # This allows the network to bootstrap even if genesis timestamp is stale
                if block.height <= BOOTSTRAP_HEIGHT:
                    print(f"🔧 BOOTSTRAP: Skipping window validation for block {block.height} (grace period)")
                elif not validate_block_window(block.timestamp, genesis_timestamp, block.slot, block.rank):
                    print(f"REJECT: Block timestamp {block.timestamp} outside assigned window")
                    print(f"  Slot: {block.slot}, Rank: {block.rank}")
                    return False
                
                # Verify proposer matches expected rank in VRF queue
                ranked_proposers = self.get_ranked_proposers_for_slot(block.slot, num_ranks=3)
                if block.rank < len(ranked_proposers):
                    expected_proposer = ranked_proposers[block.rank]
                    if block.proposer != expected_proposer:
                        print(f"REJECT: Wrong proposer for rank {block.rank}")
                        print(f"  Expected: {expected_proposer}, Got: {block.proposer}")
                        return False
        
        # TIMEOUT CERTIFICATE VALIDATION (MUST HAPPEN BEFORE PROPOSER CHECK)
        # Extract and validate timeout certificate if present, then increment round
        # This allows fallback proposers to be accepted based on the new round number
        timeout_certificate_tx = None
        for tx in block.transactions:
            if tx.tx_type == "timeout_certificate":
                if timeout_certificate_tx is not None:
                    print(f"REJECT: Block contains multiple timeout certificates")
                    return False
                timeout_certificate_tx = tx
        
        # If block contains timeout certificate, validate it BEFORE checking proposer
        if timeout_certificate_tx:
            if not self._validate_timeout_certificate(timeout_certificate_tx, block.height):
                print(f"REJECT: Invalid timeout certificate in block {block.height}")
                return False
            
            # Increment round AFTER validating certificate but BEFORE proposer check
            # This ensures the VRF proposer selection uses the NEW round number
            self.increment_round(block.height)
            print(f"✅ Timeout certificate accepted, round incremented for height {block.height}")
        
        # CRITICAL SECURITY #4: CANONICAL GENESIS VALIDATION
        # Prevent eclipse attacks by validating genesis block hash against known canonical value
        is_genesis = False
        
        if block.height == 0:
            if len(self.blocks) == 0:
                # Fresh bootstrap - validate genesis block matches canonical hash
                block_hash = block.calculate_hash()
                
                # Check if config has canonical genesis hash (testnet/mainnet security)
                canonical_hash = getattr(config, 'CANONICAL_GENESIS_HASH', None)
                if canonical_hash is not None:
                    if block_hash != canonical_hash:
                        print(f"🚨 SECURITY: Genesis block REJECTED - hash mismatch!")
                        print(f"  Expected (canonical): {canonical_hash}")
                        print(f"  Received: {block_hash}")
                        print(f"  This prevents eclipse attacks via forged genesis blocks")
                        return False
                    else:
                        print(f"✅ SECURITY: Genesis block validated against canonical hash")
                        print(f"  Hash: {block_hash}")
                        is_genesis = True
                else:
                    # No canonical hash in config (None or missing) - fresh testnet mode
                    print(f"🆕 FRESH TESTNET: No canonical hash set - accepting genesis block")
                    print(f"  Genesis hash: {block_hash}")
                    print(f"  Update CANONICAL_GENESIS_HASH in config to lock this genesis for security")
                    is_genesis = True
            else:
                # We already have blocks - REJECT any genesis block (duplicate or malicious)
                print(f"REJECT: Genesis block rejected - chain already has {len(self.blocks)} blocks")
                print(f"  This prevents eclipse attacks via duplicate genesis injection")
                return False
        
        # BOOTSTRAP GRACE PERIOD: During early blocks, allow any node to propose
        # This solves the chicken-egg problem: new networks need blocks to register validators
        # After grace period ends, only registered validators can propose
        # DYNAMIC FALLBACK: Continue bootstrap if no active validators exist (prevents deadlock)
        active_validators_check = self.get_active_validators()
        is_bootstrap_period = block.height <= 10 or len(active_validators_check) == 0
        
        if not is_genesis and hasattr(block, 'proposer'):
            # Check if block has proposer signature (MUST be signed!)
            if not hasattr(block, 'proposer_signature') or not block.proposer_signature:
                print(f"REJECT: Block {block.height} is not signed (missing proposer_signature)")
                return False
            
            if not is_bootstrap_period and block.proposer not in self.validator_registry:
                print(f"REJECT: Proposer {block.proposer} is not a registered validator")
                return False
            
            # CRITICAL SECURITY #5: Verify block signature with validator's public key
            # During bootstrap, we can't verify against registry (validators not registered yet)
            # BUT we still require the block to be signed - signature will be verified against
            # the public key in the validator registration transaction
            if not is_bootstrap_period:
                validator_public_key = self.get_validator_public_key(block.proposer)
                if not validator_public_key:
                    print(f"REJECT: No public key found for validator {block.proposer}")
                    return False
                
                if not block.verify_proposer_signature(validator_public_key):
                    print(f"REJECT: Invalid proposer signature for block {block.height}")
                    return False
                
                # VRF PROPOSER VALIDATION: Reject blocks from wrong proposer
                # This is critical for consensus - prevents validators from proposing out of turn
                # SYNC EXCEPTION: Skip during catch-up to avoid rejecting blocks due to stale checkpoints
                # 
                # CRITICAL FIX: Use block.slot instead of block.height for VRF proposer selection
                # Proposers are selected by SLOT (time-based), not by HEIGHT (sequential block number)
                # Using height causes validation to check against wrong proposer → consensus failure
                if not skip_proposer_check:
                    # Get the slot for this block (defaults to height if slot not set, maintains backward compat)
                    block_slot = block.slot if hasattr(block, 'slot') and block.slot is not None else block.height
                    
                    # HISTORICAL VALIDATOR SET: During chain reorganization, use validators
                    # that were active at the block's height, not current validators
                    if use_historical_validators:
                        # CRITICAL: Use HistoricalStateLog for deterministic VRF proposer reconstruction
                        # This provides exact liveness-filtered validator set and proposer queue
                        # that was used when the block was originally created
                        expected_proposer = self._get_historical_expected_proposer(block.height, block_slot)
                        
                        if expected_proposer is None:
                            # Historical state missing - this is a security issue during reorg
                            # Fall back to checkpoint-based validation as last resort
                            print(f"⚠️ No historical state for height {block.height} - using checkpoint fallback")
                            historical_validators = self.get_validators_at_height(block.height)
                            expected_proposer = self.select_proposer_vrf_based(block_slot, validators=historical_validators)
                    else:
                        expected_proposer = self.select_proposer_vrf_based(block_slot)
                    
                    if expected_proposer and block.proposer != expected_proposer:
                        print(f"REJECT: Wrong proposer for block {block.height} (slot {block_slot})")
                        print(f"  Expected: {expected_proposer}")
                        print(f"  Got:      {block.proposer}")
                        return False
        
        # CRITICAL SECURITY #5a: Verify merkle root (detect transaction tampering)
        expected_merkle = block.calculate_merkle_root()
        if block.merkle_root != expected_merkle:
            print(f"REJECT: Block {block.height} has invalid merkle root (block was tampered with)")
            print(f"  Expected: {expected_merkle}")
            print(f"  Got:      {block.merkle_root}")
            return False
        
        # CRITICAL SECURITY #5b: Enforce block size limits (prevent DoS)
        import json
        block_size = len(json.dumps(block.to_dict()).encode())
        if block_size > config.MAX_BLOCK_SIZE_BYTES:
            print(f"REJECT: Block {block.height} size {block_size} bytes exceeds limit {config.MAX_BLOCK_SIZE_BYTES}")
            return False
        
        # CRITICAL SECURITY #5c: Enforce transaction count limits (prevent DoS)
        if len(block.transactions) > config.MAX_TRANSACTIONS_PER_BLOCK:
            print(f"REJECT: Block {block.height} has {len(block.transactions)} transactions (max {config.MAX_TRANSACTIONS_PER_BLOCK})")
            return False
        
        # CRITICAL SECURITY #6: Enforce emission cap
        if block.reward > 0 and (self.total_emitted_pals + block.reward) > config.MAX_SUPPLY_PALS:
            print(f"REJECT: Emission cap reached - cannot emit more than {config.MAX_SUPPLY_PALS} pals")
            return False
        
        # CRITICAL SECURITY #7: Validate ALL transactions BEFORE adding block
        # This prevents blocks with invalid transactions from being accepted
        # Use temporary state to detect double-spends within same block
        temp_balances = dict(self.balances)
        temp_nonces = dict(self.nonces)
        
        # Track validator registrations within this block to prevent Sybil attacks
        # (multiple registrations with same device_id/pubkey in one block)
        temp_registered_devices = set()
        temp_registered_pubkeys = set()
        temp_registered_addresses = set()
        
        # Track epoch attestations within this block to prevent duplicates
        # Set of (epoch_number, validator_address) tuples
        temp_attested = set()
        
        for tx in block.transactions:
            # Verify transaction signature (SKIP for genesis block transactions)
            # Genesis block transactions are pre-validated during network bootstrap
            if not is_genesis and not tx.verify():
                print(f"REJECT: Block {block.height} contains transaction with invalid signature: {tx.tx_hash}")
                return False
            
            # Handle different transaction types
            if tx.tx_type == "validator_registration":
                # Validate validator registration transaction (SKIP for genesis block)
                # Genesis transactions are pre-validated during network bootstrap
                if not is_genesis and not tx.is_valid_validator_registration(temp_balances, temp_nonces):
                    print(f"REJECT: Block {block.height} contains invalid validator registration: {tx.tx_hash}")
                    return False
                
                # CRITICAL: Check for duplicate registration in EXISTING registry (Sybil prevention)
                # SKIP for genesis block since bootstrap pre-initialization may have registered the validator
                # SYNC FIX: Allow re-registration if it's the SAME validator (idempotent operation)
                # This enables nodes to sync past blocks containing their own registration
                if not is_genesis:
                    for existing_addr, data in self.validator_registry.items():
                        if isinstance(data, dict):
                            if data.get('device_id') == tx.device_id:
                                # Check if this is the same validator re-registering (idempotent)
                                if existing_addr == tx.sender:
                                    # Same validator re-registering - allow (enables sync)
                                    print(f"INFO: Validator {existing_addr[:20]}... re-registering (idempotent, allowing)")
                                else:
                                    # Different validator using same device - REJECT (Sybil attack)
                                    print(f"REJECT: Device {tx.device_id[:16]}... already registered to {existing_addr}")
                                    return False
                            if data.get('public_key') == tx.public_key:
                                # Check if this is the same validator re-registering (idempotent)
                                if existing_addr == tx.sender:
                                    # Same validator re-registering - allow (enables sync)
                                    print(f"INFO: Validator {existing_addr[:20]}... re-registering with same pubkey (idempotent, allowing)")
                                else:
                                    # Different validator using same public key - REJECT (Sybil attack)
                                    print(f"REJECT: Public key already registered to {existing_addr}")
                                    return False
                    
                    # CRITICAL: Check for duplicate registration WITHIN THIS BLOCK (Sybil bypass prevention!)
                    # A malicious proposer could try to register same device multiple times in one block
                    if tx.device_id in temp_registered_devices:
                        print(f"REJECT: Block {block.height} contains multiple registrations for device {tx.device_id[:16]}...")
                        print(f"       Sybil bypass attempt detected!")
                        return False
                    
                    if tx.public_key in temp_registered_pubkeys:
                        print(f"REJECT: Block {block.height} contains multiple registrations for same public key")
                        print(f"       Sybil bypass attempt detected!")
                        return False
                
                # Track this registration
                temp_registered_devices.add(tx.device_id)
                temp_registered_pubkeys.add(tx.public_key)
                temp_registered_addresses.add(tx.sender)
                
                # Update nonce for registration transaction
                expected_nonce = temp_nonces.get(tx.sender, 0)
                temp_nonces[tx.sender] = expected_nonce + 1
            
            elif tx.tx_type == "validator_heartbeat":
                # Validate validator heartbeat transaction
                if not tx.is_valid_validator_heartbeat(temp_balances, temp_nonces):
                    print(f"REJECT: Block {block.height} contains invalid validator heartbeat: {tx.tx_hash}")
                    return False
                
                # BOOTSTRAP FIX: During bootstrap period, allow heartbeats from unregistered validators
                # This prevents deadlock where nodes can't create blocks because heartbeats are rejected
                # SAME-BLOCK FIX: Allow heartbeats from validators registering in this same block
                # After bootstrap, only registered validators (or those registering NOW) can send heartbeats
                if not is_bootstrap_period and tx.sender not in self.validator_registry and tx.sender not in temp_registered_addresses:
                    print(f"REJECT: Heartbeat from unregistered validator: {tx.sender[:20]}...")
                    return False
                
                # Heartbeats don't update balance or nonce (they're informational)
            
            elif tx.tx_type == "epoch_attestation":
                # Validate epoch attestation transaction (scalable liveness tracking)
                if not tx.is_valid_epoch_attestation(temp_balances, temp_nonces):
                    print(f"REJECT: Block {block.height} contains invalid epoch attestation: {tx.tx_hash}")
                    return False
                
                # BOOTSTRAP FIX: During bootstrap period, allow attestations from unregistered validators
                # After bootstrap, only registered validators can send attestations
                if not is_bootstrap_period and tx.sender not in self.validator_registry and tx.sender not in temp_registered_addresses:
                    print(f"REJECT: Attestation from unregistered validator: {tx.sender[:20]}...")
                    return False
                
                # CRITICAL: Check for duplicate attestation WITHIN THIS BLOCK (prevent spam)
                # A malicious proposer could include multiple attestations from same validator
                attestation_key = (tx.epoch_number, tx.sender)
                if attestation_key in temp_attested:
                    print(f"REJECT: Block {block.height} contains duplicate attestation from {tx.sender[:20]}... for epoch {tx.epoch_number}")
                    print(f"       Attestation spam attempt detected!")
                    return False
                
                # SECURITY: epoch_number must be set (already checked in is_valid_epoch_attestation)
                if tx.epoch_number is None:
                    print(f"REJECT: Block {block.height} contains attestation with no epoch_number")
                    return False
                
                # CRITICAL SECURITY: Validate attestation against AttestationManager rules
                # This enforces committee membership, window bounds, and duplicate prevention
                all_validators_set = set(self.get_active_validators()) | temp_registered_addresses
                skip_committee = is_bootstrap_period  # During bootstrap, skip committee check
                is_valid, error_msg = self.attestation_manager.validate_attestation(
                    epoch_number=tx.epoch_number,
                    validator_address=tx.sender,
                    block_height=block.height,
                    all_validators=all_validators_set,
                    skip_committee_check=skip_committee
                )
                if not is_valid:
                    print(f"REJECT: Block {block.height} contains invalid attestation: {error_msg}")
                    return False
                
                # Track this attestation to prevent duplicates within same block
                temp_attested.add(attestation_key)
                
                # Attestations don't update balance or nonce (they're informational)
            
            else:
                # Regular transfer transaction
                # Verify nonce (prevent replay attacks and double-spends)
                expected_nonce = temp_nonces.get(tx.sender, 0)
                if tx.nonce != expected_nonce:
                    print(f"REJECT: Block {block.height} contains transaction with invalid nonce (expected {expected_nonce}, got {tx.nonce}): {tx.tx_hash}")
                    return False
                
                # Validate sender has sufficient balance
                sender_balance = temp_balances.get(tx.sender, 0)
                total_cost = tx.amount + tx.fee
                
                if sender_balance < total_cost:
                    print(f"REJECT: Block {block.height} contains transaction with insufficient balance (has {sender_balance}, needs {total_cost}): {tx.tx_hash}")
                    return False
                
                # Update temporary state for next transaction validation
                temp_balances[tx.sender] -= total_cost
                temp_balances[tx.recipient] = temp_balances.get(tx.recipient, 0) + tx.amount
                temp_nonces[tx.sender] = expected_nonce + 1
        
        # ALL validations passed - NOW add block to chain
        self.blocks.append(block)
        
        # Save new block to production storage (incremental, O(1) performance)
        self.save_new_block_to_storage(block)
        
        # GENESIS VALIDATOR AUTO-REGISTRATION: When ANY node receives block 0, automatically
        # register the proposer as the genesis validator. This ensures network nodes that sync
        # from bootstrap have the genesis validator in their registry (not just bootstrap nodes).
        if block.height == 0 and hasattr(block, 'proposer') and block.proposer.startswith('tmpl'):
            if block.proposer not in self.validator_registry:
                # Extract public key from genesis block's validator registration tx (if present)
                genesis_public_key = None
                genesis_device_id = "genesis"
                
                for tx in block.transactions:
                    if tx.tx_type == "validator_registration" and tx.sender == block.proposer:
                        genesis_public_key = tx.public_key
                        genesis_device_id = tx.device_id
                        break
                
                # If no registration tx found, check GENESIS_VALIDATORS config
                if not genesis_public_key and hasattr(config, 'GENESIS_VALIDATORS'):
                    if block.proposer in config.GENESIS_VALIDATORS:
                        genesis_public_key = config.GENESIS_VALIDATORS[block.proposer]
                
                # Last resort: extract public key by recovering from block signature
                if not genesis_public_key and hasattr(block, 'proposer_signature'):
                    try:
                        from ecdsa import VerifyingKey, SECP256k1
                        from hashlib import sha256
                        
                        # Recover public key from signature
                        # Block signature signs: block_hash + previous_hash + merkle_root
                        signature_data = f"{block.block_hash}{block.previous_hash}{block.merkle_root}"
                        message_hash = sha256(signature_data.encode()).digest()
                        
                        # Try to recover public key from signature (requires recovery_id, which we don't have)
                        # So we'll use a placeholder and log a warning
                        genesis_public_key = "pending_recovery"
                        print(f"⚠️  GENESIS: Could not extract public key for {block.proposer[:10]}...")
                        print(f"    Public key will be updated when validator registers")
                    except Exception as e:
                        genesis_public_key = "pending_recovery"
                        print(f"⚠️  GENESIS: Failed to recover public key: {e}")
                
                # Register genesis validator with immediate activation (no delay for bootstrap)
                self.validator_registry[block.proposer] = {
                    'public_key': genesis_public_key or "pending_recovery",
                    'device_id': genesis_device_id,
                    'status': 'active',  # Genesis validator is immediately active
                    'registered_at': block.timestamp,
                    'registration_height': 0,
                    'activation_height': 0,
                    'deposit_amount': 0,
                    'voting_power': 1,
                    'proposer_priority': 0
                }
                
                # CRITICAL FIX: Also add to validator_set for consensus membership checks
                # Without this, syncing nodes have genesis in registry but not in validator_set,
                # causing "Proposer not in validator set" rejections at block 11+
                if block.proposer not in self.validator_set:
                    self.validator_set.append(block.proposer)
                
                print(f"▶️  GENESIS: Auto-registered genesis validator {block.proposer[:10]}... from block 0")
        
        # Add finality checkpoint if at checkpoint interval
        if block.height > 0 and block.height % self.fork_choice.FINALITY_CHECKPOINT_INTERVAL == 0:
            self.add_finality_checkpoint(block.height, block.block_hash)
        
        # Apply all transactions (we know they're all valid now)
        for tx in block.transactions:
            expected_nonce = self.nonces.get(tx.sender, 0)
            
            if tx.tx_type == "validator_registration":
                # Apply validator registration transaction
                # GENESIS BLOCK SPECIAL CASE: Genesis validators are immediately active
                # NORMAL BLOCKS: Validator activates 2 blocks later (prevents race conditions)
                if block.height == 0:
                    # Genesis block: validator is immediately active with 'genesis' status
                    activation_height = 0
                    status = 'genesis'
                else:
                    # Normal blocks: Tendermint-style delay
                    activation_height = block.height + 2
                    status = 'pending'
                
                self.validator_registry[tx.sender] = {
                    'public_key': tx.public_key,
                    'device_id': tx.device_id,
                    'status': status,
                    'registered_at': tx.timestamp,
                    'registration_height': block.height,
                    'activation_height': activation_height,
                    'deposit_amount': 0,  # Set by economics system separately
                    'voting_power': 1,  # Equal voting power
                    'proposer_priority': 0  # Tendermint priority tracking
                }
                
                # CRITICAL FIX: Genesis validators (status='genesis') are immediately active
                # and must be added to validator_set for consensus membership checks
                if status == 'genesis' and tx.sender not in self.validator_set:
                    self.validator_set.append(tx.sender)
                # Non-genesis validators: Do NOT add to validator set immediately - will activate in 2 blocks
                
                # Update nonce
                self.nonces[tx.sender] = expected_nonce + 1
                
                print(f"✅ Validator registered on-chain: {tx.sender}")
                print(f"   Total validators: {len(self.get_active_validators())}")
            
            elif tx.tx_type == "validator_heartbeat":
                # Apply validator heartbeat - update heartbeat tracking
                self.validator_heartbeats[tx.sender] = block.height
                # Heartbeats don't update nonce or balance
            
            elif tx.tx_type == "epoch_attestation":
                # Apply epoch attestation - record in AttestationManager
                # Validation was already done in validate_block, so just record
                # epoch_number is guaranteed to be set (checked during validation)
                if tx.epoch_number is not None:
                    self.attestation_manager.record_attestation(
                        epoch_number=tx.epoch_number,
                        validator_address=tx.sender,
                        block_height=block.height
                    )
                # Attestations don't update nonce or balance
            
            else:
                # Apply regular transfer transaction
                sender_balance = self.balances.get(tx.sender, 0)
                total_cost = tx.amount + tx.fee
                
                self.balances[tx.sender] = sender_balance - total_cost
                self.balances[tx.recipient] = self.balances.get(tx.recipient, 0) + tx.amount
                self.nonces[tx.sender] = expected_nonce + 1
        
        # CRITICAL FAIRNESS: One-time deposit enforcement at grace period boundary
        # At block 5,000,000, ALL validators (new and existing) must have 100 TMPL deposit
        # This ensures "RULES ARE THE SAME FOR EVERYONE - DOESN'T MATTER WHEN YOU JOIN"
        if block.height == config.DEPOSIT_GRACE_PERIOD_BLOCKS:
            self._enforce_grace_period_transition(block.height)
        
        if block.reward_allocations:
            total_rewards_credited = 0
            for node_address, reward_amount in block.reward_allocations.items():
                if node_address and node_address.startswith("tmpl"):
                    old_balance = self.balances.get(node_address, 0)
                    self.balances[node_address] = old_balance + reward_amount
                    total_rewards_credited += reward_amount
                    # DEBUG: Log reward crediting (display in tTMPL, not pals)
                    reward_tmpl = reward_amount / 100_000_000
                    old_tmpl = old_balance / 100_000_000
                    new_tmpl = self.balances[node_address] / 100_000_000
                    print(f"💰 REWARD CREDITED: {node_address[:20]}... +{reward_tmpl:.8f} tTMPL (balance: {old_tmpl:,.8f} → {new_tmpl:,.8f} tTMPL)")
            if total_rewards_credited > 0:
                total_tmpl = total_rewards_credited / 100_000_000
                print(f"✅ Block {block.height}: {total_tmpl:.8f} tTMPL credited to {len(block.reward_allocations)} validators")
        
        # CRITICAL FAIRNESS: Redistribute slashed coins to honest validators
        # Slashed coins (from double-signing, invalid blocks) are distributed
        # equally among all active honest validators in the next block
        active_validators = self.get_active_validators()
        redistribution_rewards = self.validator_economics.get_redistribution_rewards(active_validators)
        if redistribution_rewards:
            for validator_address, redistribution_amount in redistribution_rewards.items():
                self.balances[validator_address] = self.balances.get(validator_address, 0) + redistribution_amount
        
        # CRITICAL FIX: Only add newly minted coins to total_emitted_pals, NOT transaction fees
        # Transaction fees are already in circulation (transferred from sender)
        # block.reward includes both new coins + transaction fees
        # We must recalculate the block_reward (new coins) based on emission schedule
        if block.reward > 0:
            remaining_emission = config.MAX_SUPPLY_PALS - self.total_emitted_pals
            if remaining_emission > 0:
                block_reward_pals = min(config.EMISSION_PER_BLOCK_PALS, remaining_emission)
                self.total_emitted_pals += block_reward_pals
                # NOTE: Transaction fees are NOT added to total_emitted_pals
                # They are distributed to validators but are already in circulation
        
        # FINALITY CHECKPOINT: Snapshot validator set at checkpoint intervals
        # This ensures all nodes can agree on proposer selection at future heights
        self._snapshot_validator_set_at_checkpoint(block.height)
        
        # TENDERMINT ACTIVATION: Activate any pending validators that reached their activation height
        # This ensures all nodes apply validator set changes at the same deterministic height
        self.activate_pending_validators(block.height)
        
        # TENDERMINT PRIORITY UPDATE: Update proposer priorities after committing this block
        # This ensures all nodes update priorities at the same time, maintaining consensus
        # CRITICAL: Only update priorities AFTER bootstrap period (block 10+)
        if block.height > 10:
            self.update_proposer_priorities_after_commit(block.height)
        
        # HISTORICAL STATE RECORDING: Capture validator state for deterministic VRF replay
        # This enables chain reorganization with proper proposer validation
        self._record_historical_state(block)
        
        self.save_state()
        return True
    
    def validate_transaction(self, tx) -> bool:
        """
        Validate a transaction before adding to mempool or block.
        Returns True if valid, False otherwise.
        """
        # Verify transaction signature
        if not tx.verify():
            return False
        
        # Verify nonce (prevent replay attacks)
        expected_nonce = self.nonces.get(tx.sender, 0)
        if tx.nonce != expected_nonce:
            return False
        
        # Validate sender has sufficient balance
        sender_balance = self.balances.get(tx.sender, 0)
        total_cost = tx.amount + tx.fee
        
        if sender_balance < total_cost:
            return False
        
        return True
    
    def get_balance(self, address: str) -> int:
        return self.balances.get(address, 0)
    
    def get_nonce(self, address: str) -> int:
        return self.nonces.get(address, 0)
    
    def get_latest_block(self) -> Optional[Block]:
        if len(self.blocks) == 0:
            return None
        return self.blocks[-1]
    
    def get_block_by_height(self, height: int) -> Optional[Block]:
        if height < 0 or height >= len(self.blocks):
            return None
        return self.blocks[height]
    
    def get_block_count(self) -> int:
        return len(self.blocks)
    
    def save_state(self, full_save: bool = False):
        """
        Save blockchain state to disk
        
        Args:
            full_save: If True, save entire chain (only for initial setup).
                      If False, use incremental saves (normal operation).
        """
        state = {
            "balances": self.balances,
            "nonces": self.nonces,
            "blocks": [block.to_dict() for block in self.blocks],
            "total_emitted_pals": self.total_emitted_pals,
            "validator_set": list(self.validator_set),
            "validator_registry": self.validator_registry,
            "finality_checkpoints": self.fork_choice.finality_checkpoints,
            "validator_economics": self.validator_economics.to_dict(),
            "validator_set_checkpoints": {str(h): vs for h, vs in self.validator_set_checkpoints.items()}
        }
        
        if self.use_production_storage and self.production_storage:
            if full_save or len(self.blocks) <= 1:
                self.production_storage.save_full_state(state)
            else:
                self.production_storage.save_state_only(state)
            
            current_height = len(self.blocks) - 1
            if current_height > 0 and current_height % 1000 == 0:
                self.crash_recovery.create_recovery_snapshot(current_height)
        else:
            ledger_file = os.path.join(self.data_dir, "ledger.json")
            temp_fd, temp_path = tempfile.mkstemp(dir=self.data_dir, suffix='.tmp')
            
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(state, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                
                os.replace(temp_path, ledger_file)
            except Exception as e:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise e
    
    def save_new_block_to_storage(self, block: Block):
        """Save a newly added block to production storage (incremental)"""
        if self.use_production_storage and self.production_storage:
            block_dict = block.to_dict()
            height = block.height
            self.production_storage.save_new_block(height, block_dict)
    
    def load_state(self):
        state = None
        
        if self.use_production_storage and self.production_storage:
            state = self.production_storage.load_full_state()
            
            if state is None:
                balances = self.production_storage.get_state('balances')
                if balances is not None:
                    state = {
                        'balances': balances,
                        'nonces': self.production_storage.get_state('nonces') or {},
                        'total_emitted_pals': self.production_storage.get_state('total_emitted_pals') or 0,
                        'validator_set': self.production_storage.get_state('validator_set') or [],
                        'validator_registry': self.production_storage.get_state('validator_registry') or {},
                        'finality_checkpoints': self.production_storage.get_state('finality_checkpoints') or {},
                        'validator_economics': self.production_storage.get_state('validator_economics') or {},
                        'blocks': []
                    }
            elif len(state.get('blocks', [])) == 0:
                state_balances = self.production_storage.get_state('balances')
                if state_balances:
                    state['balances'] = state_balances
                    state['nonces'] = self.production_storage.get_state('nonces') or state.get('nonces', {})
                    state['total_emitted_pals'] = self.production_storage.get_state('total_emitted_pals') or state.get('total_emitted_pals', 0)
                    state['validator_set'] = self.production_storage.get_state('validator_set') or state.get('validator_set', [])
                    state['validator_registry'] = self.production_storage.get_state('validator_registry') or state.get('validator_registry', {})
                    state['finality_checkpoints'] = self.production_storage.get_state('finality_checkpoints') or state.get('finality_checkpoints', {})
                    state['validator_economics'] = self.production_storage.get_state('validator_economics') or state.get('validator_economics', {})
        else:
            ledger_file = os.path.join(self.data_dir, "ledger.json")
            if not os.path.exists(ledger_file):
                return
            
            try:
                with open(ledger_file, 'r') as f:
                    state = json.load(f)
            except json.JSONDecodeError as e:
                print(f"ERROR: Corrupted ledger file: {e}")
                print("Cannot start - blockchain data is corrupted")
                raise e
        
        if state is None:
            return
        
        # BACKWARD COMPATIBILITY: Handle both old format (balances as TMPL floats) 
        # and new format (balances as pals integers)
        raw_balances = state.get("balances", {})
        self.balances = {}
        for addr, balance in raw_balances.items():
            if isinstance(balance, float):
                # Old format: TMPL as float, convert to pals
                self.balances[addr] = int(balance * config.PALS_PER_TMPL)
            else:
                # New format: pals as integer, use directly
                self.balances[addr] = int(balance)
        self.nonces = state.get("nonces", {})
        self.total_emitted_pals = state.get("total_emitted_pals", state.get("total_emitted", 0) * 1e8)
        if isinstance(self.total_emitted_pals, float):
            self.total_emitted_pals = int(self.total_emitted_pals)
        
        # Load validator set and registry from saved state (CRITICAL for persistence)
        self.validator_set = state.get("validator_set", list(config.GENESIS_VALIDATORS.keys()))
        
        # Load validator registry with backward compatibility
        loaded_registry = state.get("validator_registry", {})
        self.validator_registry = {}
        
        # Convert old format (str) to new format (dict) if needed
        for addr, data in loaded_registry.items():
            if isinstance(data, str):
                # Old format: public_key as string
                self.validator_registry[addr] = {
                    'public_key': data,
                    'device_id': f"legacy_{addr[:16]}",
                    'status': 'active',
                    'registered_at': 0
                }
            else:
                # New format: dict with full metadata
                self.validator_registry[addr] = data
        
        block_dicts = state.get("blocks", [])
        self.blocks = [Block.from_dict(b) for b in block_dicts]
        
        # CRITICAL FIX: Recalculate total_emitted_pals from blocks to ensure accuracy
        # This prevents bugs where the stored total_emitted_pals gets out of sync with actual block rewards
        if self.blocks:
            calculated_total = sum(block.reward for block in self.blocks)
            if self.total_emitted_pals != calculated_total:
                print(f"⚠️  Correcting total_emitted_pals: {self.total_emitted_pals:,} → {calculated_total:,} pals")
                self.total_emitted_pals = calculated_total
        
        # CRITICAL FIX: Load finality checkpoints from disk
        saved_checkpoints = state.get("finality_checkpoints", {})
        if saved_checkpoints:
            # Convert string keys to integers (JSON converts int keys to strings)
            self.fork_choice.finality_checkpoints = {
                int(height): block_hash 
                for height, block_hash in saved_checkpoints.items()
            }
            print(f"✅ Loaded {len(self.fork_choice.finality_checkpoints)} finality checkpoints from disk")
        
        # Load validator economics data (deposits, slashing history, withdrawals)
        economics_data = state.get("validator_economics", {})
        if economics_data:
            self.validator_economics.from_dict(economics_data)
            stats = self.validator_economics.get_economics_stats()
            print(f"✅ Loaded validator economics: {stats['active_validators']} validators, "
                  f"{stats['total_deposits_tmpl']} {config.SYMBOL} deposited")
        
        # CRITICAL FIX: Synchronize validator_economics.validator_status with validator_registry
        # This ensures that after loading from disk, validator_economics knows about all active validators
        for addr, data in self.validator_registry.items():
            if isinstance(data, dict):
                status = data.get('status', 'active')
                if status in ('active', 'genesis'):
                    # Mark validator as active in economics (idempotent operation)
                    self.validator_economics.mark_active(addr)
        
        # Load validator set checkpoints (CRITICAL for consensus synchronization)
        saved_checkpoints = state.get("validator_set_checkpoints", {})
        if saved_checkpoints:
            # Convert string keys back to integers (JSON converts int keys to strings)
            self.validator_set_checkpoints = {
                int(height): validator_list 
                for height, validator_list in saved_checkpoints.items()
            }
            print(f"✅ Loaded {len(self.validator_set_checkpoints)} validator set checkpoints from disk")
        else:
            # No saved checkpoints - initialize with genesis
            self.validator_set_checkpoints = {
                0: list(config.GENESIS_VALIDATORS.keys())
            }
        
        # CRITICAL FIX: Verify validator_registry matches blocks and rebuild if mismatch
        # This prevents state divergence when state.json is out of sync with blocks
        self._verify_and_repair_validator_state()
    
    def _verify_and_repair_validator_state(self):
        """
        Verify that validator_registry and validator_set match what's in the blocks.
        If there's a mismatch, rebuild the full state from blocks.
        
        This prevents state divergence when:
        - state.json is out of sync with blocks (e.g., old snapshot)
        - Node was restarted with different code that processes registrations differently
        - State file was corrupted or partially written
        
        CRITICAL: This ensures all nodes derive identical validator state from the same blocks.
        """
        if not self.blocks:
            return
        
        # Count validator registrations in blocks
        expected_validators = set()
        for block in self.blocks:
            for tx in block.transactions:
                if tx.tx_type == "validator_registration":
                    expected_validators.add(tx.sender)
        
        # Compare with loaded registry
        loaded_validators = set(self.validator_registry.keys())
        
        if expected_validators != loaded_validators:
            missing = expected_validators - loaded_validators
            extra = loaded_validators - expected_validators
            
            print(f"⚠️  VALIDATOR STATE MISMATCH DETECTED!")
            print(f"   Expected validators (from blocks): {len(expected_validators)}")
            print(f"   Loaded validators (from state):    {len(loaded_validators)}")
            if missing:
                print(f"   Missing validators: {[v[:20] + '...' for v in missing]}")
            if extra:
                print(f"   Extra validators:   {[v[:20] + '...' for v in extra]}")
            
            print(f"🔧 Rebuilding full state from blocks to repair mismatch...")
            self._rebuild_state_from_blocks()
            
            # Also rebuild validator_set from the repaired registry
            self.validator_set = []
            for addr, data in self.validator_registry.items():
                if isinstance(data, dict):
                    status = data.get('status', 'active')
                    if status in ('active', 'genesis'):
                        if addr not in self.validator_set:
                            self.validator_set.append(addr)
            
            print(f"✅ State repaired: {len(self.validator_registry)} validators in registry, "
                  f"{len(self.validator_set)} in active set")
        else:
            # Validators match, but also verify validator_set is correct
            # (validators might be in registry but not in validator_set)
            active_in_registry = set()
            for addr, data in self.validator_registry.items():
                if isinstance(data, dict):
                    status = data.get('status', 'active')
                    if status in ('active', 'genesis'):
                        active_in_registry.add(addr)
            
            current_set = set(self.validator_set)
            if active_in_registry != current_set:
                print(f"⚠️  VALIDATOR SET MISMATCH DETECTED!")
                print(f"   Active in registry: {len(active_in_registry)}")
                print(f"   In validator_set:   {len(current_set)}")
                
                # Repair validator_set
                self.validator_set = list(active_in_registry)
                print(f"✅ Validator set repaired: {len(self.validator_set)} validators")
    
    def _enforce_grace_period_transition(self, current_height: int):
        """
        ONE-TIME enforcement of deposit requirement at grace period boundary.
        
        CRITICAL FOR FAIRNESS: "RULES ARE THE SAME FOR EVERYONE - DOESN'T MATTER WHEN YOU JOIN"
        
        At block 5,000,000, ALL validators (new AND existing) must have 100 TMPL deposit.
        Uses smart transition mechanism:
        1. Validators who scheduled deposits: Auto-locked
        2. Validators with auto-lock enabled (default) + ≥100 TMPL: Auto-locked
        3. Others: Marked inactive until they deposit
        
        Args:
            current_height: Current blockchain height (should be DEPOSIT_GRACE_PERIOD_BLOCKS)
        """
        # Get all registered validators
        all_validators = list(self.validator_registry.keys())
        
        # Use the new process_transition() method which handles:
        # - Scheduled deposits
        # - Auto-lock preferences
        # - Balance checks
        # - Status updates
        transition_results = self.validator_economics.process_transition(
            registered_validators=all_validators,
            get_balance_func=lambda addr: self.balances.get(addr, 0)
        )
        
        # Apply balance deductions for auto-locked deposits
        for validator_address, (success, amount_locked, message) in transition_results.items():
            if success and amount_locked > 0:
                # Deduct locked amount from validator's balance
                current_balance = self.balances.get(validator_address, 0)
                self.balances[validator_address] = current_balance - amount_locked
        
        print(f"\n  From this block forward:")
        print(f"    • ALL validators must maintain 100 {config.SYMBOL} deposit")
        print(f"    • Inactive validators can reactivate by depositing 100 {config.SYMBOL}")
        print(f"    • RULES ARE THE SAME FOR EVERYONE")
    
    def get_validator_set(self) -> List[str]:
        """
        Get the current validator set, including genesis validator.
        
        Genesis validator is a PERMANENT participant that runs 24/7 and participates
        in consensus from block 0. It earns rewards like any other validator and acts
        as a safety net if other validators go offline.
        """
        validators = list(self.validator_set)
        return validators
    
    def set_validator_set(self, validators: List[str]):
        """Set the validator set (only for initialization/testing)"""
        self.validator_set = validators
    
    def get_validator_set_at_checkpoint(self, current_height: int) -> List[str]:
        """
        Get validator set from the last finalized checkpoint.
        
        CRITICAL CONSENSUS FIX: This ensures all nodes agree on the validator set
        for proposer selection, even when they're at different heights during sync.
        
        Based on Tendermint/Cosmos and Ethereum Beacon Chain approach:
        - Before first real checkpoint (height < INTERVAL): Use CURRENT validator set
        - After first checkpoint: Use validator set from LAST FINALIZED CHECKPOINT
        - All nodes at heights 101-199 use validator set from checkpoint 100
        
        Args:
            current_height: The current blockchain height (target height for proposer selection)
            
        Returns:
            List of validator addresses from the last checkpoint
        """
        # SPECIAL CASE: Before first checkpoint interval, use current validator set
        if current_height < config.FINALITY_CHECKPOINT_INTERVAL:
            return self.get_validator_set()
        
        # Calculate last finalized checkpoint height
        # Examples: height 100-199 → checkpoint 100, height 200-299 → checkpoint 200
        checkpoint_height = (current_height // config.FINALITY_CHECKPOINT_INTERVAL) * config.FINALITY_CHECKPOINT_INTERVAL
        
        # Get validator set from that checkpoint
        if checkpoint_height in self.validator_set_checkpoints:
            return self.validator_set_checkpoints[checkpoint_height]
        
        # Fallback to nearest earlier checkpoint
        available_checkpoints = sorted([h for h in self.validator_set_checkpoints.keys() if h <= checkpoint_height])
        if available_checkpoints:
            checkpoint_height = available_checkpoints[-1]
            return self.validator_set_checkpoints[checkpoint_height]
        
        # Ultimate fallback: use current validator set
        return self.get_validator_set()
    
    def _snapshot_validator_set_at_checkpoint(self, height: int):
        """
        Create a snapshot of the current validator set at a checkpoint height.
        
        CRITICAL FOR BOOTSTRAP TRANSITION:
        - During early blocks (height < 100): Create checkpoints every 10 blocks
        - After block 100: Create checkpoints every 100 blocks  
        - This ensures validator set is preserved when bootstrap period ends at block 10
        
        This prevents the deadlock where nodes at block 11 have no checkpoint
        and fall back to genesis validator only, rejecting all new validators.
        """
        should_checkpoint = False
        
        # Early blocks: Checkpoint every 10 blocks (bootstrap transition support)
        if height < config.FINALITY_CHECKPOINT_INTERVAL:
            if height % 10 == 0:
                should_checkpoint = True
        else:
            # Regular blocks: Checkpoint every 100 blocks
            if height % config.FINALITY_CHECKPOINT_INTERVAL == 0:
                should_checkpoint = True
        
        if should_checkpoint:
            # Store current validator set at this checkpoint
            self.validator_set_checkpoints[height] = list(self.validator_set)
            
            # Cleanup old checkpoints (keep last 10 for safety during bootstrap, 5 after)
            max_checkpoints = 10 if height < 100 else 5
            if len(self.validator_set_checkpoints) > max_checkpoints:
                checkpoints_to_keep = sorted(self.validator_set_checkpoints.keys())[-max_checkpoints:]
                self.validator_set_checkpoints = {
                    h: vs for h, vs in self.validator_set_checkpoints.items() 
                    if h in checkpoints_to_keep
                }
    
    def _get_historical_expected_proposer(self, block_height: int, block_slot: int) -> Optional[str]:
        """
        Get the expected proposer for a block using ONLY stored historical state.
        
        CRITICAL FOR REORG SECURITY: Uses HistoricalStateLog to reconstruct the exact
        VRF proposer selection that was valid when the block was originally created.
        This enables deterministic validation of proposer identity during chain
        reorganization without the security vulnerability of skipping VRF checks.
        
        SECURITY INVARIANT: This method MUST NOT use any current state (VRF manager,
        attestation manager, validator registry). It can ONLY use data stored in
        HistoricalStateLog to guarantee deterministic replay across all nodes.
        
        REORG HANDLING: During chain reorganization, the historical state at block_height
        may not exist yet (we just cleared it). In this case, we MUST use the PARENT's
        historical state (block_height - 1) which represents the fork point and is preserved.
        
        Args:
            block_height: Block height being validated
            block_slot: Block slot (time-based) for VRF selection
        
        Returns:
            Expected proposer address, or None if historical state unavailable
        """
        # PRIORITY 1: Use stored proposer queue directly from historical record
        # This is the MOST RELIABLE source as it was captured at commit time
        proposer_queue = self.historical_state_log.get_proposer_queue_at_height(block_height)
        if proposer_queue:
            return proposer_queue[0] if proposer_queue else None
        
        # PRIORITY 2: Reconstruct from AM snapshot at this height (contains epoch_seed + liveness)
        am_snapshot = self.historical_state_log.get_am_snapshot(block_height)
        if am_snapshot:
            stored_epoch_seed = am_snapshot.get('epoch_seed')
            if stored_epoch_seed and len(stored_epoch_seed) > 0:
                # Prefer liveness set stored directly in AM snapshot
                liveness_set = am_snapshot.get('combined_liveness_set')
                
                # Fallback to frame's liveness data
                if not liveness_set:
                    frame = self.historical_state_log.get_frame(block_height)
                    if frame and frame.liveness_filter_state:
                        liveness_set = frame.liveness_filter_state.combined_liveness_set
                
                if liveness_set:
                    queue = HistoricalStateBuilder.compute_proposer_queue_for_height(
                        committee=set(liveness_set),
                        epoch_seed=stored_epoch_seed,
                        block_height=block_height
                    )
                    return queue[0] if queue else None
        
        # PRIORITY 3 (REORG CASE): Use PARENT's historical state
        # During chain reorganization, we may not have state at block_height yet.
        # The parent's state (fork point) IS available and must be used.
        # The parent's liveness set + epoch seed determines who can propose at block_height.
        if block_height > 0:
            parent_am = self.historical_state_log.get_am_snapshot(block_height - 1)
            parent_frame = self.historical_state_log.get_frame(block_height - 1)
            
            if parent_am:
                stored_epoch_seed = parent_am.get('epoch_seed')
                if stored_epoch_seed and len(stored_epoch_seed) > 0:
                    # Prefer liveness set stored directly in AM snapshot
                    liveness_set = parent_am.get('combined_liveness_set')
                    
                    # Fallback to parent frame's liveness data
                    if not liveness_set and parent_frame and parent_frame.liveness_filter_state:
                        liveness_set = parent_frame.liveness_filter_state.combined_liveness_set
                    
                    if liveness_set:
                        queue = HistoricalStateBuilder.compute_proposer_queue_for_height(
                            committee=set(liveness_set),
                            epoch_seed=stored_epoch_seed,
                            block_height=block_height
                        )
                        return queue[0] if queue else None
        
        # PRIORITY 4: Reconstruct from nearest epoch snapshot
        state = self.historical_state_log.get_state_at_height(block_height)
        if state and state.get('nearest_epoch_snapshot'):
            epoch_snap = state['nearest_epoch_snapshot']
            if epoch_snap.ordered_committee and epoch_snap.epoch_seed:
                queue = HistoricalStateBuilder.compute_proposer_queue_for_height(
                    committee=set(epoch_snap.ordered_committee),
                    epoch_seed=epoch_snap.epoch_seed,
                    block_height=block_height
                )
                return queue[0] if queue else None
        
        # PRIORITY 5: Try grandparent state if parent is missing
        # This handles edge cases where we're validating block_height = 1 and only genesis exists
        if block_height > 1:
            grandparent_am = self.historical_state_log.get_am_snapshot(block_height - 2)
            grandparent_frame = self.historical_state_log.get_frame(block_height - 2)
            
            if grandparent_am and grandparent_frame:
                stored_epoch_seed = grandparent_am.get('epoch_seed')
                if stored_epoch_seed and grandparent_frame.liveness_filter_state:
                    liveness_set = grandparent_frame.liveness_filter_state.combined_liveness_set
                    if liveness_set:
                        queue = HistoricalStateBuilder.compute_proposer_queue_for_height(
                            committee=set(liveness_set),
                            epoch_seed=stored_epoch_seed,
                            block_height=block_height
                        )
                        return queue[0] if queue else None
        
        # FAIL-SAFE: Return None - caller must reject reorg if no historical data
        # DO NOT fall back to current VRF state as that breaks determinism
        # CRITICAL SECURITY: This is a HARD FAILURE for reorg validation
        # Missing historical state means we cannot deterministically verify proposers
        print(f"❌ SECURITY: No stored historical state available for height {block_height}")
        print(f"   This indicates incomplete historical data - chain may need migration or resync")
        print(f"   Reorg validation MUST fail to prevent accepting invalid chains")
        return None
    
    def _record_historical_state(self, block: Block):
        """
        Record historical state for a committed block.
        
        CRITICAL FOR REORG: Captures validator state, attestation data, and VRF context
        at this block height to enable deterministic proposer validation during
        chain reorganization.
        
        This method is called at block commit time (after all state changes are applied)
        to capture the exact state that was used for consensus at this height.
        
        Args:
            block: The block that was just committed
        """
        try:
            is_epoch_boundary = (block.height % config.EPOCH_LENGTH == 0 and block.height > 0)
            is_full_frame = is_epoch_boundary or (block.height % 100 == 0)
            
            recent_proposers = list(self._get_recently_active_validators(block.height, lookback_blocks=30))
            
            grace_period_validators = []
            for addr, data in self.validator_registry.items():
                if isinstance(data, dict):
                    activation_height = data.get('activation_height', 0)
                    if activation_height > 0 and (block.height - activation_height) <= 30:
                        grace_period_validators.append(addr)
            
            combined_liveness = list(set(recent_proposers) | set(grace_period_validators))
            
            # CRITICAL: Compute epoch_seed FIRST so it can be stored in ValidatorStateFrame
            # This ensures P2P validation can access epoch_seed from parent frame (never evicted)
            epoch_number = self.attestation_manager.get_epoch_number(block.height)
            
            # Determine the seed source block hash deterministically
            epoch_start = self.attestation_manager.get_epoch_start_block(epoch_number)
            if epoch_start > 0 and epoch_start - 1 < len(self.blocks):
                seed_source_hash = self.blocks[epoch_start - 1].block_hash
            elif block.height > 0 and block.height - 1 < len(self.blocks):
                seed_source_hash = self.blocks[block.height - 1].block_hash
            elif self.blocks:
                seed_source_hash = self.blocks[0].block_hash
            else:
                seed_source_hash = "genesis_epoch_seed_source"
            
            # Generate epoch_seed for this block (stored in ValidatorStateFrame)
            epoch_seed = self.vrf_manager.generate_epoch_seed(
                epoch_number=epoch_number,
                finalized_block_hash=seed_source_hash,
                attestation_data=""
            )
            
            if not epoch_seed or len(epoch_seed) == 0:
                raise ValueError(f"Failed to generate epoch_seed for epoch {epoch_number}")
            
            # Create validator frame WITH epoch_seed (critical for P2P validation)
            validator_frame = HistoricalStateBuilder.create_validator_frame(
                block_height=block.height,
                block_hash=block.block_hash,
                validator_registry=self.validator_registry,
                is_full_frame=is_full_frame,
                parent_frame=self._previous_validator_frame,
                recent_proposers=recent_proposers,
                grace_period_validators=grace_period_validators,
                combined_liveness_set=combined_liveness,
                lookback_blocks=30,
                grace_window_blocks=30,
                epoch_seed=epoch_seed,
                epoch_number=epoch_number
            )
            
            epoch_snapshot = None
            if is_epoch_boundary or block.height == 0:
                all_validators = set(self.get_active_validators())
                
                # Use already-computed epoch_seed and seed_source_hash
                epoch_snapshot = HistoricalStateBuilder.create_epoch_snapshot(
                    epoch_number=epoch_number,
                    epoch_length=config.EPOCH_LENGTH,
                    attestation_manager=self.attestation_manager,
                    epoch_seed=epoch_seed,
                    epoch_seed_source_hash=seed_source_hash,
                    all_validators=all_validators,
                    epoch_seed_source_height=max(0, block.height - 1)
                )
            
            am_snapshot = self.attestation_manager.export_snapshot(block.height)
            
            # CRITICAL: Ensure am_snapshot is NEVER None - create minimal snapshot if needed
            if am_snapshot is None:
                am_snapshot = {
                    'height': block.height,
                    'created_from_fallback': True
                }
            
            # Use already-computed epoch_seed (stored in ValidatorStateFrame too)
            am_snapshot['epoch_seed'] = epoch_seed
            am_snapshot['epoch_number'] = epoch_number
            
            # Also store liveness data in AM snapshot for rollback reconstruction
            am_snapshot['combined_liveness_set'] = combined_liveness
            
            # CRITICAL: Use LIVENESS-FILTERED validators for proposer queue computation
            # This MUST match what select_proposer_vrf_based() uses at consensus time
            proposer_queue = []
            if epoch_seed and combined_liveness:
                proposer_queue = HistoricalStateBuilder.compute_proposer_queue_for_height(
                    committee=set(combined_liveness),
                    epoch_seed=epoch_seed,
                    block_height=block.height
                )
            
            expected_proposer = proposer_queue[0] if proposer_queue else ""
            current_round = self.current_round_by_height.get(block.height, 0)
            
            historical_record = HistoricalStateBuilder.create_historical_record(
                block_height=block.height,
                block_hash=block.block_hash,
                validator_frame=validator_frame,
                epoch_number=epoch_number,
                epoch_snapshot=epoch_snapshot,
                previous_record=self._previous_historical_record,
                proposer_address=block.proposer if hasattr(block, 'proposer') else "",
                expected_proposer=expected_proposer,
                attestation_manager_snapshot=None,
                current_round=current_round,
                slot=block.height,
                proposer_queue=proposer_queue
            )
            
            self.historical_state_log.store(
                record=historical_record,
                validator_frame=validator_frame,
                epoch_snapshot=epoch_snapshot,
                am_snapshot=am_snapshot
            )
            
            self._previous_validator_frame = validator_frame
            self._previous_historical_record = historical_record
            
        except Exception as e:
            print(f"⚠️ Failed to record historical state for block {block.height}: {e}")
    
    def _get_recently_active_validators(self, current_height: int, lookback_blocks: int = 30) -> Set[str]:
        """
        Get validators who have PROPOSED BLOCKS in recent blockchain history (real-time liveness).
        
        This provides the most accurate real-time liveness check: if a validator proposed a block
        recently, they're definitely online. This catches validators who go offline mid-epoch
        (attestations are only submitted at epoch start and don't reflect real-time status).
        
        DETERMINISTIC: All nodes compute the same result from the same blockchain state.
        CONSENSUS-SAFE: No changes to validation rules, just smarter proposer selection.
        
        Args:
            current_height: Current blockchain height
            lookback_blocks: Number of recent blocks to check (default: 30 = 90 seconds)
        
        Returns:
            Set of validator addresses who proposed blocks recently
        """
        recently_active = set()
        
        # Check last N blocks to see which validators proposed them
        start_height = max(0, current_height - lookback_blocks)
        
        for height in range(start_height, current_height):
            if height < len(self.blocks):
                block = self.blocks[height]
                proposer = None
                
                # Prefer canonical field
                if hasattr(block, "proposer") and block.proposer:
                    proposer = block.proposer
                # Fallbacks for older/bootstrapped blocks
                elif hasattr(block, "proposer_address") and block.proposer_address:
                    proposer = block.proposer_address
                elif hasattr(block, "header") and isinstance(block.header, dict):
                    proposer = block.header.get("proposer") or block.header.get("proposer_address")
                
                # CRITICAL FIX: Exclude genesis block proposer "genesis" - it's not a real validator
                if proposer and proposer != "genesis":
                    recently_active.add(proposer)
        
        return recently_active
    
    def _get_liveness_filtered_validators(self, current_height: int) -> Set[str]:
        """
        Three-stage liveness filter that unions recent proposers, newly activated validators,
        and validators with recent attestations to enable proper rotation while maintaining liveness.
        
        FIXES CHICKEN-AND-EGG PROBLEM: Newly activated validators can enter rotation
        even before proposing their first block via dynamic grace period.
        
        DETERMINISTIC: All nodes compute the same result from the same blockchain state.
        MAINNET PARITY: Scales from 2 validators (testnet) to 100K+ (mainnet).
        
        Stages:
        1. Recent proposers (validators who proposed blocks recently)
        2. Recently activated (dynamic grace window based on validator count)
        3. Validators with recent epoch attestations (proves liveness without proposing)
        
        DYNAMIC GRACE WINDOW: max(MIN_BLOCKS, 2 × active_validator_count)
        - 2 validators: max(100, 4) = 100 blocks (~5 min)
        - 100 validators: max(100, 200) = 200 blocks (~10 min)
        - 100,000 validators: max(100, 200,000) = 200,000 blocks (~7 days)
        
        This ensures every validator gets at least 2 full proposer-priority rotations
        before being considered offline.
        
        Args:
            current_height: Current blockchain height
        
        Returns:
            Set of validator addresses that pass liveness filter
        """
        liveness_validators = set()
        
        # Count active validators for dynamic grace window calculation
        # CRITICAL: Count from REGISTRY (deterministic), not from any filtered set
        active_validator_count = 0
        registered_validators = []
        for validator_addr, validator in sorted(self.validator_registry.items()):
            if validator_addr == "genesis":
                continue
            if isinstance(validator, dict) and validator.get('status') in ('active', 'genesis'):
                active_validator_count += 1
                registered_validators.append(validator_addr[:20] + '...')
        
        # STAGE 1: Validators who proposed blocks recently (highest confidence of liveness)
        # Lookback scales with validator count: more validators = longer lookback
        proposer_lookback = max(30, active_validator_count)
        recent_proposers = self._get_recently_active_validators(current_height, lookback_blocks=proposer_lookback)
        liveness_validators.update(recent_proposers)
        
        # DEBUG: Log what Stage 1 found (helps diagnose consensus issues)
        if len(self.blocks) > 10:  # Only log after bootstrap
            print(f"   Registered validators: {active_validator_count}, Online validators (P2P): {len(recent_proposers)}")
        
        # STAGE 2: Recently activated validators (dynamic grace period)
        # DYNAMIC GRACE WINDOW: Scales with validator count
        # Formula: max(MIN_BLOCKS, 2 × active_validator_count)
        # This ensures every new validator gets at least 2 full priority rotations
        # to be selected by VRF before being considered offline
        MIN_GRACE_BLOCKS = 100  # Minimum 5 minutes at 3s block time
        ROTATION_MULTIPLIER = 2  # Allow 2 full rotations through all validators
        activation_grace_window = max(MIN_GRACE_BLOCKS, ROTATION_MULTIPLIER * active_validator_count)
        
        # CRITICAL FIX: Sort validator registry items by address for deterministic iteration
        # Dict.items() order is NOT guaranteed across different Python processes!
        for validator_addr, validator in sorted(self.validator_registry.items()):
            # CRITICAL FIX: Exclude genesis block proposer "genesis" - it's not a real validator
            if validator_addr == "genesis":
                continue
                
            if not isinstance(validator, dict):
                # Old-format registry, consider as active for backward compatibility
                liveness_validators.add(validator_addr)
                continue
            
            status = validator.get('status')
            if status not in ('active', 'genesis'):
                continue
            
            activation_height = validator.get('activation_height', 0)
            
            # Include validators within dynamic grace window:
            # - Normal activations within window
            # - Genesis (activation_height == 0) while still early enough in chain
            if (activation_height > 0 and current_height - activation_height < activation_grace_window) or \
               (activation_height == 0 and current_height < activation_grace_window):
                liveness_validators.add(validator_addr)
        
        # STAGE 3: Validators with recent epoch attestations (proves liveness without proposing)
        # This allows validators who haven't proposed yet to stay in rotation by attesting
        # Particularly important for large validator sets where proposer slots are rare
        try:
            if hasattr(self, 'attestation_manager') and self.attestation_manager:
                current_epoch = current_height // config.EPOCH_LENGTH
                # Check last 2 epochs for attestations
                for epoch in range(max(0, current_epoch - 1), current_epoch + 1):
                    attestations = self.attestation_manager.get_attestations_for_epoch(epoch)
                    for validator_addr in attestations.keys():
                        if validator_addr in self.validator_registry:
                            validator = self.validator_registry[validator_addr]
                            if isinstance(validator, dict) and validator.get('status') in ('active', 'genesis'):
                                liveness_validators.add(validator_addr)
        except Exception as e:
            # Attestation check is optional - don't fail if it errors
            pass
        
        # CONSENSUS-CRITICAL FIX: Removed P2P callback from proposer selection
        # 
        # PREVIOUS BUG: If stages 1-3 yielded empty set, we called _online_validators_callback
        # which returns P2P-connected validators. This is NON-DETERMINISTIC because different
        # nodes have different P2P connections → different proposer queues → FORKS.
        #
        # This function is used by get_ranked_proposers_for_slot() for PROPOSER SELECTION,
        # so any non-determinism here directly causes consensus forks.
        #
        # FIX: If stages 1-3 yield empty set, fall back to ALL active validators from registry.
        # This is deterministic because all nodes have the same validator_registry state.
        #
        # NOTE: P2P callback is still available for non-consensus uses (explorer UI, debug)
        # but MUST NOT be used for proposer selection or rewards.
        
        if not liveness_validators:
            # Deterministic fallback: all active validators from registry
            # This prevents network stall during genesis or testing scenarios
            for validator_addr, validator in sorted(self.validator_registry.items()):
                if validator_addr == "genesis":
                    continue
                if isinstance(validator, dict) and validator.get('status') in ('active', 'genesis'):
                    # Also check economics system for consistency
                    if self.validator_economics.is_validator_active(validator_addr, current_height):
                        liveness_validators.add(validator_addr)
        
        return liveness_validators
    
    def get_validators_with_recent_heartbeats(self, lookback_blocks: int = 5) -> Set[str]:
        """
        Get validators who have submitted heartbeat transactions in recent blocks.
        
        DETERMINISTIC LIVENESS CHECK: All nodes see the same blocks/transactions,
        so all nodes will compute the same set of "online" validators.
        
        Args:
            lookback_blocks: Number of recent blocks to check for heartbeats (default: 5 = 15 seconds)
        
        Returns:
            Set of validator addresses who have recent heartbeat transactions
        """
        validators_with_heartbeats = set()
        current_height = len(self.blocks) - 1
        
        # Check last N blocks for heartbeat transactions
        start_block = max(0, current_height - lookback_blocks + 1)
        
        for height in range(start_block, current_height + 1):
            if height < len(self.blocks):
                block = self.blocks[height]
                for tx in block.transactions:
                    if tx.tx_type == "validator_heartbeat":
                        validators_with_heartbeats.add(tx.sender)
        
        return validators_with_heartbeats
    
    def get_validators_with_recent_attestations(self, lookback_blocks: int = 100) -> Set[str]:
        """
        Get validators who have submitted epoch attestations in recent epochs.
        
        SCALABLE LIVENESS CHECK: Query AttestationManager directly instead of scanning
        all block transactions. Supports 100K+ validators efficiently.
        
        Args:
            lookback_blocks: Number of recent blocks to check (default: 100 = 1 epoch)
        
        Returns:
            Set of validator addresses who have recent epoch attestations
        """
        current_height = len(self.blocks) - 1
        
        # BOOTSTRAP FALLBACK: If we're in early blocks or have no attestations yet,
        # return all registered validators so network doesn't stall
        if current_height < self.attestation_manager.epoch_length:
            # Bootstrap period - no attestations required yet
            return set(self.validator_registry.keys())
        
        # Get current and previous epochs
        current_epoch = self.attestation_manager.get_epoch_number(current_height)
        
        # Collect validators from current and previous epoch attestations
        validators_with_attestations = set()
        
        # Check current epoch
        current_attestations = self.attestation_manager.get_attestations_for_epoch(current_epoch)
        validators_with_attestations.update(current_attestations.keys())
        
        # Also check previous epoch (validators who attested recently but not this epoch yet)
        if current_epoch > 0:
            prev_attestations = self.attestation_manager.get_attestations_for_epoch(current_epoch - 1)
            validators_with_attestations.update(prev_attestations.keys())
        
        # CONSENSUS-CRITICAL FIX: Removed P2P callback from attestation fallback
        #
        # PREVIOUS BUG: When no attestations, we called _online_validators_callback
        # which returns P2P-connected validators. This is NON-DETERMINISTIC because
        # different nodes have different P2P connections → different validator sets → FORKS.
        #
        # FIX: If no attestations, fall back to ALL active validators from registry.
        # This is deterministic because all nodes have the same validator_registry state.
        #
        # NOTE: P2P callback is still available for non-consensus uses (explorer UI, debug)
        # but MUST NOT be used for reward calculation or proposer selection.
        
        if not validators_with_attestations:
            # Skip all warnings in read-only mode (e.g., block explorer)
            if self.read_only:
                # Read-only context doesn't distribute rewards, return all registered validators for display
                return set(self.validator_registry.keys())
            
            # Deterministic fallback: all active validators from registry
            # This prevents network stall during genesis or testing scenarios
            fallback_validators = set()
            for validator_addr, validator in sorted(self.validator_registry.items()):
                if validator_addr == "genesis":
                    continue
                if isinstance(validator, dict) and validator.get('status') in ('active', 'genesis'):
                    if self.validator_economics.is_validator_active(validator_addr, current_height):
                        fallback_validators.add(validator_addr)
            
            return sorted(list(fallback_validators))
        
        # CRITICAL FIX: Return sorted list, not set (sets have non-deterministic order)
        # This ensures reward_allocations dict is built in same order on all nodes
        return sorted(list(validators_with_attestations))
    

    def get_active_validators(self) -> list:
        """
        Get validators who are:
        1) Registered & 'active' (or 'genesis' during bootstrap),
        2) Satisfy deposit rules (post-grace),
        3) Are online by our scalable liveness (recent attestations) OR all active if no attestations.

        CONSENSUS-CRITICAL: Returns a deterministic list of validator addresses.
        
        CRITICAL FIX: Removed P2P callback fallback which caused forks.
        Different nodes have different P2P connections → different validator sets → FORKS.
        Now uses deterministic fallback: all active validators from registry.
        """
        active = []
        current_height = len(self.blocks) - 1

        # Primary scalable liveness (epoch attestations)
        # NOTE: get_validators_with_recent_attestations now returns deterministic fallback
        # if no attestations are found (all active validators from registry)
        validators_with_attestations = self.get_validators_with_recent_attestations(lookback_blocks=100)

        for addr, data in sorted(self.validator_registry.items()):
            if addr == "genesis":
                continue

            is_registered_active = False
            if isinstance(data, dict) and data.get('status') in ('active', 'genesis'):
                is_registered_active = True
            elif isinstance(data, str):
                # legacy registry format
                is_registered_active = True

            if not is_registered_active:
                continue

            if not self.validator_economics.is_validator_active(addr, current_height):
                continue

            # After bootstrap, require liveness (attestations or fallback)
            if current_height > 10:
                if validators_with_attestations:
                    if addr not in validators_with_attestations:
                        continue
                # NOTE: No P2P fallback - get_validators_with_recent_attestations
                # already returns deterministic fallback if no attestations

            active.append(addr)

        # CRITICAL FIX: Return sorted list for deterministic reward_allocations ordering
        # This ensures all nodes build reward dicts in identical order → same block hash
        return sorted(active)

    def get_online_validators_deterministic(self, current_height: int) -> list:
        """
        DETERMINISTIC online validator detection using ONLY on-chain data.
        
        TIMPAL PHILOSOPHY: All online validators receive equal block rewards.
        
        CONSENSUS SAFETY: This function MUST be deterministic across all nodes.
        All nodes must compute the SAME set of online validators for the same height,
        otherwise they will produce blocks with different reward_allocations → different
        block hashes → FORK.
        
        CRITICAL FIX: Removed P2P-based liveness sources that caused forks:
        - REMOVED: P2P callback (_online_validators_callback) - different nodes have
          different P2P connections and see different validators as online
        - REMOVED: P2P-based offline tracking (offline_since_height) - set by P2P events
          which occur at different times on different nodes
        
        LIVENESS SOURCES (all deterministic, on-chain only):
        1. Recent block proposers (from blockchain - all nodes see same blocks)
        2. Epoch attestations (from blockchain - all nodes see same attestations)
        
        Args:
            current_height: Current blockchain height
            
        Returns:
            Sorted list of online validator addresses (deterministic order)
        """
        online_validators = set()
        
        # Count active validators for dynamic window calculation
        active_validator_count = 0
        for addr, data in self.validator_registry.items():
            if addr == "genesis":
                continue
            if isinstance(data, dict) and data.get('status') in ('active', 'genesis'):
                active_validator_count += 1
        
        # SOURCE 1: Recent block proposers (highest confidence of liveness)
        # Lookback scales with validator count to ensure all validators get fair chance
        # DETERMINISTIC: All nodes see the same blocks in their chain
        proposer_lookback = max(30, active_validator_count * 2)
        recent_proposers = self._get_recently_active_validators(current_height, lookback_blocks=proposer_lookback)
        online_validators.update(recent_proposers)
        
        # SOURCE 2: Attestation holders (epoch-based liveness proof)
        # Validators must submit attestations each epoch to prove they're online and synced
        # DETERMINISTIC: Attestations are recorded on-chain, all nodes see the same data
        validators_with_attestations = self.get_validators_with_recent_attestations(lookback_blocks=100)
        online_validators.update(validators_with_attestations)
        
        # REMOVED: P2P-tracked online validators - NON-DETERMINISTIC, CAUSES FORKS
        # Different nodes have different P2P connections and see different validators
        # as online, leading to different reward_allocations → different block hashes
        # 
        # The P2P callback is still available for non-consensus uses (e.g., explorer UI)
        # but MUST NOT be used for reward calculation
        
        # Filter to only registered, active validators that pass economics
        result = []
        for addr in online_validators:
            if addr not in self.validator_registry:
                continue
            data = self.validator_registry[addr]
            if isinstance(data, dict):
                if data.get('status') not in ('active', 'genesis'):
                    continue
            if not self.validator_economics.is_validator_active(addr, current_height):
                continue
            
            # NOTE: is_validator_offline_for_rewards is now disabled (always returns False)
            # to prevent non-deterministic behavior from P2P-set offline_since_height
            if self.is_validator_offline_for_rewards(addr, current_height):
                continue
            
            result.append(addr)
        
        # DEBUG: Log the final result for troubleshooting
        if len(result) > 0:
            print(f"📊 LIVENESS: get_online_validators_deterministic(height={current_height}) returning {len(result)} validators: {[a[:12]+'...' for a in sorted(result)]}")
        
        # CRITICAL: Sorted for deterministic ordering across all nodes
        return sorted(result)
    
    def get_validators_at_height(self, block_height: int) -> set:
        """
        Get the set of validators that were ACTIVE at a specific block height.
        
        This is used during chain reorganization to replay blocks with the
        correct historical validator set for VRF proposer selection.
        
        Uses activation_height to determine which validators existed when
        the block was originally created:
        - Validators with activation_height <= block_height are included
        - Validators registered AFTER block_height are excluded
        
        Args:
            block_height: The historical block height to get validators for
            
        Returns:
            Set of validator addresses that were active at that height
            
        SECURITY: This enables secure replay of historical blocks during
        chain reorganization without skipping proposer validation.
        """
        historical_validators = set()
        
        for addr, data in self.validator_registry.items():
            if addr == "genesis":
                continue
            
            if not isinstance(data, dict):
                continue
            
            # Check validator status
            status = data.get('status')
            if status not in ('active', 'genesis', 'pending'):
                continue
            
            # Get activation height (when validator became active)
            activation_height = data.get('activation_height', 0)
            
            # For genesis validators, activation_height is 0
            # For other validators, they must have been activated at or before block_height
            if activation_height <= block_height:
                # Check if validator was pending at this height (not yet activated)
                if status == 'pending' and block_height < activation_height:
                    continue
                historical_validators.add(addr)
        
        return historical_validators
    
    
    def register_validator(self, address: str, public_key: str, device_id: str) -> bool:
        """
        Register a new validator dynamically with MANDATORY deposit.
        
        Args:
            address: Validator TMPL address
            public_key: ECDSA public key (128 hex chars)
            device_id: Unique device fingerprint hash
        
        Returns:
            True if registration successful, False otherwise
        
        CRITICAL SECURITY - Multi-Layer Sybil Prevention:
            1. Economic: 100 TMPL deposit requirement (makes mass registration expensive)
            2. Device: One validator per device (device_id must be unique)
            3. Identity: One validator per address, one per public key
        """
        # Validate address format
        if not address.startswith("tmpl") or len(address) < 20:
            print(f"REJECT: Invalid address format: {address}")
            return False
        
        # Validate public key format (128 hex characters for ECDSA)
        if not isinstance(public_key, str) or len(public_key) != 128:
            print(f"REJECT: Invalid public key format (must be 128 hex chars)")
            return False
        
        try:
            int(public_key, 16)  # Verify it's valid hex
        except ValueError:
            print(f"REJECT: Public key is not valid hexadecimal")
            return False
        
        # Check if address already registered
        if address in self.validator_registry:
            # Already registered - not an error, just return True
            return True
        
        # SYBIL PREVENTION #1: Check device_id uniqueness (one validator per device!)
        for existing_addr, data in self.validator_registry.items():
            if isinstance(data, dict):
                existing_device_id = data.get('device_id')
                if existing_device_id and existing_device_id == device_id:
                    print(f"REJECT: Device {device_id[:16]}... already has a registered validator ({existing_addr})")
                    print(f"       Sybil attack prevention: Only ONE validator per device allowed")
                    return False
                
                # Also check public key uniqueness
                existing_pubkey = data.get('public_key')
                if existing_pubkey == public_key:
                    print(f"REJECT: Public key already registered to {existing_addr}")
                    return False
        
        # CRITICAL SECURITY FIX #3: ECONOMIC SYBIL PREVENTION - Require deposit
        # This makes running 1,000 validators cost 100,000 TMPL (~$100k-$1M)
        # GRACE PERIOD: First ~6 months (5M blocks), NO deposit required for network growth
        current_balance = self.get_balance(address)
        current_height = len(self.blocks)  # Current blockchain height
        
        can_register, reason = self.validator_economics.can_register_validator(address, current_balance, current_height)
        
        if not can_register:
            print(f"REJECT: Cannot register validator - {reason}")
            return False
        
        # Calculate and deduct deposit (0 during grace period)
        deposit_amount = self.validator_economics.calculate_deposit_requirement(address, current_height)
        
        if current_balance < deposit_amount:
            print(f"REJECT: Insufficient balance for validator deposit")
            print(f"       Need: {deposit_amount / config.PALS_PER_TMPL} {config.SYMBOL}")
            print(f"       Have: {current_balance / config.PALS_PER_TMPL} {config.SYMBOL}")
            return False
        
        # Deduct deposit from balance (deposit is locked, not burned)
        # During grace period, deposit_amount = 0, so no balance change
        self.balances[address] = current_balance - deposit_amount
        
        # Record deposit in economics system
        success, message = self.validator_economics.process_validator_deposit(address, deposit_amount, current_height)
        if not success:
            # Refund if deposit processing failed
            self.balances[address] = current_balance
            print(f"REJECT: Deposit processing failed - {message}")
            return False
        
        # Register the new validator with Tendermint-style priority tracking
        # ACTIVATION DELAY: Validator activates 2 blocks after registration (Tendermint standard)
        # This prevents race conditions - all nodes apply updates at the same height
        activation_height = current_height + 2
        
        self.validator_registry[address] = {
            'public_key': public_key,
            'device_id': device_id,
            'status': 'pending',  # Start as pending, becomes active at activation_height
            'registered_at': time.time(),
            'registration_height': current_height,
            'activation_height': activation_height,
            'deposit_amount': deposit_amount,
            'voting_power': 1,  # All validators have equal power for now
            'proposer_priority': 0  # Tendermint priority system (updated after each block)
        }
        
        # Do NOT add to validator set immediately - will be added when activated
        # This ensures deterministic validator set changes at specific heights
        
        # Persist to disk
        self.save_state()
        
        print(f"✅ New validator registered: {address}")
        print(f"   Status: PENDING (will activate at block {activation_height})")
        if deposit_amount > 0:
            print(f"   Deposit: {deposit_amount / config.PALS_PER_TMPL} {config.SYMBOL} (locked)")
        else:
            print(f"   Deposit: WAIVED (grace period - block {current_height} < {config.DEPOSIT_GRACE_PERIOD_BLOCKS})")
        print(f"   Remaining balance: {self.balances[address] / config.PALS_PER_TMPL} {config.SYMBOL}")
        print(f"   Device ID: {device_id[:32]}...")
        print(f"   Total active validators: {len(self.get_active_validators())}")
        
        return True
    
    def peek_next_proposer_tendermint(self, current_height: int) -> Optional[str]:
        """
        Peek at who the next proposer should be WITHOUT updating priorities.
        
        Used for block validation to check if the proposer is correct.
        Does NOT modify proposer priorities (read-only operation).
        
        Includes genesis validator as a permanent participant.
        
        Args:
            current_height: Current blockchain height
            
        Returns:
            Address of expected proposer, or None if no active validators
        """
        # Get active validators with their priority data (including genesis)
        active_validators = []
        
        for addr, data in self.validator_registry.items():
            # CRITICAL FIX: Exclude genesis block proposer "genesis" - it's not a real validator
            if addr == "genesis":
                continue
                
            if isinstance(data, dict):
                status = data.get('status')
                activation_height = data.get('activation_height', 0)
                voting_power = data.get('voting_power', 1)
                
                # Include both 'active' and 'genesis' status validators
                if (status in ('active', 'genesis') and 
                    current_height >= activation_height):
                    active_validators.append({
                        'address': addr,
                        'voting_power': voting_power,
                        'priority': data.get('proposer_priority', 0)
                    })
        
        if not active_validators:
            return None
        
        # Select validator with highest priority (deterministic tie-break)
        expected_proposer = max(active_validators, 
                              key=lambda v: (v['priority'], -ord(v['address'][0])))
        
        return expected_proposer['address']
    
    def select_proposer_pool_based(self, current_height: int) -> Optional[str]:
        """
        Select next block proposer using pool-based selection with heartbeat liveness.
        
        CRITICAL: This is a PURE function - it only reads committed blockchain state.
        All nodes with the same blockchain state will compute the same proposer.
        
        Algorithm:
        1. Build pool of active validators with recent heartbeats (last 6 blocks / ~18 seconds)
        2. If pool empty, fallback to ALL active validators (bootstrap grace period)
        3. Deterministically select from pool using: hash(prev_block_hash || height) mod pool_size
        
        Args:
            current_height: Current blockchain height
            
        Returns:
            Address of selected proposer, or None if no validators available
        """
        import hashlib
        
        # Get active validators (both genesis and dynamically registered)
        active_validators = []
        
        for addr, data in self.validator_registry.items():
            # CRITICAL FIX: Exclude genesis block proposer "genesis" - it's not a real validator
            if addr == "genesis":
                continue
                
            if isinstance(data, dict):
                status = data.get('status')
                activation_height = data.get('activation_height', 0)
                
                # Include both 'genesis' and 'active' validators past their activation height
                # Genesis validators are permanent participants; dynamic validators join upon registration
                if (status in ('active', 'genesis') and 
                    current_height >= activation_height):
                    active_validators.append(addr)
        
        if not active_validators:
            return None
        
        # Build pool of validators with recent heartbeats (within last 6 blocks / ~18 seconds)
        # Heartbeat TTL: 6 blocks (assumes 3-second block time = ~18 seconds for realistic network latency)
        heartbeat_ttl = 6
        live_pool = []
        
        for addr in active_validators:
            last_heartbeat_height = self.validator_heartbeats.get(addr, 0)
            if current_height - last_heartbeat_height <= heartbeat_ttl:
                live_pool.append(addr)
        
        # FALLBACK: If no one has sent heartbeats yet (bootstrap), use all active validators
        if not live_pool:
            live_pool = active_validators
            print(f"⚠️  No recent heartbeats, using all {len(live_pool)} active validators")
        
        # Sort pool lexicographically for deterministic ordering
        live_pool.sort()
        
        # Deterministic random selection using previous block hash as seed
        if current_height == 0:
            # Genesis block: select first validator
            selected = live_pool[0]
        else:
            # CRITICAL FIX: Use get_block_by_height for safe access
            # The current_height parameter is actually the NEXT block height,
            # so we need to get the previous block at (current_height - 1)
            prev_block = self.get_block_by_height(current_height - 1)
            if prev_block:
                # Create deterministic seed from previous block hash + current height
                seed_data = f"{prev_block.block_hash}{current_height}"
                seed_hash = hashlib.sha256(seed_data.encode()).hexdigest()
                # Convert hash to integer and mod by pool size
                seed_int = int(seed_hash, 16)
                pool_index = seed_int % len(live_pool)
                selected = live_pool[pool_index]
            else:
                selected = live_pool[0]
        
        print(f"🎲 Pool proposer [height {current_height}]: {selected[:20]}... (pool size: {len(live_pool)})")
        return selected
    
    def select_proposer_vrf_based(self, current_height: int, validators: Optional[set] = None) -> Optional[str]:
        """
        Select next block proposer using VRF with epoch-based committee attestations.
        
        SCALABILITY: This method scales to 100,000+ validators by only considering
        the active committee (1,000 validators) instead of all registered validators.
        
        LIVENESS FILTERING: Filters committee to only include validators with recent attestations
        from prior finalized epoch, preventing offline validators from being selected.
        
        Algorithm:
        1. Determine current epoch from block height
        2. Get active committee members who have attested in prior epoch
        3. Generate deterministic epoch seed from finalized blocks
        4. Use VRF to select proposer from committee (O(committee_size) = O(1000))
        5. Cache ordered proposer queue for fallback selection
        
        Args:
            current_height: Current blockchain height
            validators: Optional set of validator addresses to use for selection.
                       If provided, uses these instead of liveness filter (for historical replay).
            
        Returns:
            Address of selected proposer, or None if no validators available
        
        SECURITY: Uses finalized block hashes for unpredictability. Committee members
        cannot manipulate proposer selection without controlling finalized blocks.
        
        CONSENSUS: This method is purely deterministic and NEVER falls back to pool-based
        selection. All nodes must compute the same proposer from the same blockchain state.
        """
        import hashlib
        
        # Get current epoch
        current_epoch = self.attestation_manager.get_epoch_number(current_height)
        
        # If historical validators provided (during chain reorganization replay),
        # use them directly instead of liveness filter
        if validators is not None:
            validators_for_selection = validators
            print(f"📜 Historical replay: Using {len(validators_for_selection)} validators at height {current_height}")
        else:
            # THREE-STAGE LIVENESS FILTER: Unions recent proposers, heartbeats, and newly activated validators
            # This enables proper round-robin rotation while maintaining liveness detection
            # Fixes chicken-and-egg problem where new validators couldn't enter rotation
            liveness_filtered_validators = self._get_liveness_filtered_validators(current_height)
            
            if liveness_filtered_validators:
                validators_for_selection = liveness_filtered_validators
                print(f"🔍 Liveness filter: {len(validators_for_selection)} validators passed 3-stage filter")
            else:
                # Fallback: Use all registered active|genesis validators (prevents deadlock during genesis)
                validators_for_selection = set(
                    v for v, val in self.validator_registry.items()
                    if isinstance(val, dict) and val.get('status') in ('active', 'genesis')
                )
                # Suppress spam: only log this warning once per 100 heights
                if current_height % 100 == 0:
                    print(f"⚠️  No liveness data - using all {len(validators_for_selection)} registered (active|genesis) validators")
        
        if not validators_for_selection:
            return None
        
        # Select committee for this epoch from recently active validators
        committee = self.attestation_manager.select_committee(current_epoch, validators_for_selection)
        
        if not committee:
            return None
        
        # Generate epoch seed from finalized blocks
        epoch_start_height = self.attestation_manager.get_epoch_start_block(current_epoch)
        
        if epoch_start_height == 0:
            seed_block = self.get_block_by_height(0)
            if not seed_block:
                return None
            seed_source_hash = seed_block.block_hash
        elif epoch_start_height <= len(self.blocks):
            seed_block = self.get_block_by_height(epoch_start_height - 1)
            if not seed_block:
                return None
            seed_source_hash = seed_block.block_hash
        else:
            # CRITICAL FIX (ChatGPT): Fallback for future epochs
            # When wall-clock slot is ahead of chain height, epoch start block doesn't exist yet
            # Use latest block as deterministic seed so VRF can still select proposers
            # This keeps all nodes deterministic while allowing block production to continue
            seed_block = self.get_latest_block()
            if not seed_block:
                return None
            seed_source_hash = seed_block.block_hash
        
        # Generate epoch seed (deterministic across all nodes)
        epoch_seed = self.vrf_manager.generate_epoch_seed(
            epoch_number=current_epoch,
            finalized_block_hash=seed_source_hash,
            attestation_data=""
        )
        
        # Get ordered proposer queue (primary + fallbacks) sorted by VRF score
        proposer_queue = self.vrf_manager.get_ordered_proposer_queue(
            block_height=current_height,
            epoch_number=current_epoch,
            epoch_seed=epoch_seed,
            committee=committee,
            get_public_key_func=self.get_validator_public_key
        )
        
        if not proposer_queue:
            return None
        
        # Cache proposer queue for fallback selection
        if not hasattr(self, 'proposer_queues'):
            self.proposer_queues = {}
        self.proposer_queues[current_height] = proposer_queue
        
        # ROUND-BASED PROPOSER SELECTION: Use current round to select from VRF queue
        # Round 0 = primary proposer (first in queue)
        # Round 1+ = fallback proposers (rotate through queue deterministically)
        current_round = self.get_current_round(current_height)
        proposer_index = current_round % len(proposer_queue)
        selected = proposer_queue[proposer_index]
        
        if current_round > 0:
            print(f"🎲 VRF proposer [height {current_height}, round {current_round}]: {selected[:20]}... (fallback #{current_round}, committee: {len(committee)})")
        else:
            print(f"🎲 VRF proposer [height {current_height}]: {selected[:20]}... (primary, committee: {len(committee)})")
        
        return selected
    
    def select_proposer_for_slot(self, slot: int) -> Optional[str]:
        """
        Slot-based VRF proposer selection (ChatGPT Fix B).
        
        CRITICAL: Use this for slot-based consensus, NOT select_proposer_vrf_based().
        This ensures consistent selection during both block creation and validation.
        
        Args:
            slot: Slot number (time-based, monotonically increasing)
            
        Returns:
            Address of rank-0 proposer for this slot, or None if no validators available
        """
        ranked = self.get_ranked_proposers_for_slot(slot, num_ranks=1)
        if not ranked:
            return None
        
        selected = ranked[0]
        print(f"🎲 VRF proposer [slot {slot}]: {selected[:20]}...")
        return selected
    
    def get_ranked_proposers_for_slot(self, slot: int, num_ranks: int = 3) -> list:
        """
        Returns top-N ranked proposers for this slot (primary + fallbacks).
        Deterministic given (slot, epoch_seed, committee).
        
        This is the CANONICAL slot-based selection API (ChatGPT Fix B).
        
        CRITICAL FIX (Architect): Use current chain height for epoch/validator lookup,
        NOT slot number. When slots are ahead of height (catch-up), slot-based epochs
        don't have validator checkpoints yet, causing fallback to genesis validators only.
        
        Args:
            slot: Slot number (time-based)
            num_ranks: Number of ranked proposers to return (default 3 for 3 sub-windows)
            
        Returns:
            List of validator addresses ranked 0, 1, 2, ... (primary + fallbacks)
        """
        # CRITICAL: Use current chain height for epoch, not slot
        # This ensures we use the actual validator set from the chain state
        current_height = len(self.blocks) - 1
        current_epoch = self.attestation_manager.get_epoch_number(current_height)
        
        # Get seed from finalized checkpoint or latest block (ChatGPT Fix D)
        seed_block = self.get_latest_block()
        if not seed_block:
            return []
        
        epoch_seed = self.vrf_manager.generate_epoch_seed(current_epoch, seed_block.block_hash)
        
        # Get liveness-filtered validators based on current height
        liveness_filtered_validators = self._get_liveness_filtered_validators(current_height)
        
        if liveness_filtered_validators:
            validators_for_selection = liveness_filtered_validators
        else:
            validators_for_selection = set(
                v for v, val in self.validator_registry.items()
                if isinstance(val, dict) and val.get('status') in ('active', 'genesis')
            )
        
        if not validators_for_selection:
            return []
        
        # Select committee for current epoch (based on height, not slot)
        committee = self.attestation_manager.select_committee(current_epoch, validators_for_selection)
        
        if not committee:
            return []
        
        # Get ordered proposer queue using VRF (deterministic permutation)
        proposer_queue = self.vrf_manager.get_ordered_proposer_queue(
            block_height=slot,  # Pass SLOT, not height
            epoch_number=current_epoch,
            epoch_seed=epoch_seed,
            committee=committee,
            get_public_key_func=self.get_validator_public_key
        )
        
        if not proposer_queue:
            return []
        
        # Cache for fallback reference
        if not hasattr(self, 'proposer_queues'):
            self.proposer_queues = {}
        self.proposer_queues[slot] = proposer_queue
        
        # Return top-N ranked proposers
        ranked = proposer_queue[:num_ranks]
        return ranked
    
    def get_fallback_proposer(self, block_height: int, failed_proposer: Optional[str] = None) -> Optional[str]:
        """
        Get fallback proposer when primary proposer fails to produce a block.
        
        This method uses the cached ordered proposer queue to deterministically select
        the next validator in line when a proposer times out (6 seconds without block).
        
        All nodes compute the same fallback proposer from the same blockchain state,
        ensuring consensus on who should propose when the primary fails.
        
        Args:
            block_height: Block height that needs a proposer
            failed_proposer: Address of proposer who failed (to skip in queue), or None
        
        Returns:
            Address of fallback proposer, or None if no validators available
        
        DETERMINISM: All nodes derive the same fallback from cached proposer queue.
        TIMEOUT SAFETY: If primary proposer is offline, network continues with next validator.
        """
        # Check if we have a cached proposer queue for this height
        if not hasattr(self, 'proposer_queues'):
            self.proposer_queues = {}
        
        proposer_queue = self.proposer_queues.get(block_height)
        
        if not proposer_queue:
            # Queue not cached - recompute using select_proposer_vrf_based
            # This will populate the cache
            primary = self.select_proposer_vrf_based(block_height)
            if not primary:
                return None
            proposer_queue = self.proposer_queues.get(block_height, [])
        
        if not proposer_queue:
            return None
        
        # If no failed proposer specified, return primary (first in queue)
        if not failed_proposer:
            return proposer_queue[0]
        
        # Find failed proposer in queue and return next validator
        try:
            failed_index = proposer_queue.index(failed_proposer)
            # Return next validator in queue (wrap around if at end)
            next_index = (failed_index + 1) % len(proposer_queue)
            fallback = proposer_queue[next_index]
            
            print(f"⏭️  Fallback proposer [height {block_height}]: {fallback[:20]}... (primary {failed_proposer[:20]}... timed out)")
            return fallback
        except ValueError:
            # Failed proposer not in queue - return primary
            return proposer_queue[0]
    
    def update_proposer_priorities_after_commit(self, block_height: int):
        """
        Update proposer priorities after a block is committed (Tendermint algorithm).
        
        This MUST be called after add_block() succeeds, ensuring all nodes update
        priorities at the same time (when committing the same block).
        
        CRITICAL: Excludes genesis validators - they are placeholders with no private keys.
        
        Algorithm:
        1. Increment ALL active validators' priorities by their voting_power
        2. Select validator with highest priority (this was the proposer)
        3. Decrement that validator's priority by total_voting_power
        
        Args:
            block_height: Height of the block just committed
        """
        # Get active validators at this height
        active_validators = []
        total_voting_power = 0
        genesis_addrs = set(config.GENESIS_VALIDATORS.keys())
        
        for addr, data in self.validator_registry.items():
            if isinstance(data, dict):
                status = data.get('status')
                activation_height = data.get('activation_height', 0)
                voting_power = data.get('voting_power', 1)
                
                # Only include if active AND past activation height AND not genesis
                if (status == 'active' and 
                    block_height >= activation_height and 
                    addr not in genesis_addrs):
                    active_validators.append({
                        'address': addr,
                        'voting_power': voting_power,
                        'priority': data.get('proposer_priority', 0)
                    })
                    total_voting_power += voting_power
        
        if not active_validators:
            return
        
        # Step 1: Increment ALL validators' priorities by their voting power
        for validator in active_validators:
            addr = validator['address']
            # Initialize proposer_priority if missing (for old validators)
            if 'proposer_priority' not in self.validator_registry[addr]:
                self.validator_registry[addr]['proposer_priority'] = 0
            self.validator_registry[addr]['proposer_priority'] += validator['voting_power']
            validator['priority'] = self.validator_registry[addr]['proposer_priority']
        
        # Step 2: Select validator with highest priority (this was the proposer for this block)
        selected_validator = max(active_validators, 
                                key=lambda v: (v['priority'], -ord(v['address'][0])))
        
        # Step 3: Decrement selected validator's priority by total voting power
        sel_addr = selected_validator['address']
        # Initialize if missing (safety check)
        if 'proposer_priority' not in self.validator_registry[sel_addr]:
            self.validator_registry[sel_addr]['proposer_priority'] = 0
        self.validator_registry[sel_addr]['proposer_priority'] -= total_voting_power
    
    def activate_pending_validators(self, current_height: int):
        """
        Activate validators that have reached their activation height.
        
        This implements Tendermint's 2-block activation delay, ensuring all nodes
        apply validator set changes at the same deterministic height.
        
        Args:
            current_height: Current blockchain height
        """
        activated = []
        
        for addr, data in self.validator_registry.items():
            if isinstance(data, dict):
                status = data.get('status')
                activation_height = data.get('activation_height', 0)
                
                # Activate if pending and activation height reached
                if status == 'pending' and current_height >= activation_height:
                    data['status'] = 'active'
                    
                    # CRITICAL: Synchronize validator_economics state with registry
                    self.validator_economics.mark_active(addr)
                    
                    # Add to validator set
                    if addr not in self.validator_set:
                        self.validator_set.append(addr)
                    
                    activated.append(addr)
        
        if activated:
            print(f"🔓 Activated {len(activated)} pending validator(s) at height {current_height}:")
            for addr in activated:
                print(f"   • {addr}")
    
    def deregister_validator(self, address: str) -> bool:
        """
        Remove validator (voluntary exit or timeout).
        Keeps in registry for history but marks as inactive.
        Validator must request withdrawal separately to get deposit back.
        """
        if address not in self.validator_registry:
            return False
        
        # Mark as inactive (keep in registry for history)
        if isinstance(self.validator_registry[address], dict):
            self.validator_registry[address]['status'] = 'inactive'
            self.validator_registry[address]['deregistered_at'] = time.time()
        
        # Remove from active set
        if address in self.validator_set:
            self.validator_set.remove(address)
        
        self.save_state()
        
        print(f"⚠️  Validator deregistered: {address}")
        print(f"   Status: inactive (must request deposit withdrawal separately)")
        print(f"   Total active validators: {len(self.get_active_validators())}")
        
        return True
    
    def slash_validator(self, address: str, reason: str, percentage: int) -> bool:
        """
        Slash validator deposit for misbehavior.
        
        CRITICAL SECURITY: Economic punishment for protocol violations.
        
        Args:
            address: Validator address to slash
            reason: Reason for slashing
            percentage: Percentage of deposit to slash (0-100)
        
        Returns:
            True if slashing successful
        """
        if address not in self.validator_registry:
            print(f"Cannot slash {address}: not a registered validator")
            return False
        
        # Slash via economics manager
        success, slashed_amount = self.validator_economics.slash_validator(address, reason, percentage)
        
        if success and slashed_amount > 0:
            # Slashed funds are burned (removed from supply)
            # They do NOT go to proposer or anyone else - this prevents incentive to slash unfairly
            
            # Check if deposit fell below minimum
            remaining_deposit = self.validator_economics.get_validator_deposit(address)
            if remaining_deposit < self.validator_economics.MIN_DEPOSIT_PALS:
                print(f"⚠️  Validator {address} deposit below minimum - forcing deregistration")
                self.deregister_validator(address)
            
            self.save_state()
            return True
        
        return False
    
    def request_validator_withdrawal(self, address: str) -> bool:
        """
        Request deposit withdrawal after deregistration.
        
        Args:
            address: Validator address
        
        Returns:
            True if request successful
        """
        current_height = len(self.blocks)
        success, message = self.validator_economics.request_withdrawal(address, current_height)
        
        if success:
            self.save_state()
            print(f"✅ {message}")
            return True
        else:
            print(f"❌ Withdrawal request failed: {message}")
            return False
    
    def process_validator_withdrawal(self, address: str) -> bool:
        """
        Process deposit withdrawal (after waiting period).
        
        Args:
            address: Validator address
        
        Returns:
            True if withdrawal successful
        """
        current_height = len(self.blocks)
        success, amount, message = self.validator_economics.process_withdrawal(address, current_height)
        
        if success:
            # Return deposit to validator's balance
            current_balance = self.get_balance(address)
            self.balances[address] = current_balance + amount
            
            self.save_state()
            print(f"✅ {message}")
            print(f"   New balance: {self.balances[address] / config.PALS_PER_TMPL} {config.SYMBOL}")
            return True
        else:
            print(f"❌ Withdrawal failed: {message}")
            return False
    
    def get_validator_public_key(self, address: str) -> Optional[str]:
        """Get public key for a validator (supports both old and new format)"""
        data = self.validator_registry.get(address)
        if data is None:
            return None
        
        # New format: dict with 'public_key' field
        if isinstance(data, dict):
            return data.get('public_key')
        
        # Old format: public key stored directly as string
        return data
    
    def is_validator_registered(self, address: str) -> bool:
        """Check if address is a registered validator"""
        return address in self.validator_registry
    
    def get_validator_count(self) -> int:
        """Get total number of active validators"""
        return len(self.get_active_validators())
    
    def get_validator_info(self, address: str) -> Optional[Dict]:
        """Get full validator information with LIVE status based on recent attestations"""
        data = self.validator_registry.get(address)
        if data is None:
            return None
        
        # Determine ACTUAL validator status based on recent attestations
        # Validator is only 'active' if they have recent attestations (actually online)
        active_validators = set(self.get_active_validators())
        actual_status = 'active' if address in active_validators else 'offline'
        
        # Convert old format to new format for consistency
        if isinstance(data, str):
            return {
                'public_key': data,
                'device_id': 'legacy',
                'status': actual_status,
                'registered_at': 0
            }
        
        # New format: override status with actual liveness check
        result = data.copy()
        result['status'] = actual_status
        return result
    
    def verify_chain(self) -> bool:
        if len(self.blocks) == 0:
            return True
            
        for i in range(1, len(self.blocks)):
            current = self.blocks[i]
            previous = self.blocks[i - 1]
            
            if current.previous_hash != previous.block_hash:
                print(f"Chain verification failed at block {i}: previous_hash mismatch")
                return False
            
            if current.block_hash != current.calculate_hash():
                print(f"Chain verification failed at block {i}: hash mismatch")
                return False
        
        return True
    
    def handle_alternative_chain(self, alternative_chain: List[Block]) -> Tuple[bool, str]:
        """
        Handle an alternative blockchain (e.g., from network partition recovery).
        
        This method:
        1. Validates the alternative chain
        2. Compares it with current chain using fork-choice rule
        3. Reorganizes if alternative chain is better
        
        Args:
            alternative_chain: List of blocks representing alternative blockchain
        
        Returns:
            (success, message) tuple
        """
        # Validate alternative chain continuity
        valid, reason = self.fork_choice.validate_chain_continuity(alternative_chain)
        if not valid:
            return (False, f"Alternative chain invalid: {reason}")
        
        # Compare chains using fork-choice rule
        comparison = self.fork_choice.compare_chains(self.blocks, alternative_chain)
        
        if comparison >= 0:
            # Current chain is better or equal
            return (False, "Current chain is canonical - no reorganization needed")
        
        # Alternative chain is better - attempt reorganization
        return self.reorganize_to_chain(alternative_chain)
    
    def reorganize_to_chain(self, new_chain: List[Block]) -> Tuple[bool, str]:
        """
        Reorganize blockchain to follow a different (better) chain.
        
        This implements fork resolution:
        1. Validates new chain is better than current
        2. Finds fork point
        3. Removes blocks after fork point
        4. Adds blocks from new chain
        5. Returns transactions to mempool
        
        Args:
            new_chain: The new canonical blockchain
        
        Returns:
            (success, message) tuple
        """
        # Get reorganization plan
        plan = self.fork_choice.get_reorganization_plan(self.blocks, new_chain)
        
        if plan is None:
            return (False, "Reorganization not allowed or not beneficial")
        
        fork_height = plan['fork_height']
        blocks_to_add = plan['blocks_to_add']
        
        print(f"🔄 Starting chain reorganization at height {fork_height}")
        
        # Step 1: Rollback to fork point
        rollback_success = self._rollback_to_height(fork_height - 1)
        if not rollback_success:
            return (False, f"Failed to rollback to height {fork_height - 1}")
        
        # Step 2: Add new blocks one by one using HISTORICAL VRF VALIDATION
        # CRITICAL SECURITY: VRF proposer ordering is CORE SECURITY in TIMPAL.
        # Unlike Bitcoin (PoW) or Ethereum (PoS cumulative stake), TIMPAL has NO
        # alternative security mechanism once VRF is bypassed.
        #
        # ATTACK SCENARIO (if VRF skipped): Malicious validator could grind
        # arbitrary block sequences offline and force honest nodes to reorganize
        # onto the forged chain.
        #
        # SOLUTION: Use historical state reconstruction from HistoricalStateLog:
        # 1. Load historical validator frame for each block height
        # 2. Reconstruct exact VRF proposer queue using stored epoch seed + liveness data
        # 3. Verify block.proposer matches expected proposer from historical state
        # 4. Only accept blocks with valid VRF proposer selection
        #
        # FAIL-SAFE: If historical state is missing, REJECT the reorg rather than
        # silently downgrade security. This indicates ledger corruption that must
        # be addressed via resync from peers.
        for block in blocks_to_add:
            # Check if we have historical state for this height
            if not self.historical_state_log.has_height(block.height - 1) and block.height > 0:
                print(f"❌ SECURITY: Missing historical state for height {block.height - 1}")
                print(f"   Cannot validate VRF proposer selection during reorg")
                print(f"   Aborting reorg - please resync from peers")
                return (False, f"Missing historical state for VRF validation at height {block.height}")
            
            # Use historical validators for VRF validation during reorg
            success = self.add_block(block, skip_proposer_check=False, use_historical_validators=True)
            if not success:
                # Reorganization failed - state is corrupted
                print(f"❌ CRITICAL: Reorganization failed when adding block {block.height}")
                print(f"   Blockchain may be in inconsistent state!")
                return (False, f"Failed to add block at height {block.height} during reorganization")
        
        print(f"✅ Chain reorganization complete: now at height {len(self.blocks) - 1}")
        return (True, f"Reorganized to new chain, now at height {len(self.blocks) - 1}")
    
    def _rollback_to_height(self, target_height: int) -> bool:
        """
        Rollback blockchain to a specific height.
        
        This is used during chain reorganization to undo blocks.
        
        CRITICAL FOR VRF SECURITY:
        - Restores historical validator state for deterministic proposer validation
        - Restores attestation manager state for correct liveness filtering
        - Clears historical state log above target height
        
        Args:
            target_height: Height to rollback to (minimum 0 to preserve genesis)
        
        Returns:
            True if successful, False otherwise
        """
        if target_height >= len(self.blocks):
            print(f"Cannot rollback to height {target_height} - current height is {len(self.blocks) - 1}")
            return False
        
        # CRITICAL FIX: Never rollback below genesis (height 0)
        # Previous bug: allowed target_height=-1, which removed genesis block
        # This caused reorg to fail with "expected block 0, got block 1"
        if target_height < 0:
            print(f"❌ Cannot rollback below genesis (target_height={target_height})")
            print(f"   Clamping to height 0 to preserve genesis block")
            target_height = 0
        
        current_height = len(self.blocks) - 1
        blocks_to_remove = current_height - target_height
        
        print(f"📤 Rolling back {blocks_to_remove} blocks from height {current_height} to {target_height}")
        
        # STEP 1: Restore AttestationManager state from historical snapshot
        # This is CRITICAL for correct VRF proposer selection during replay
        # DETERMINISTIC SERIALIZATION FIX (Dec 2025): export_snapshot() now uses
        # deterministic serialization (sorted lists, string keys), so hash verification
        # should always pass for snapshots created with the current code version.
        # If hash verification fails, it indicates data corruption or incompatible version.
        am_snapshot = self.historical_state_log.get_am_snapshot(target_height)
        if am_snapshot:
            restore_success = self.attestation_manager.import_snapshot(am_snapshot)
            if restore_success:
                print(f"✅ AttestationManager state restored to height {target_height}")
            else:
                # Hash mismatch indicates data corruption or snapshot from incompatible version
                # This should not happen with snapshots created by current code version
                print(f"⚠️ Failed to restore AttestationManager state - using rollback_to_height fallback")
                self.attestation_manager.rollback_to_height(target_height)
        else:
            # Fallback: use lightweight rollback if no snapshot available
            self.attestation_manager.rollback_to_height(target_height)
            print(f"ℹ️  No AM snapshot at height {target_height} - used lightweight rollback")
        
        # STEP 2: Restore VRF manager epoch seeds from snapshot or epoch snapshot
        # This is CRITICAL for deterministic proposer validation
        # SECURITY: Only restore non-empty epoch seeds to avoid corrupting VRF state
        epoch_seed_restored = False
        
        # Try AM snapshot first
        if am_snapshot and 'epoch_seed' in am_snapshot:
            stored_epoch_seed = am_snapshot.get('epoch_seed')
            stored_epoch_number = am_snapshot.get('epoch_number')
            if stored_epoch_seed and len(stored_epoch_seed) > 0 and stored_epoch_number is not None:
                self.vrf_manager.restore_epoch_seed(stored_epoch_number, stored_epoch_seed)
                print(f"✅ VRF epoch seed restored for epoch {stored_epoch_number}")
                epoch_seed_restored = True
        
        # Fallback: Derive epoch_seed from nearest epoch snapshot
        if not epoch_seed_restored:
            epoch_snap, snap_height = self.historical_state_log.get_nearest_epoch_snapshot(target_height)
            if epoch_snap and epoch_snap.epoch_seed and len(epoch_snap.epoch_seed) > 0:
                self.vrf_manager.restore_epoch_seed(epoch_snap.epoch_number, epoch_snap.epoch_seed)
                print(f"✅ VRF epoch seed restored from epoch snapshot {epoch_snap.epoch_number}")
                epoch_seed_restored = True
        
        if not epoch_seed_restored:
            print(f"❌ CRITICAL: No valid epoch seed found for height {target_height}")
            print(f"   This is required for deterministic proposer validation")
            print(f"   Historical data may be incomplete - requires migration or resync")
            print(f"   ABORTING ROLLBACK to prevent chain corruption")
            return False
        
        # STRICT: AM snapshot MUST exist and have required liveness data
        if am_snapshot is None:
            print(f"❌ CRITICAL: No AM snapshot found for height {target_height}")
            print(f"   This is required for deterministic proposer validation")
            print(f"   Historical data may be incomplete - requires migration or resync")
            print(f"   ABORTING ROLLBACK to prevent chain corruption")
            return False
        
        if 'combined_liveness_set' not in am_snapshot:
            print(f"❌ CRITICAL: AM snapshot at height {target_height} missing combined_liveness_set")
            print(f"   This is required for deterministic proposer validation")
            print(f"   Historical data may be incomplete - requires migration or resync")
            print(f"   ABORTING ROLLBACK to prevent chain corruption")
            return False
        
        # STEP 3: Restore validator frame pointers for delta computation
        frame = self.historical_state_log.get_frame(target_height)
        if frame:
            self._previous_validator_frame = frame
            
            # CRITICAL: Restore validator registry directly from frame
            # This ensures slashing/status changes are correctly restored
            # instead of only replaying registrations from transactions
            self._restore_validator_registry_from_frame(frame)
            print(f"✅ Validator registry restored from frame at height {target_height}")
        else:
            self._previous_validator_frame = None
            print(f"⚠️ No validator frame at height {target_height} - will rebuild from transactions")
        
        record = self.historical_state_log.get_record(target_height)
        if record:
            self._previous_historical_record = record
        else:
            self._previous_historical_record = None
        
        # STEP 4: Clear historical state log above target height
        removed_count = self.historical_state_log.remove_above_height(target_height)
        if removed_count > 0:
            print(f"🗑️  Cleared {removed_count} historical state records above height {target_height}")
        
        # STEP 5: Remove blocks from end
        self.blocks = self.blocks[:target_height + 1]
        
        # STEP 6: Rebuild balances/nonces from remaining blocks
        # Note: Validator registry is already restored from frame (STEP 3)
        # This only rebuilds financial state, not validator state
        self._rebuild_financial_state_from_blocks()
        
        # STEP 7: Verify validator state matches blocks after rollback
        # CRITICAL FIX: Historical frames may be stale (recorded before later registrations)
        # This ensures validators registered AFTER the frame's height are not lost
        self._verify_and_repair_validator_state()
        
        print(f"✅ Rollback complete - now at height {len(self.blocks) - 1}")
        return True
    
    def _restore_validator_registry_from_frame(self, frame):
        """
        Restore validator registry directly from a ValidatorStateFrame.
        
        This is CRITICAL for correct rollback because it restores ALL validator
        state including slashing, status changes, and deregistration - which
        cannot be reconstructed by only replaying validator_registration transactions.
        
        Args:
            frame: ValidatorStateFrame containing ordered_validators list
        """
        self.validator_registry = {}
        
        for validator_entry in frame.ordered_validators:
            self.validator_registry[validator_entry.address] = {
                'public_key': validator_entry.public_key,
                'device_id': validator_entry.device_id,
                'activation_height': validator_entry.activation_height,
                'stake': validator_entry.deposit_amount,
                'status': validator_entry.status,
                'registration_height': validator_entry.registration_height,
                'voting_power': validator_entry.voting_power,
                'proposer_priority': validator_entry.proposer_priority
            }
        
        # Update validator set checkpoint for this height
        self.validator_set_checkpoints[frame.block_height] = set(self.validator_registry.keys())
    
    def _rebuild_financial_state_from_blocks(self):
        """
        Rebuild only financial state (balances, nonces, emissions) from blocks.
        
        This is a lighter-weight rebuild that preserves validator registry
        (which is restored separately from ValidatorStateFrame during rollback).
        
        Used when validator state was already restored from historical frame.
        """
        print("🔨 Rebuilding financial state from blocks...")
        
        # Reset financial state only (NOT validator registry)
        self.balances = {}
        self.nonces = {}
        self.total_emitted_pals = 0
        
        # Replay all blocks for financial state
        for block in self.blocks:
            # Process reward distribution
            if hasattr(block, 'reward_distribution') and block.reward_distribution:
                for address, reward in block.reward_distribution.items():
                    if address not in self.balances:
                        self.balances[address] = 0
                    self.balances[address] += reward
                    self.total_emitted_pals += reward
            
            # Process transactions
            for tx in block.transactions:
                if tx.tx_type == "validator_registration":
                    # For registration, only process the financial aspect (stake + fee)
                    if tx.sender not in self.balances:
                        self.balances[tx.sender] = 0
                    self.balances[tx.sender] -= (tx.amount + tx.fee) if hasattr(tx, 'fee') else tx.amount
                else:
                    # Regular transfer transaction
                    if tx.sender not in self.balances:
                        self.balances[tx.sender] = 0
                    self.balances[tx.sender] -= (tx.amount + tx.fee)
                    
                    if tx.recipient not in self.balances:
                        self.balances[tx.recipient] = 0
                    self.balances[tx.recipient] += tx.amount
                
                # Update nonce - CRITICAL: must be tx.nonce + 1 to match add_block behavior
                # The nonce stored is the NEXT expected nonce, not the current transaction's nonce
                self.nonces[tx.sender] = tx.nonce + 1
        
        emitted_tmpl = self.total_emitted_pals / config.PALS_PER_TMPL
        print(f"✅ Financial state rebuilt: {len(self.balances)} accounts, "
              f"{emitted_tmpl:,.8f} {config.SYMBOL} emitted")
    
    def _rebuild_state_from_blocks(self):
        """
        Rebuild full ledger state from blocks including validator registry.
        
        Used after chain reorganization to ensure state matches blockchain.
        
        CRITICAL: This method rebuilds ALL state that was modified by blocks:
        - Balances (from transactions and rewards)
        - Nonces (from transactions)
        - Emissions (from block rewards)
        - Validator registry (from validator_registration transactions)
        """
        print("🔨 Rebuilding ledger state from blocks...")
        
        # Reset state
        self.balances = {}
        self.nonces = {}
        self.total_emitted_pals = 0
        self.validator_registry = {}
        
        # Clear existing validator checkpoints above height 0
        # Keep genesis checkpoint if it exists
        genesis_checkpoint = self.validator_set_checkpoints.get(0)
        self.validator_set_checkpoints = {}
        if genesis_checkpoint:
            self.validator_set_checkpoints[0] = genesis_checkpoint
        
        # Replay all blocks
        for block in self.blocks:
            # Process reward distribution
            if hasattr(block, 'reward_distribution') and block.reward_distribution:
                for address, reward in block.reward_distribution.items():
                    if address not in self.balances:
                        self.balances[address] = 0
                    self.balances[address] += reward
                    self.total_emitted_pals += reward
            
            # Process transactions
            for tx in block.transactions:
                # Handle validator registration transactions
                if tx.tx_type == "validator_registration":
                    self._process_validator_registration_for_rebuild(tx, block.height)
                else:
                    # Regular transfer transaction
                    # Update sender
                    if tx.sender not in self.balances:
                        self.balances[tx.sender] = 0
                    self.balances[tx.sender] -= (tx.amount + tx.fee)
                    
                    # Update recipient
                    if tx.recipient not in self.balances:
                        self.balances[tx.recipient] = 0
                    self.balances[tx.recipient] += tx.amount
                
                # Update nonce - CRITICAL: must be tx.nonce + 1 to match add_block behavior
                # The nonce stored is the NEXT expected nonce, not the current transaction's nonce
                self.nonces[tx.sender] = tx.nonce + 1
        
        # Create checkpoint for final height
        if self.blocks:
            final_height = len(self.blocks) - 1
            if final_height > 0 and final_height not in self.validator_set_checkpoints:
                self.validator_set_checkpoints[final_height] = set(self.validator_registry.keys())
        
        emitted_tmpl = self.total_emitted_pals / config.PALS_PER_TMPL
        print(f"✅ State rebuilt: {len(self.balances)} accounts, "
              f"{len(self.validator_registry)} validators, "
              f"{emitted_tmpl:,.8f} {config.SYMBOL} emitted")
    
    def _process_validator_registration_for_rebuild(self, tx, block_height: int):
        """
        Process a validator registration transaction during state rebuild.
        
        This replicates the validator registration logic from add_block
        but without validation (since we're replaying already-validated blocks).
        
        CRITICAL FIX: Must also add validator to validator_set, not just registry.
        Without this, syncing nodes would have validators in registry but not in
        validator_set, causing them to not be included in proposer selection.
        """
        validator_address = tx.sender
        
        # Determine status based on block height (genesis vs normal)
        if block_height == 0:
            status = 'genesis'
            activation_height = 0
        else:
            # For rebuild, we set status to 'active' immediately since we're
            # replaying blocks that already passed the activation delay
            status = 'active'
            activation_height = block_height + 2  # Original activation height
        
        # Register the validator
        self.validator_registry[validator_address] = {
            'public_key': tx.public_key if hasattr(tx, 'public_key') else '',
            'device_id': tx.device_id if hasattr(tx, 'device_id') else '',
            'activation_height': activation_height,
            'registration_height': block_height,
            'stake': tx.amount if hasattr(tx, 'amount') else 0,
            'status': status,
            'registered_at': tx.timestamp if hasattr(tx, 'timestamp') else 0,
            'deposit_amount': 0,
            'voting_power': 1,
            'proposer_priority': 0
        }
        
        # CRITICAL FIX: Add to validator_set for consensus membership
        # This was missing before, causing syncing nodes to not recognize validators
        if validator_address not in self.validator_set:
            self.validator_set.append(validator_address)
        
        # Mark active in economics system
        self.validator_economics.mark_active(validator_address)
        
        # Update balance (stake is locked, fee is deducted)
        if validator_address not in self.balances:
            self.balances[validator_address] = 0
        self.balances[validator_address] -= (tx.amount + tx.fee) if hasattr(tx, 'fee') else tx.amount
        
        print(f"✅ Validator rebuilt: {validator_address[:20]}... (status={status})")
    
    def add_finality_checkpoint(self, height: int, block_hash: str):
        """
        Add a finality checkpoint at the given height.
        
        Checkpoints prevent deep chain reorganizations and protect against
        long-range attacks.
        """
        self.fork_choice.add_finality_checkpoint(height, block_hash)
    
    def is_block_finalized(self, height: int) -> bool:
        """Check if a block at given height is finalized (cannot be reorganized)."""
        return self.fork_choice.is_finalized(height)
    
    def _validate_timeout_certificate(self, cert_tx: Transaction, block_height: int) -> bool:
        """
        Validate a timeout certificate transaction with 2/3 voting power quorum.
        
        This is CRITICAL for consensus safety - invalid certificates must be rejected
        to prevent malicious validators from arbitrarily advancing rounds.
        
        Args:
            cert_tx: Transaction containing timeout certificate data
            block_height: Height of block containing this certificate
        
        Returns:
            True if certificate is valid, False otherwise
        """
        from timeout import TimeoutCertificate, TimeoutVote
        
        if not cert_tx.timeout_cert_data:
            print("REJECT CERT: No timeout_cert_data in transaction")
            return False
        
        cert_data = cert_tx.timeout_cert_data
        
        # Extract certificate fields
        cert_height = cert_data.get('height')
        cert_round = cert_data.get('round')
        cert_proposer = cert_data.get('proposer')
        votes_data = cert_data.get('votes', [])
        aggregated_power = cert_data.get('aggregated_power', 0)
        
        # Validate certificate is for correct height
        if cert_height != block_height:
            print(f"REJECT CERT: Certificate height {cert_height} != block height {block_height}")
            return False
        
        # Validate certificate is for current round (prevents old certificates from being reused)
        current_round = self.get_current_round(block_height)
        if cert_round != current_round:
            print(f"REJECT CERT: Certificate round {cert_round} != current round {current_round}")
            return False
        
        # Prevent replay attacks: Check certificate hasn't been used before
        cert_hash = cert_tx.tx_hash
        if cert_hash in self.used_timeout_certificates:
            print(f"REJECT CERT: Certificate {cert_hash[:16]}... already used (replay attack)")
            return False
        
        # Must have at least one vote
        if not votes_data or len(votes_data) == 0:
            print("REJECT CERT: No votes in certificate")
            return False
        
        # Verify all votes and calculate actual voting power
        actual_voting_power = 0
        verified_voters = set()
        
        for vote_data in votes_data:
            # Reconstruct TimeoutVote object to verify signature
            vote = TimeoutVote.from_dict(vote_data)
            
            # Verify vote signature
            if not vote.verify():
                print(f"REJECT CERT: Invalid vote signature from {vote.voter[:20]}...")
                return False
            
            # Verify vote is for same height/round/proposer as certificate
            if vote.height != cert_height or vote.round != cert_round or vote.proposer != cert_proposer:
                print(f"REJECT CERT: Vote mismatch - vote({vote.height}/{vote.round}) != cert({cert_height}/{cert_round})")
                return False
            
            # Verify voter is a registered validator
            if vote.voter not in self.validator_registry:
                print(f"REJECT CERT: Voter {vote.voter[:20]}... not a registered validator")
                return False
            
            # Prevent duplicate votes
            if vote.voter in verified_voters:
                print(f"REJECT CERT: Duplicate vote from {vote.voter[:20]}...")
                return False
            verified_voters.add(vote.voter)
            
            # Get voter's voting power from economics module
            voter_power = self.validator_economics.get_voting_power(vote.voter)
            actual_voting_power += voter_power
        
        # Calculate total voting power of all active validators
        active_validators = self.get_active_validators()
        total_voting_power = sum(
            self.validator_economics.get_voting_power(addr) 
            for addr in active_validators
        )
        
        if total_voting_power == 0:
            print("REJECT CERT: Total voting power is zero")
            return False
        
        # Verify ≥2/3 quorum (Byzantine fault tolerance threshold)
        required_power = (total_voting_power * 2) // 3  # Integer division for exact 2/3
        if actual_voting_power < required_power:
            print(f"REJECT CERT: Insufficient voting power {actual_voting_power}/{total_voting_power} (need ≥{required_power})")
            return False
        
        # Verify claimed aggregated_power matches calculated power
        if aggregated_power != actual_voting_power:
            print(f"REJECT CERT: Claimed power {aggregated_power} != actual power {actual_voting_power}")
            return False
        
        # Mark certificate as used to prevent replay
        self.used_timeout_certificates.add(cert_hash)
        
        # Clear vote cache for this (height, round, proposer)
        self.clear_timeout_votes(cert_height, cert_round, cert_proposer)
        
        print(f"✅ VALID CERT: {len(verified_voters)} voters, {actual_voting_power}/{total_voting_power} power ({actual_voting_power*100//total_voting_power}%)")
        return True
    
    def select_liveness_committee(self, height: int) -> List[str]:
        """
        Select liveness committee for availability checks at given height.
        
        Committee size: 200-300 validators (constant for scalability)
        Selection: Deterministic from epoch seed + height
        Purpose: Check proposer availability before block time
        
        Args:
            height: Block height for which to select committee
        
        Returns:
            List of validator addresses in committee (sorted for determinism)
        """
        # Check cache first
        if height in self.liveness_committee_cache:
            return self.liveness_committee_cache[height]
        
        # Get current epoch and active validators
        current_epoch = self.attestation_manager.get_epoch_number(height)
        active_validators = self.get_active_validators()
        
        if not active_validators:
            return []
        
        # Committee size: min(300, total_validators) for scalability
        committee_size = min(300, len(active_validators))
        
        # Generate deterministic seed from epoch + height
        epoch_start_height = self.attestation_manager.get_epoch_start_block(current_epoch)
        
        if epoch_start_height == 0:
            seed_block = self.get_block_by_height(0)
        else:
            seed_block = self.get_block_by_height(epoch_start_height - 1)
        
        if not seed_block:
            return []
        
        # Create deterministic seed combining epoch and height
        import hashlib
        seed_data = f"{seed_block.block_hash}{current_epoch}{height}"
        seed_hash = hashlib.sha256(seed_data.encode()).hexdigest()
        seed_int = int(seed_hash, 16)
        
        # Use seed to shuffle validators deterministically
        # All nodes compute same committee from same blockchain state
        validators_list = list(active_validators)
        validators_list.sort()  # Ensure deterministic ordering
        
        # Fisher-Yates shuffle with deterministic seed
        import random
        rng = random.Random(seed_int)
        shuffled = validators_list.copy()
        for i in range(len(shuffled) - 1, 0, -1):
            j = rng.randint(0, i)
            shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
        
        # Select first committee_size validators
        committee = shuffled[:committee_size]
        committee.sort()  # Sort for deterministic comparison
        
        # Cache for performance
        self.liveness_committee_cache[height] = committee
        
        print(f"📋 Liveness committee for height {height}: {len(committee)} members")
        return committee
    
    def get_current_round(self, height: int) -> int:
        """Get the current round number for a given block height"""
        return self.current_round_by_height.get(height, 0)
    
    def increment_round(self, height: int):
        """Increment the round number for a given height (when timeout certificate accepted)"""
        current_round = self.get_current_round(height)
        self.current_round_by_height[height] = current_round + 1
        print(f"⏭️  Round incremented for height {height}: round {current_round} → {current_round + 1}")
    
    def add_timeout_vote(self, vote_data: dict) -> bool:
        """
        Add a timeout vote to the cache for aggregation.
        
        Args:
            vote_data: Dictionary containing TimeoutVote data
        
        Returns:
            True if vote was added, False if duplicate
        """
        height = vote_data['height']
        round_num = vote_data['round']
        proposer = vote_data['proposer']
        voter = vote_data['voter']
        
        # Create cache key
        cache_key = f"{height}_{round_num}_{proposer}"
        
        # Initialize cache for this key if needed
        if cache_key not in self.timeout_votes_cache:
            self.timeout_votes_cache[cache_key] = []
        
        # Check for duplicate vote from same voter
        for existing_vote in self.timeout_votes_cache[cache_key]:
            if existing_vote.get('voter') == voter:
                return False  # Duplicate vote
        
        # Add vote to cache
        self.timeout_votes_cache[cache_key].append(vote_data)
        return True
    
    def get_timeout_votes(self, height: int, round_num: int, proposer: str) -> List[dict]:
        """Get all timeout votes for a specific (height, round, proposer) combination"""
        cache_key = f"{height}_{round_num}_{proposer}"
        return self.timeout_votes_cache.get(cache_key, [])
    
    def clear_timeout_votes(self, height: int, round_num: int, proposer: str):
        """Clear timeout votes for a specific (height, round, proposer) after certificate creation"""
        cache_key = f"{height}_{round_num}_{proposer}"
        if cache_key in self.timeout_votes_cache:
            del self.timeout_votes_cache[cache_key]
    
    @property
    def blockchain(self):
        """Alias for self.blocks to maintain compatibility"""
        return self.blocks
    
    def close(self):
        """Close database connections and cleanup resources"""
        if self._closed:
            return
        
        if self.use_production_storage and self.production_storage:
            self.production_storage.close()
        
        self._closed = True
    
    def __del__(self):
        """Destructor to ensure database is closed"""
        try:
            self.close()
        except:
            pass
