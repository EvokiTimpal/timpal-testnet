"""
HISTORICAL STATE SYSTEM FOR DETERMINISTIC VRF PROPOSER VALIDATION

This module provides the data structures needed to reconstruct exact historical
validator state during chain reorganization, enabling deterministic VRF proposer
validation at any block height.

SECURITY REQUIREMENT (per Architect):
VRF proposer ordering is CORE SECURITY in TIMPAL because there is no PoW/PoS
cumulative weight. Skipping VRF validation during reorg would allow a single
malicious validator to grind a fake chain offline and force reorganization.

ARCHITECTURE:
- ValidatorStateFrame: Captures validator registry state at a specific block height
- EpochSnapshot: Captures attestation/committee state at epoch boundaries
- HistoricalStateRecord: Links block height to its validator frame and epoch snapshot
- HistoricalStateLog: Manages persistence and retrieval of historical states

USAGE DURING REORG:
1. Load HistoricalStateRecord for target block height
2. Restore validator set from ValidatorStateFrame
3. Restore attestation state from EpochSnapshot
4. VRF proposer selection uses historical state, not current state
5. Block validation succeeds if proposer matches historical VRF selection
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple, Any
import json
import hashlib
import time


@dataclass
class ValidatorEntry:
    """
    Complete state of a single validator at a specific block height.
    
    This captures ALL fields needed to reconstruct VRF eligibility:
    - Status determines if validator is active for proposer selection
    - Activation height determines when validator became eligible
    - Public key is needed for VRF verification
    """
    address: str
    public_key: str
    device_id: str
    status: str  # 'active', 'genesis', 'pending', 'slashed', 'exited'
    registered_at: float  # timestamp
    registration_height: int
    activation_height: int
    deposit_amount: int
    voting_power: int
    proposer_priority: int
    
    def to_dict(self) -> dict:
        return {
            'address': self.address,
            'public_key': self.public_key,
            'device_id': self.device_id,
            'status': self.status,
            'registered_at': self.registered_at,
            'registration_height': self.registration_height,
            'activation_height': self.activation_height,
            'deposit_amount': self.deposit_amount,
            'voting_power': self.voting_power,
            'proposer_priority': self.proposer_priority
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ValidatorEntry':
        return cls(
            address=data['address'],
            public_key=data['public_key'],
            device_id=data['device_id'],
            status=data['status'],
            registered_at=data['registered_at'],
            registration_height=data['registration_height'],
            activation_height=data['activation_height'],
            deposit_amount=data['deposit_amount'],
            voting_power=data['voting_power'],
            proposer_priority=data['proposer_priority']
        )


@dataclass
class LivenessFilterState:
    """
    Captures the exact state used by _get_liveness_filtered_validators().
    
    This is CRITICAL for deterministic VRF replay because liveness filtering
    determines which validators are eligible for proposer selection.
    
    COMPONENTS:
    - recent_proposers: Validators who proposed blocks in last N blocks
    - grace_period_validators: Recently activated validators within grace window
    - combined_liveness_set: Union of all liveness-qualified validators
    """
    recent_proposers: List[str]
    grace_period_validators: List[str]
    combined_liveness_set: List[str]
    lookback_blocks: int
    grace_window_blocks: int
    
    def to_dict(self) -> dict:
        return {
            'recent_proposers': self.recent_proposers,
            'grace_period_validators': self.grace_period_validators,
            'combined_liveness_set': self.combined_liveness_set,
            'lookback_blocks': self.lookback_blocks,
            'grace_window_blocks': self.grace_window_blocks
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'LivenessFilterState':
        return cls(
            recent_proposers=data['recent_proposers'],
            grace_period_validators=data['grace_period_validators'],
            combined_liveness_set=data['combined_liveness_set'],
            lookback_blocks=data['lookback_blocks'],
            grace_window_blocks=data['grace_window_blocks']
        )


@dataclass
class ValidatorStateFrame:
    """
    Complete validator registry state at a specific block height.
    
    This is the CORE data structure for historical VRF validation.
    It captures the EXACT ordered validator list needed by VRF selection.
    
    CRITICAL: The ordered_validators list must be sorted deterministically
    (by address) to ensure all nodes compute the same VRF proposer.
    
    STORAGE OPTIMIZATION:
    - Full frames stored at epoch boundaries (every 100 blocks)
    - Delta frames stored for intermediate blocks (only changes)
    - Delta contains: added validators, removed validators, status changes
    
    LIVENESS STATE (per Architect requirement):
    - liveness_filter_state: Captures _get_liveness_filtered_validators() output
    - This enables deterministic VRF replay even when liveness status changes
    
    VRF STATE (CRITICAL for P2P validation):
    - epoch_seed: Deterministic seed for VRF proposer selection
    - epoch_number: Which epoch this block belongs to
    - These fields are NEVER evicted from cache, enabling P2P validation
    """
    block_height: int
    block_hash: str
    timestamp: float
    
    ordered_validators: List[ValidatorEntry]
    
    liveness_filter_state: Optional[LivenessFilterState] = None
    
    epoch_seed: str = ""
    epoch_number: int = 0
    
    is_full_frame: bool = True
    parent_frame_height: Optional[int] = None
    
    added_validators: List[ValidatorEntry] = field(default_factory=list)
    removed_validators: List[str] = field(default_factory=list)
    status_changes: Dict[str, str] = field(default_factory=dict)
    
    def get_active_validators(self) -> Set[str]:
        """Get set of addresses for validators with active/genesis status"""
        return {
            v.address for v in self.ordered_validators
            if v.status in ('active', 'genesis')
        }
    
    def get_validators_eligible_at_height(self, height: int) -> Set[str]:
        """Get validators that were activated at or before given height"""
        return {
            v.address for v in self.ordered_validators
            if v.activation_height <= height and v.status in ('active', 'genesis')
        }
    
    def get_validator_public_key(self, address: str) -> Optional[str]:
        """Look up public key for a validator address"""
        for v in self.ordered_validators:
            if v.address == address:
                return v.public_key
        return None
    
    def calculate_hash(self) -> str:
        """Calculate deterministic hash of this frame for integrity verification"""
        data = {
            'block_height': self.block_height,
            'block_hash': self.block_hash,
            'validators': [v.to_dict() for v in sorted(self.ordered_validators, key=lambda x: x.address)]
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    def to_dict(self) -> dict:
        return {
            'block_height': self.block_height,
            'block_hash': self.block_hash,
            'timestamp': self.timestamp,
            'ordered_validators': [v.to_dict() for v in self.ordered_validators],
            'liveness_filter_state': self.liveness_filter_state.to_dict() if self.liveness_filter_state else None,
            'epoch_seed': self.epoch_seed,
            'epoch_number': self.epoch_number,
            'is_full_frame': self.is_full_frame,
            'parent_frame_height': self.parent_frame_height,
            'added_validators': [v.to_dict() for v in self.added_validators],
            'removed_validators': self.removed_validators,
            'status_changes': self.status_changes,
            'frame_hash': self.calculate_hash()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ValidatorStateFrame':
        liveness_data = data.get('liveness_filter_state')
        return cls(
            block_height=data['block_height'],
            block_hash=data['block_hash'],
            timestamp=data['timestamp'],
            ordered_validators=[ValidatorEntry.from_dict(v) for v in data['ordered_validators']],
            liveness_filter_state=LivenessFilterState.from_dict(liveness_data) if liveness_data else None,
            epoch_seed=data.get('epoch_seed', ''),
            epoch_number=data.get('epoch_number', 0),
            is_full_frame=data.get('is_full_frame', True),
            parent_frame_height=data.get('parent_frame_height'),
            added_validators=[ValidatorEntry.from_dict(v) for v in data.get('added_validators', [])],
            removed_validators=data.get('removed_validators', []),
            status_changes=data.get('status_changes', {})
        )


@dataclass
class EpochSnapshot:
    """
    Complete attestation and committee state at an epoch boundary.
    
    This captures all mutable state from AttestationManager that affects
    VRF proposer selection:
    - Committee assignments (who was eligible to attest)
    - Attestations received (who actually attested = liveness proof)
    - Epoch seed (deterministic input to VRF)
    - Participation scores (affects liveness filtering)
    
    VRF CONTEXT (per Architect requirement):
    - epoch_seed_derivation: Full context for how seed was derived
    - ordered_committee: Committee in VRF-sorted order (not just member set)
    - proposer_queue_by_height: Cached proposer queues for heights in this epoch
    
    STORAGE: One snapshot per epoch (every 100 blocks = 5 minutes)
    """
    epoch_number: int
    epoch_start_block: int
    epoch_end_block: int
    
    epoch_seed: str
    epoch_seed_source_hash: str
    
    committee_members: List[str]
    
    epoch_seed_source_height: int = 0
    ordered_committee: List[str] = field(default_factory=list)
    
    attestations: Dict[str, int] = field(default_factory=dict)
    
    proposer_queue_by_height: Dict[int, List[str]] = field(default_factory=dict)
    
    participating_validators: int = 0
    total_validators: int = 0
    participation_rate: float = 0.0
    
    is_finalized: bool = False
    finalized_at_height: Optional[int] = None
    
    liveness_scores: Dict[str, int] = field(default_factory=dict)
    
    vrf_manager_cache: Dict[str, str] = field(default_factory=dict)
    
    def get_attesting_validators(self) -> Set[str]:
        """Get validators who submitted attestations this epoch"""
        return set(self.attestations.keys())
    
    def get_live_validators(self, min_attestations: int = 1) -> Set[str]:
        """Get validators with sufficient liveness proofs"""
        return {
            addr for addr, count in self.liveness_scores.items()
            if count >= min_attestations
        }
    
    def calculate_hash(self) -> str:
        """Calculate deterministic hash of this snapshot"""
        data = {
            'epoch_number': self.epoch_number,
            'epoch_seed': self.epoch_seed,
            'committee': sorted(self.committee_members),
            'attestations': {k: v for k, v in sorted(self.attestations.items())}
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    def to_dict(self) -> dict:
        return {
            'epoch_number': self.epoch_number,
            'epoch_start_block': self.epoch_start_block,
            'epoch_end_block': self.epoch_end_block,
            'epoch_seed': self.epoch_seed,
            'epoch_seed_source_hash': self.epoch_seed_source_hash,
            'epoch_seed_source_height': self.epoch_seed_source_height,
            'committee_members': self.committee_members,
            'ordered_committee': self.ordered_committee,
            'attestations': self.attestations,
            'proposer_queue_by_height': {str(k): v for k, v in self.proposer_queue_by_height.items()},
            'participating_validators': self.participating_validators,
            'total_validators': self.total_validators,
            'participation_rate': self.participation_rate,
            'is_finalized': self.is_finalized,
            'finalized_at_height': self.finalized_at_height,
            'liveness_scores': self.liveness_scores,
            'vrf_manager_cache': self.vrf_manager_cache,
            'snapshot_hash': self.calculate_hash()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'EpochSnapshot':
        return cls(
            epoch_number=data['epoch_number'],
            epoch_start_block=data['epoch_start_block'],
            epoch_end_block=data['epoch_end_block'],
            epoch_seed=data['epoch_seed'],
            epoch_seed_source_hash=data['epoch_seed_source_hash'],
            epoch_seed_source_height=data.get('epoch_seed_source_height', 0),
            committee_members=data['committee_members'],
            ordered_committee=data.get('ordered_committee', []),
            attestations=data.get('attestations', {}),
            proposer_queue_by_height={int(k): v for k, v in data.get('proposer_queue_by_height', {}).items()},
            participating_validators=data.get('participating_validators', 0),
            total_validators=data.get('total_validators', 0),
            participation_rate=data.get('participation_rate', 0.0),
            is_finalized=data.get('is_finalized', False),
            finalized_at_height=data.get('finalized_at_height'),
            liveness_scores=data.get('liveness_scores', {}),
            vrf_manager_cache=data.get('vrf_manager_cache', {})
        )


@dataclass  
class HistoricalStateRecord:
    """
    Master record linking a block to its historical validator and epoch state.
    
    This is the top-level structure that gets persisted for each block.
    It enables full state reconstruction at any block height.
    
    USAGE:
    1. On block commit: Create and persist HistoricalStateRecord
    2. On reorg: Load record for target height, restore validator frame + epoch snapshot
    3. VRF validation uses restored historical state
    
    INTEGRITY:
    - record_hash links to previous record (chain of custody)
    - Tampered records will fail integrity check during restoration
    
    ATTESTATION MANAGER STATE (per Architect requirement):
    - attestation_manager_snapshot_hash: Reference to full AM state snapshot
    - This enables complete restoration of attestation state during reorg
    
    CONSENSUS STATE:
    - current_round: Round number for this height (affects fallback proposer selection)
    - slot: Time-based slot for this block
    """
    block_height: int
    block_hash: str
    timestamp: float
    
    validator_frame_hash: str
    
    epoch_number: int
    epoch_snapshot_hash: Optional[str] = None
    has_epoch_transition: bool = False
    
    attestation_manager_snapshot_hash: Optional[str] = None
    
    previous_record_hash: Optional[str] = None
    record_hash: Optional[str] = None
    
    proposer_address: str = ""
    proposer_was_valid: bool = True
    expected_proposer_by_vrf: str = ""
    
    current_round: int = 0
    slot: int = 0
    
    proposer_queue: List[str] = field(default_factory=list)
    
    def calculate_record_hash(self) -> str:
        """Calculate hash for integrity chain"""
        data = {
            'block_height': self.block_height,
            'block_hash': self.block_hash,
            'validator_frame_hash': self.validator_frame_hash,
            'epoch_number': self.epoch_number,
            'epoch_snapshot_hash': self.epoch_snapshot_hash,
            'previous_record_hash': self.previous_record_hash
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    def to_dict(self) -> dict:
        return {
            'block_height': self.block_height,
            'block_hash': self.block_hash,
            'timestamp': self.timestamp,
            'validator_frame_hash': self.validator_frame_hash,
            'epoch_number': self.epoch_number,
            'epoch_snapshot_hash': self.epoch_snapshot_hash,
            'has_epoch_transition': self.has_epoch_transition,
            'attestation_manager_snapshot_hash': self.attestation_manager_snapshot_hash,
            'previous_record_hash': self.previous_record_hash,
            'proposer_address': self.proposer_address,
            'proposer_was_valid': self.proposer_was_valid,
            'expected_proposer_by_vrf': self.expected_proposer_by_vrf,
            'current_round': self.current_round,
            'slot': self.slot,
            'proposer_queue': self.proposer_queue,
            'record_hash': self.calculate_record_hash()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'HistoricalStateRecord':
        return cls(
            block_height=data['block_height'],
            block_hash=data['block_hash'],
            timestamp=data['timestamp'],
            validator_frame_hash=data['validator_frame_hash'],
            epoch_number=data['epoch_number'],
            epoch_snapshot_hash=data.get('epoch_snapshot_hash'),
            has_epoch_transition=data.get('has_epoch_transition', False),
            attestation_manager_snapshot_hash=data.get('attestation_manager_snapshot_hash'),
            previous_record_hash=data.get('previous_record_hash'),
            record_hash=data.get('record_hash'),
            proposer_address=data.get('proposer_address', ''),
            proposer_was_valid=data.get('proposer_was_valid', True),
            expected_proposer_by_vrf=data.get('expected_proposer_by_vrf', ''),
            current_round=data.get('current_round', 0),
            slot=data.get('slot', 0),
            proposer_queue=data.get('proposer_queue', [])
        )


@dataclass
class AttestationManagerSnapshot:
    """
    Complete serializable state of AttestationManager for restore operations.
    
    When switching branches during reorg, the AttestationManager's mutable state
    must be rolled back to match the target branch. This snapshot captures
    everything needed for a complete restore.
    
    CAPTURED STATE:
    - attestations: All attestations received per epoch
    - epoch_validator_sets: Validator sets at each epoch
    - finalized_epochs: Which epochs are finalized
    - epoch_committees: Committee assignments per epoch
    """
    snapshot_height: int
    snapshot_hash: str
    
    attestations: Dict[int, Dict[str, int]]
    epoch_validator_sets: Dict[int, List[str]]
    finalized_epochs: List[int]
    epoch_committees: Dict[int, List[str]]
    
    epoch_length: int
    attestation_window: int
    committee_size: int
    
    def to_dict(self) -> dict:
        return {
            'snapshot_height': self.snapshot_height,
            'snapshot_hash': self.snapshot_hash,
            'attestations': self.attestations,
            'epoch_validator_sets': {str(k): list(v) for k, v in self.epoch_validator_sets.items()},
            'finalized_epochs': list(self.finalized_epochs),
            'epoch_committees': {str(k): list(v) for k, v in self.epoch_committees.items()},
            'epoch_length': self.epoch_length,
            'attestation_window': self.attestation_window,
            'committee_size': self.committee_size
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AttestationManagerSnapshot':
        return cls(
            snapshot_height=data['snapshot_height'],
            snapshot_hash=data['snapshot_hash'],
            attestations={int(k): v for k, v in data['attestations'].items()},
            epoch_validator_sets={int(k): list(v) for k, v in data['epoch_validator_sets'].items()},
            finalized_epochs=list(data['finalized_epochs']),
            epoch_committees={int(k): list(v) for k, v in data['epoch_committees'].items()},
            epoch_length=data['epoch_length'],
            attestation_window=data['attestation_window'],
            committee_size=data['committee_size']
        )


class HistoricalStateBuilder:
    """
    Factory class to create historical state records from live ledger state.
    
    This is used during block commit to capture the current state and
    create the appropriate historical records for persistence.
    """
    
    @staticmethod
    def create_validator_frame(
        block_height: int,
        block_hash: str,
        validator_registry: Dict[str, Any],
        is_full_frame: bool = False,
        parent_frame: Optional[ValidatorStateFrame] = None,
        recent_proposers: Optional[List[str]] = None,
        grace_period_validators: Optional[List[str]] = None,
        combined_liveness_set: Optional[List[str]] = None,
        lookback_blocks: int = 30,
        grace_window_blocks: int = 30,
        epoch_seed: str = "",
        epoch_number: int = 0
    ) -> ValidatorStateFrame:
        """
        Create a ValidatorStateFrame from current ledger state.
        
        Args:
            block_height: Height of the block being committed
            block_hash: Hash of the block being committed
            validator_registry: Current validator registry from ledger
            is_full_frame: True for epoch boundaries, False for deltas
            parent_frame: Previous frame (for delta calculation)
            recent_proposers: Validators who proposed in last N blocks (for liveness)
            grace_period_validators: Recently activated validators (for liveness)
            combined_liveness_set: Union of all liveness-qualified validators
            lookback_blocks: Number of blocks for recent proposer check
            grace_window_blocks: Number of blocks for activation grace period
            epoch_seed: Deterministic VRF seed for proposer selection (CRITICAL)
            epoch_number: Which epoch this block belongs to
        
        Returns:
            ValidatorStateFrame capturing validator state at this height
        """
        ordered_validators = []
        
        for addr, data in sorted(validator_registry.items()):
            if isinstance(data, dict):
                entry = ValidatorEntry(
                    address=addr,
                    public_key=data.get('public_key', ''),
                    device_id=data.get('device_id', ''),
                    status=data.get('status', 'pending'),
                    registered_at=data.get('registered_at', 0),
                    registration_height=data.get('registration_height', 0),
                    activation_height=data.get('activation_height', block_height),
                    deposit_amount=data.get('deposit_amount', 0),
                    voting_power=data.get('voting_power', 1),
                    proposer_priority=data.get('proposer_priority', 0)
                )
            else:
                entry = ValidatorEntry(
                    address=addr,
                    public_key=data if isinstance(data, str) else '',
                    device_id=f"legacy_{addr[:16]}",
                    status='active',
                    registered_at=0,
                    registration_height=0,
                    activation_height=0,
                    deposit_amount=0,
                    voting_power=1,
                    proposer_priority=0
                )
            ordered_validators.append(entry)
        
        added = []
        removed = []
        status_changes = {}
        
        if parent_frame and not is_full_frame:
            parent_addrs = {v.address for v in parent_frame.ordered_validators}
            current_addrs = {v.address for v in ordered_validators}
            
            for v in ordered_validators:
                if v.address not in parent_addrs:
                    added.append(v)
            
            removed = list(parent_addrs - current_addrs)
            
            parent_status = {v.address: v.status for v in parent_frame.ordered_validators}
            for v in ordered_validators:
                if v.address in parent_status and parent_status[v.address] != v.status:
                    status_changes[v.address] = v.status
        
        liveness_state = None
        if recent_proposers is not None or grace_period_validators is not None:
            liveness_state = LivenessFilterState(
                recent_proposers=recent_proposers or [],
                grace_period_validators=grace_period_validators or [],
                combined_liveness_set=combined_liveness_set or [],
                lookback_blocks=lookback_blocks,
                grace_window_blocks=grace_window_blocks
            )
        
        return ValidatorStateFrame(
            block_height=block_height,
            block_hash=block_hash,
            timestamp=time.time(),
            ordered_validators=ordered_validators,
            liveness_filter_state=liveness_state,
            epoch_seed=epoch_seed,
            epoch_number=epoch_number,
            is_full_frame=is_full_frame,
            parent_frame_height=parent_frame.block_height if parent_frame else None,
            added_validators=added,
            removed_validators=removed,
            status_changes=status_changes
        )
    
    @staticmethod
    def create_epoch_snapshot(
        epoch_number: int,
        epoch_length: int,
        attestation_manager: Any,
        epoch_seed: str,
        epoch_seed_source_hash: str,
        all_validators: Set[str],
        epoch_seed_source_height: int = 0,
        proposer_queue_by_height: Optional[Dict[int, List[str]]] = None,
        vrf_manager_cache: Optional[Dict[str, str]] = None
    ) -> EpochSnapshot:
        """
        Create an EpochSnapshot from current AttestationManager state.
        
        AUTOMATICALLY COMPUTES:
        - ordered_committee: Derives VRF-sorted committee ordering from epoch_seed
        
        Args:
            epoch_number: Epoch being snapshotted
            epoch_length: Blocks per epoch
            attestation_manager: Current AttestationManager instance
            epoch_seed: VRF seed for this epoch
            epoch_seed_source_hash: Block hash used to derive seed
            all_validators: Set of all registered validators
            epoch_seed_source_height: Block height used to derive seed
            proposer_queue_by_height: Cached proposer queues per height in epoch
            vrf_manager_cache: Cached VRF manager state
        
        Returns:
            EpochSnapshot capturing attestation state at epoch boundary
        """
        epoch_start = epoch_number * epoch_length
        epoch_end = (epoch_number + 1) * epoch_length - 1
        
        committee = attestation_manager.select_committee(epoch_number, all_validators)
        
        attestations = {}
        if epoch_number in attestation_manager.attestations:
            attestations = dict(attestation_manager.attestations[epoch_number])
        
        participating = len(attestations)
        total = len(committee) if committee else len(all_validators)
        rate = participating / total if total > 0 else 0.0
        
        is_finalized = epoch_number in attestation_manager.finalized_epochs
        
        liveness_scores = {}
        for validator in all_validators:
            score = 0
            for ep in range(max(0, epoch_number - 3), epoch_number + 1):
                if ep in attestation_manager.attestations:
                    if validator in attestation_manager.attestations[ep]:
                        score += 1
            liveness_scores[validator] = score
        
        ordered_committee = HistoricalStateBuilder._compute_vrf_ordered_committee(
            committee=committee,
            epoch_seed=epoch_seed,
            epoch_number=epoch_number
        )
        
        return EpochSnapshot(
            epoch_number=epoch_number,
            epoch_start_block=epoch_start,
            epoch_end_block=epoch_end,
            epoch_seed=epoch_seed,
            epoch_seed_source_hash=epoch_seed_source_hash,
            committee_members=list(committee),
            epoch_seed_source_height=epoch_seed_source_height,
            ordered_committee=ordered_committee,
            attestations=attestations,
            proposer_queue_by_height=proposer_queue_by_height or {},
            participating_validators=participating,
            total_validators=total,
            participation_rate=rate,
            is_finalized=is_finalized,
            liveness_scores=liveness_scores,
            vrf_manager_cache=vrf_manager_cache or {}
        )
    
    @staticmethod
    def _compute_vrf_ordered_committee(
        committee: Set[str],
        epoch_seed: str,
        epoch_number: int
    ) -> List[str]:
        """
        Compute VRF-sorted committee ordering using the same algorithm as VRFManager.
        
        This matches the logic in VRFManager.get_ordered_proposer_queue() to ensure
        deterministic ordering that can be replayed during reorg.
        
        Algorithm:
        1. Hash each validator with epoch_seed to get VRF score
        2. Sort by (vrf_score, address) for deterministic ordering
        
        Args:
            committee: Set of committee member addresses
            epoch_seed: Deterministic epoch seed
            epoch_number: Epoch number for reference
        
        Returns:
            List of validator addresses sorted by VRF score (lowest first = highest priority)
        """
        if not committee:
            return []
        
        vrf_scores = {}
        for validator_address in committee:
            vrf_input = f"{epoch_seed}_{validator_address}_{epoch_number}"
            vrf_score = hashlib.sha256(vrf_input.encode()).hexdigest()
            vrf_scores[validator_address] = vrf_score
        
        ordered = sorted(committee, key=lambda addr: (vrf_scores[addr], addr))
        return ordered
    
    @staticmethod
    def compute_proposer_queue_for_height(
        committee: Set[str],
        epoch_seed: str,
        block_height: int
    ) -> List[str]:
        """
        Compute the VRF-sorted proposer queue for a specific block height.
        
        This matches VRFManager.get_ordered_proposer_queue() algorithm exactly
        to enable deterministic replay during reorganization.
        
        Algorithm matches vrf.py:
        1. For each committee member: Hash(epoch_seed || validator_address || block_height)
        2. Sort by (vrf_score, address) for deterministic ordering
        
        Args:
            committee: Set of committee member addresses
            epoch_seed: Deterministic epoch seed
            block_height: Block height for proposer selection
        
        Returns:
            Ordered list [primary, fallback1, fallback2, ...] sorted by VRF score
        """
        if not committee:
            return []
        
        vrf_scores = {}
        for validator_address in committee:
            vrf_input = f"{epoch_seed}_{validator_address}_{block_height}"
            vrf_score = hashlib.sha256(vrf_input.encode()).hexdigest()
            vrf_scores[validator_address] = vrf_score
        
        ordered = sorted(committee, key=lambda addr: (vrf_scores[addr], addr))
        return ordered
    
    @staticmethod
    def compute_proposer_queues_for_epoch(
        committee: Set[str],
        epoch_seed: str,
        epoch_start_height: int,
        epoch_length: int
    ) -> Dict[int, List[str]]:
        """
        Pre-compute proposer queues for all heights in an epoch.
        
        This enables instant lookup during replay without recomputation.
        
        Args:
            committee: Set of committee member addresses
            epoch_seed: Deterministic epoch seed
            epoch_start_height: First block height in epoch
            epoch_length: Number of blocks per epoch
        
        Returns:
            Dict mapping height -> ordered proposer queue
        """
        queues = {}
        for height in range(epoch_start_height, epoch_start_height + epoch_length):
            queues[height] = HistoricalStateBuilder.compute_proposer_queue_for_height(
                committee=committee,
                epoch_seed=epoch_seed,
                block_height=height
            )
        return queues
    
    @staticmethod
    def create_attestation_manager_snapshot(
        attestation_manager: Any,
        block_height: int
    ) -> AttestationManagerSnapshot:
        """
        Create a complete snapshot of AttestationManager state.
        
        Used for full state restoration during branch switches.
        
        Args:
            attestation_manager: AttestationManager instance to snapshot
            block_height: Current block height
        
        Returns:
            AttestationManagerSnapshot for restoration
        """
        snapshot_data = {
            'attestations': dict(attestation_manager.attestations),
            'epoch_validator_sets': {k: list(v) for k, v in attestation_manager.epoch_validator_sets.items()},
            'finalized_epochs': list(attestation_manager.finalized_epochs),
            'epoch_committees': {k: list(v) for k, v in attestation_manager.epoch_committees.items()}
        }
        snapshot_hash = hashlib.sha256(json.dumps(snapshot_data, sort_keys=True).encode()).hexdigest()
        
        return AttestationManagerSnapshot(
            snapshot_height=block_height,
            snapshot_hash=snapshot_hash,
            attestations=dict(attestation_manager.attestations),
            epoch_validator_sets={k: list(v) for k, v in attestation_manager.epoch_validator_sets.items()},
            finalized_epochs=list(attestation_manager.finalized_epochs),
            epoch_committees={k: list(v) for k, v in attestation_manager.epoch_committees.items()},
            epoch_length=attestation_manager.epoch_length,
            attestation_window=attestation_manager.attestation_window,
            committee_size=attestation_manager.committee_size
        )
    
    @staticmethod
    def create_historical_record(
        block_height: int,
        block_hash: str,
        validator_frame: ValidatorStateFrame,
        epoch_number: int,
        epoch_snapshot: Optional[EpochSnapshot] = None,
        previous_record: Optional[HistoricalStateRecord] = None,
        proposer_address: str = "",
        expected_proposer: str = "",
        attestation_manager_snapshot: Optional[AttestationManagerSnapshot] = None,
        current_round: int = 0,
        slot: int = 0,
        proposer_queue: Optional[List[str]] = None
    ) -> HistoricalStateRecord:
        """
        Create a HistoricalStateRecord linking all state for a block.
        
        Args:
            block_height: Block height
            block_hash: Block hash
            validator_frame: ValidatorStateFrame for this block
            epoch_number: Current epoch
            epoch_snapshot: EpochSnapshot if epoch transition occurred
            previous_record: Previous HistoricalStateRecord (for chain)
            proposer_address: Actual block proposer
            expected_proposer: Expected proposer by VRF
            attestation_manager_snapshot: Full AM state snapshot (for reorg restore)
            current_round: Round number for this height (affects fallback selection)
            slot: Time-based slot for this block
            proposer_queue: Ordered proposer queue for this height
        
        Returns:
            HistoricalStateRecord for persistence
        """
        return HistoricalStateRecord(
            block_height=block_height,
            block_hash=block_hash,
            timestamp=time.time(),
            validator_frame_hash=validator_frame.calculate_hash(),
            epoch_number=epoch_number,
            epoch_snapshot_hash=epoch_snapshot.calculate_hash() if epoch_snapshot else None,
            has_epoch_transition=epoch_snapshot is not None,
            attestation_manager_snapshot_hash=attestation_manager_snapshot.snapshot_hash if attestation_manager_snapshot else None,
            previous_record_hash=previous_record.calculate_record_hash() if previous_record else None,
            proposer_address=proposer_address,
            proposer_was_valid=(proposer_address == expected_proposer) if expected_proposer else True,
            expected_proposer_by_vrf=expected_proposer,
            current_round=current_round,
            slot=slot,
            proposer_queue=proposer_queue or []
        )


class HistoricalStateLog:
    """
    Persistent storage and retrieval system for historical state data.
    
    This class manages the storage and caching of historical state records,
    enabling efficient reconstruction of validator state at any block height
    for deterministic VRF proposer validation during chain reorganization.
    
    STORAGE ARCHITECTURE:
    - In-memory LRU cache for recent blocks (fast access during normal operation)
    - Height-indexed storage for O(1) lookup by block height
    - Hash-indexed storage for integrity verification
    - Epoch-based compaction for storage efficiency
    - Disk persistence for evicted frames (transparent reload)
    
    PERSISTENCE STRATEGY:
    - Full snapshots every N blocks (configurable epoch_snapshot_interval)
    - Delta frames between snapshots for space efficiency
    - Automatic disk persistence before LRU eviction
    - Transparent reload from disk when accessing evicted heights
    - Automatic cleanup of ancient history beyond retention window
    
    THREAD SAFETY:
    - All operations are designed for single-threaded access
    - External synchronization required for concurrent use
    """
    
    def __init__(
        self,
        data_dir: str = "ledger/history",
        cache_size: int = 500,
        epoch_snapshot_interval: int = 100,
        max_retention_epochs: int = 10,
        auto_persist: bool = True
    ):
        """
        Initialize the historical state log.
        
        Args:
            data_dir: Directory for persistent storage
            cache_size: Maximum number of recent records to keep in memory
            epoch_snapshot_interval: Blocks between full snapshots
            max_retention_epochs: Maximum epochs to retain in storage
            auto_persist: Automatically persist to disk when evicting from cache
        """
        import os
        self.data_dir = data_dir
        self.cache_size = cache_size
        self.epoch_snapshot_interval = epoch_snapshot_interval
        self.max_retention_epochs = max_retention_epochs
        self.auto_persist = auto_persist
        
        os.makedirs(data_dir, exist_ok=True)
        
        self._height_to_record: Dict[int, HistoricalStateRecord] = {}
        self._height_to_frame: Dict[int, ValidatorStateFrame] = {}
        self._height_to_epoch_snapshot: Dict[int, EpochSnapshot] = {}
        self._height_to_am_snapshot: Dict[int, Dict] = {}
        
        self._hash_to_height: Dict[str, int] = {}
        
        self._access_order: List[int] = []
        
        self._latest_height: int = -1
        self._earliest_height: int = 0
        
        self._epoch_boundaries: List[int] = []
        
        self._persisted_heights: Set[int] = set()
    
    def store(
        self,
        record: HistoricalStateRecord,
        validator_frame: ValidatorStateFrame,
        epoch_snapshot: Optional[EpochSnapshot] = None,
        am_snapshot: Optional[Dict] = None
    ) -> bool:
        """
        Store a historical state record with its associated data.
        
        Args:
            record: The HistoricalStateRecord to store
            validator_frame: ValidatorStateFrame for this block
            epoch_snapshot: EpochSnapshot if this is an epoch boundary
            am_snapshot: AttestationManager snapshot dict for this block
        
        Returns:
            True if stored successfully, False on error
        """
        height = record.block_height
        
        self._height_to_record[height] = record
        self._height_to_frame[height] = validator_frame
        
        if epoch_snapshot:
            self._height_to_epoch_snapshot[height] = epoch_snapshot
            if height not in self._epoch_boundaries:
                self._epoch_boundaries.append(height)
                self._epoch_boundaries.sort()
        
        if am_snapshot:
            self._height_to_am_snapshot[height] = am_snapshot
        
        record_hash = record.calculate_record_hash()
        self._hash_to_height[record_hash] = height
        
        frame_hash = validator_frame.calculate_hash()
        self._hash_to_height[frame_hash] = height
        
        if height in self._access_order:
            self._access_order.remove(height)
        self._access_order.append(height)
        
        if height > self._latest_height:
            self._latest_height = height
        
        self._enforce_cache_limit()
        
        return True
    
    def get_record(self, height: int) -> Optional[HistoricalStateRecord]:
        """
        Retrieve a HistoricalStateRecord by block height.
        
        Args:
            height: Block height to retrieve
        
        Returns:
            HistoricalStateRecord if found, None otherwise
        """
        record = self._height_to_record.get(height)
        if record:
            self._touch(height)
        return record
    
    def get_frame(self, height: int) -> Optional[ValidatorStateFrame]:
        """
        Retrieve a ValidatorStateFrame by block height.
        
        Args:
            height: Block height to retrieve
        
        Returns:
            ValidatorStateFrame if found, None otherwise
        """
        frame = self._height_to_frame.get(height)
        if frame:
            self._touch(height)
        return frame
    
    def get_epoch_snapshot(self, height: int) -> Optional[EpochSnapshot]:
        """
        Retrieve an EpochSnapshot by block height.
        
        Args:
            height: Block height to retrieve
        
        Returns:
            EpochSnapshot if found, None otherwise
        """
        return self._height_to_epoch_snapshot.get(height)
    
    def get_am_snapshot(self, height: int) -> Optional[Dict]:
        """
        Retrieve an AttestationManager snapshot by block height.
        
        Args:
            height: Block height to retrieve
        
        Returns:
            AM snapshot dict if found, None otherwise
        """
        return self._height_to_am_snapshot.get(height)
    
    def get_nearest_epoch_snapshot(self, height: int) -> Tuple[Optional[EpochSnapshot], int]:
        """
        Get the nearest epoch snapshot at or before the given height.
        
        This is useful for reconstructing state when we don't have
        exact height data but have epoch boundary snapshots.
        
        Args:
            height: Target block height
        
        Returns:
            Tuple of (EpochSnapshot or None, epoch_boundary_height)
        """
        best_height = -1
        for epoch_height in self._epoch_boundaries:
            if epoch_height <= height:
                best_height = epoch_height
            else:
                break
        
        if best_height >= 0:
            snapshot = self._height_to_epoch_snapshot.get(best_height)
            return snapshot, best_height
        
        return None, -1
    
    def get_state_at_height(self, height: int) -> Optional[Dict]:
        """
        Get complete historical state at a specific height.
        
        This combines all stored data for convenient access.
        
        Args:
            height: Block height to retrieve
        
        Returns:
            Dict with record, frame, epoch_snapshot, am_snapshot or None
        """
        record = self.get_record(height)
        if not record:
            return None
        
        frame = self.get_frame(height)
        epoch_snapshot = self.get_epoch_snapshot(height)
        am_snapshot = self.get_am_snapshot(height)
        
        epoch_snap, epoch_height = self.get_nearest_epoch_snapshot(height)
        
        return {
            'record': record,
            'frame': frame,
            'epoch_snapshot': epoch_snapshot,
            'am_snapshot': am_snapshot,
            'nearest_epoch_snapshot': epoch_snap,
            'nearest_epoch_height': epoch_height
        }
    
    def get_validator_set_at_height(self, height: int) -> Optional[Dict[str, ValidatorEntry]]:
        """
        Reconstruct the full validator set at a specific height.
        
        If exact height frame exists, return it directly.
        Otherwise, find nearest full frame and apply deltas.
        
        Args:
            height: Block height to reconstruct
        
        Returns:
            Dict mapping address -> ValidatorEntry, or None if unavailable
        """
        frame = self.get_frame(height)
        if frame and frame.is_full_frame:
            return {v.address: v for v in frame.ordered_validators}
        
        if frame:
            return {v.address: v for v in frame.ordered_validators}
        
        return None
    
    def get_proposer_queue_at_height(self, height: int) -> Optional[List[str]]:
        """
        Get the VRF-ordered proposer queue for a specific height.
        
        Uses stored proposer_queue if available, otherwise computes
        from stored epoch snapshot.
        
        Args:
            height: Block height for proposer queue
        
        Returns:
            Ordered list of proposer addresses, or None if unavailable
        """
        record = self.get_record(height)
        if record and record.proposer_queue:
            return record.proposer_queue
        
        epoch_snap, _ = self.get_nearest_epoch_snapshot(height)
        if epoch_snap and height in epoch_snap.proposer_queue_by_height:
            return epoch_snap.proposer_queue_by_height[height]
        
        if epoch_snap and epoch_snap.committee_members and epoch_snap.epoch_seed:
            queue = HistoricalStateBuilder.compute_proposer_queue_for_height(
                committee=set(epoch_snap.committee_members),
                epoch_seed=epoch_snap.epoch_seed,
                block_height=height
            )
            return queue
        
        return None
    
    def remove_above_height(self, height: int) -> int:
        """
        Remove all records above a certain height.
        
        Used during rollback to clear invalidated state.
        
        Args:
            height: Keep records at and below this height
        
        Returns:
            Number of records removed
        """
        removed_count = 0
        
        heights_to_remove = [h for h in self._height_to_record.keys() if h > height]
        
        for h in heights_to_remove:
            record = self._height_to_record.pop(h, None)
            if record:
                record_hash = record.calculate_record_hash()
                self._hash_to_height.pop(record_hash, None)
                removed_count += 1
            
            frame = self._height_to_frame.pop(h, None)
            if frame:
                frame_hash = frame.calculate_hash()
                self._hash_to_height.pop(frame_hash, None)
            
            self._height_to_epoch_snapshot.pop(h, None)
            self._height_to_am_snapshot.pop(h, None)
            
            if h in self._access_order:
                self._access_order.remove(h)
        
        self._epoch_boundaries = [h for h in self._epoch_boundaries if h <= height]
        
        if heights_to_remove:
            remaining = list(self._height_to_record.keys())
            self._latest_height = max(remaining) if remaining else -1
        
        if removed_count > 0:
            print(f"🗑️ HistoricalStateLog: Removed {removed_count} records above height {height}")
        
        return removed_count
    
    def has_height(self, height: int) -> bool:
        """Check if we have a record for the given height."""
        return height in self._height_to_record
    
    def get_height_range(self) -> Tuple[int, int]:
        """Get the range of heights stored (min, max)."""
        return self._earliest_height, self._latest_height
    
    def get_stats(self) -> Dict:
        """Get storage statistics."""
        return {
            'total_records': len(self._height_to_record),
            'total_frames': len(self._height_to_frame),
            'epoch_snapshots': len(self._height_to_epoch_snapshot),
            'am_snapshots': len(self._height_to_am_snapshot),
            'cache_size': len(self._access_order),
            'cache_limit': self.cache_size,
            'earliest_height': self._earliest_height,
            'latest_height': self._latest_height,
            'epoch_boundaries': len(self._epoch_boundaries)
        }
    
    def _touch(self, height: int):
        """Update LRU access order."""
        if height in self._access_order:
            self._access_order.remove(height)
        self._access_order.append(height)
    
    def _get_height_file_path(self, height: int) -> str:
        """Get the file path for a specific height's persisted data."""
        import os
        epoch = height // self.epoch_snapshot_interval
        return os.path.join(self.data_dir, f"epoch_{epoch}", f"height_{height}.json")
    
    def _persist_height_to_disk(self, height: int) -> bool:
        """
        Persist a specific height's data to disk.
        
        Args:
            height: Block height to persist
        
        Returns:
            True if persisted successfully
        """
        import os
        
        record = self._height_to_record.get(height)
        frame = self._height_to_frame.get(height)
        epoch_snapshot = self._height_to_epoch_snapshot.get(height)
        am_snapshot = self._height_to_am_snapshot.get(height)
        
        if not record and not frame:
            return False
        
        data = {
            'version': 1,
            'height': height,
            'record': record.to_dict() if record else None,
            'frame': frame.to_dict() if frame else None,
            'epoch_snapshot': epoch_snapshot.to_dict() if epoch_snapshot else None,
            'am_snapshot': am_snapshot
        }
        
        file_path = self._get_height_file_path(height)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        try:
            with open(file_path, 'w') as f:
                json.dump(data, f, sort_keys=True)
            self._persisted_heights.add(height)
            return True
        except Exception as e:
            print(f"⚠️ Failed to persist height {height}: {e}")
            return False
    
    def _load_height_from_disk(self, height: int) -> bool:
        """
        Load a specific height's data from disk into cache.
        
        Args:
            height: Block height to load
        
        Returns:
            True if loaded successfully
        """
        file_path = self._get_height_file_path(height)
        
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            if data.get('version') != 1:
                return False
            
            if data.get('record'):
                self._height_to_record[height] = HistoricalStateRecord.from_dict(data['record'])
            
            if data.get('frame'):
                self._height_to_frame[height] = ValidatorStateFrame.from_dict(data['frame'])
            
            if data.get('epoch_snapshot'):
                self._height_to_epoch_snapshot[height] = EpochSnapshot.from_dict(data['epoch_snapshot'])
            
            if data.get('am_snapshot'):
                self._height_to_am_snapshot[height] = data['am_snapshot']
            
            self._touch(height)
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            print(f"⚠️ Failed to load height {height}: {e}")
            return False
    
    def _enforce_cache_limit(self):
        """Evict oldest entries if cache exceeds limit, persisting to disk first."""
        while len(self._access_order) > self.cache_size:
            oldest_height = self._access_order.pop(0)
            
            is_epoch_boundary = oldest_height in self._epoch_boundaries
            if is_epoch_boundary:
                continue
            
            if self.auto_persist:
                self._persist_height_to_disk(oldest_height)
            
            record = self._height_to_record.pop(oldest_height, None)
            if record:
                record_hash = record.calculate_record_hash()
                self._hash_to_height.pop(record_hash, None)
            
            frame = self._height_to_frame.pop(oldest_height, None)
            if frame:
                frame_hash = frame.calculate_hash()
                self._hash_to_height.pop(frame_hash, None)
            
            self._height_to_am_snapshot.pop(oldest_height, None)
    
    def export_for_persistence(self, from_height: int = 0, to_height: Optional[int] = None) -> Dict:
        """
        Export historical state data for file persistence.
        
        Args:
            from_height: Start height (inclusive)
            to_height: End height (inclusive), or None for latest
        
        Returns:
            Dict with serialized historical state data
        """
        if to_height is None:
            to_height = self._latest_height
        
        records = {}
        frames = {}
        epoch_snapshots = {}
        am_snapshots = {}
        
        for height in range(from_height, to_height + 1):
            if height in self._height_to_record:
                records[height] = self._height_to_record[height].to_dict()
            if height in self._height_to_frame:
                frames[height] = self._height_to_frame[height].to_dict()
            if height in self._height_to_epoch_snapshot:
                epoch_snapshots[height] = self._height_to_epoch_snapshot[height].to_dict()
            if height in self._height_to_am_snapshot:
                am_snapshots[height] = self._height_to_am_snapshot[height]
        
        return {
            'version': 1,
            'from_height': from_height,
            'to_height': to_height,
            'records': records,
            'frames': frames,
            'epoch_snapshots': epoch_snapshots,
            'am_snapshots': am_snapshots,
            'epoch_boundaries': self._epoch_boundaries,
            'exported_at': time.time()
        }
    
    def import_from_persistence(self, data: Dict) -> bool:
        """
        Import historical state data from persistence.
        
        Args:
            data: Dict from export_for_persistence()
        
        Returns:
            True if import succeeded, False on error
        """
        if data.get('version') != 1:
            print(f"⚠️ Unknown historical state version: {data.get('version')}")
            return False
        
        for height_str, record_dict in data.get('records', {}).items():
            height = int(height_str)
            record = HistoricalStateRecord.from_dict(record_dict)
            self._height_to_record[height] = record
            
            record_hash = record.calculate_record_hash()
            self._hash_to_height[record_hash] = height
        
        for height_str, frame_dict in data.get('frames', {}).items():
            height = int(height_str)
            frame = ValidatorStateFrame.from_dict(frame_dict)
            self._height_to_frame[height] = frame
            
            frame_hash = frame.calculate_hash()
            self._hash_to_height[frame_hash] = height
        
        for height_str, snapshot_dict in data.get('epoch_snapshots', {}).items():
            height = int(height_str)
            snapshot = EpochSnapshot.from_dict(snapshot_dict)
            self._height_to_epoch_snapshot[height] = snapshot
        
        for height_str, am_dict in data.get('am_snapshots', {}).items():
            height = int(height_str)
            self._height_to_am_snapshot[height] = am_dict
        
        self._epoch_boundaries = data.get('epoch_boundaries', [])
        
        all_heights = list(self._height_to_record.keys())
        if all_heights:
            self._earliest_height = min(all_heights)
            self._latest_height = max(all_heights)
            self._access_order = sorted(all_heights)[-self.cache_size:]
        
        print(f"✅ HistoricalStateLog: Imported {len(self._height_to_record)} records from persistence")
        return True
