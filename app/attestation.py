"""
EPOCH-BASED ATTESTATION SYSTEM

Replaces per-block heartbeat transactions with epoch-based attestations for 100K+ validator scalability.

KEY IMPROVEMENTS:
- Reduces transaction load from 150K heartbeats/block to ~1K attestations/epoch
- Validators submit ONE attestation per epoch (100 blocks = 5 minutes)
- Attestations prove liveness without spamming the mempool/network
- Epoch boundaries provide natural checkpoints for validator set rotation

ARCHITECTURE:
- Epoch = 100 blocks (5 minutes at 3s/block)
- Attestation window = 10 blocks (30 seconds) after epoch starts
- Validators must attest to stay in active validator pool
- 67% participation required for healthy epoch
"""

from typing import Dict, Set, Optional, List, Tuple
from dataclasses import dataclass
import time
import hashlib
import json


@dataclass
class EpochInfo:
    """Information about a specific epoch"""
    epoch_number: int
    start_block: int
    end_block: int
    attestation_deadline: int  # Last block to submit attestation
    participating_validators: int
    total_validators: int
    participation_rate: float
    is_finalized: bool


class AttestationManager:
    """
    Manages epoch-based validator attestations for scalability.
    Replaces continuous heartbeat transactions with periodic attestations.
    """
    
    def __init__(self, epoch_length: int = 100, attestation_window: int = 10, committee_size: int = 1000):
        """
        Initialize attestation manager with rotating committee support.
        
        Args:
            epoch_length: Number of blocks per epoch (default: 100 = 5 min)
            attestation_window: Number of blocks to submit attestation (default: 10 = 30 seconds)
            committee_size: Number of validators in attestation committee (default: 1000)
        """
        self.epoch_length = epoch_length
        self.attestation_window = attestation_window
        self.committee_size = committee_size
        
        # Track attestations: {epoch_number: {validator_address: block_height}}
        self.attestations: Dict[int, Dict[str, int]] = {}
        
        # Track validator set per epoch: {epoch_number: set(validator_addresses)}
        self.epoch_validator_sets: Dict[int, Set[str]] = {}
        
        # Cache finalized epochs for quick lookup
        self.finalized_epochs: Set[int] = set()
        
        # Cache committee members per epoch: {epoch_number: set(validator_addresses)}
        self.epoch_committees: Dict[int, Set[str]] = {}
    
    def get_epoch_number(self, block_height: int) -> int:
        """
        Calculate epoch number for a given block height.
        
        Args:
            block_height: Block height to check
            
        Returns:
            Epoch number (0-indexed)
            
        Example:
            Block 0-99 → Epoch 0
            Block 100-199 → Epoch 1
            Block 200-299 → Epoch 2
        """
        return block_height // self.epoch_length
    
    def get_epoch_start_block(self, epoch_number: int) -> int:
        """Get the first block of an epoch"""
        return epoch_number * self.epoch_length
    
    def get_epoch_end_block(self, epoch_number: int) -> int:
        """Get the last block of an epoch"""
        return (epoch_number + 1) * self.epoch_length - 1
    
    def get_attestation_deadline(self, epoch_number: int) -> int:
        """
        Get the deadline block for submitting attestations.
        Validators must attest within attestation_window blocks of epoch start.
        """
        epoch_start = self.get_epoch_start_block(epoch_number)
        return epoch_start + self.attestation_window - 1
    
    def select_committee(self, epoch_number: int, all_validators: Set[str]) -> Set[str]:
        """
        Select attestation committee for an epoch using deterministic uniform sampling.
        
        Uses epoch-seeded hash ranking to select exactly committee_size validators.
        All nodes will select the same committee independently with uniform probability.
        
        Args:
            epoch_number: Epoch to select committee for
            all_validators: Set of all registered validators
            
        Returns:
            Set of validator addresses selected for committee (exactly committee_size members)
        """
        # Check cache first
        if epoch_number in self.epoch_committees:
            return self.epoch_committees[epoch_number]
        
        # If fewer validators than committee size, all validators are in committee
        if len(all_validators) <= self.committee_size:
            committee = set(all_validators)
            self.epoch_committees[epoch_number] = committee
            return committee
        
        # UNIFORM SAMPLING: Hash each validator with epoch seed, sort by hash, take top N
        # This guarantees exactly committee_size members with uniform probability
        validator_hashes = []
        
        for validator in all_validators:
            # Hash combines epoch number + validator address
            combined = f"epoch_{epoch_number}_{validator}".encode()
            hash_value = hashlib.sha256(combined).hexdigest()
            # Use hash as sorting key (deterministic randomness)
            validator_hashes.append((hash_value, validator))
        
        # Sort by hash value (deterministic across all nodes)
        validator_hashes.sort(key=lambda x: x[0])
        
        # Select top committee_size validators
        committee = set([validator for _, validator in validator_hashes[:self.committee_size]])
        
        # Verify we got exactly the right number (should always be true)
        assert len(committee) == self.committee_size, f"Committee size mismatch: {len(committee)} != {self.committee_size}"
        
        # Cache for future lookups
        self.epoch_committees[epoch_number] = committee
        return committee
    
    def is_in_committee(self, epoch_number: int, validator_address: str, all_validators: Set[str]) -> bool:
        """
        Check if a validator is in the attestation committee for an epoch.
        
        Args:
            epoch_number: Epoch number
            validator_address: Validator's address
            all_validators: Set of all registered validators
            
        Returns:
            True if validator is in committee, False otherwise
        """
        committee = self.select_committee(epoch_number, all_validators)
        return validator_address in committee
    
    def should_attest(self, block_height: int, validator_address: str, all_validators: Set[str]) -> bool:
        """
        Check if validator should submit an attestation at this block height.
        
        Args:
            block_height: Current block height
            validator_address: Validator's address
            all_validators: Set of all registered validators
            
        Returns:
            True if validator should attest now, False otherwise
        """
        current_epoch = self.get_epoch_number(block_height)
        
        # Check if validator is in this epoch's committee
        if not self.is_in_committee(current_epoch, validator_address, all_validators):
            return False
        
        attestation_deadline = self.get_attestation_deadline(current_epoch)
        
        # Don't attest if outside attestation window
        if block_height > attestation_deadline:
            return False
        
        # Don't attest if already attested for this epoch
        if current_epoch in self.attestations:
            if validator_address in self.attestations[current_epoch]:
                return False
        
        # Should attest if in committee, within window, and haven't attested yet
        epoch_start = self.get_epoch_start_block(current_epoch)
        return block_height >= epoch_start
    
    def validate_attestation(self, epoch_number: int, validator_address: str, block_height: int, all_validators: Set[str], skip_committee_check: bool = False) -> Tuple[bool, str]:
        """
        Validate an attestation without recording it (for block validation).
        
        Args:
            epoch_number: Epoch number to attest for
            validator_address: Validator's address
            block_height: Block height where attestation was submitted
            all_validators: Set of all registered validators (for committee check)
            skip_committee_check: Skip committee check during bootstrap period
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        current_epoch = self.get_epoch_number(block_height)
        
        # SECURITY: Reject attestations for future epochs (prevent spam)
        if epoch_number > current_epoch:
            return False, f"future epoch {epoch_number} (current: {current_epoch})"
        
        # SECURITY: Reject attestations for very old epochs (prevent replay)
        # Only accept attestations for current epoch or previous epoch (grace period)
        if epoch_number < current_epoch - 1:
            return False, f"too old (epoch {epoch_number}, current: {current_epoch})"
        
        # SECURITY: Validator must be in committee for this epoch (unless bootstrap)
        if not skip_committee_check:
            if not self.is_in_committee(epoch_number, validator_address, all_validators):
                return False, f"not in committee for epoch {epoch_number}"
        
        # Check if attestation is within valid window
        epoch_start = self.get_epoch_start_block(epoch_number)
        attestation_deadline = self.get_attestation_deadline(epoch_number)
        
        if block_height < epoch_start:
            return False, f"too early (block {block_height} < epoch start {epoch_start})"
        
        if block_height > attestation_deadline:
            return False, f"past deadline (block {block_height} > deadline {attestation_deadline})"
        
        # SECURITY: Prevent duplicate attestations (one per epoch per validator)
        if epoch_number in self.attestations:
            if validator_address in self.attestations[epoch_number]:
                return False, f"already attested for epoch {epoch_number}"
        
        return True, ""
    
    def record_attestation(self, epoch_number: int, validator_address: str, block_height: int) -> bool:
        """
        Record a validator's attestation for an epoch.
        
        NOTE: This method assumes validation has already been done via validate_attestation().
        It should only be called during block application, not during validation.
        
        Args:
            epoch_number: Epoch number to attest for
            validator_address: Validator's address
            block_height: Block height where attestation was submitted
            
        Returns:
            True if attestation was recorded, False if duplicate
        """
        # Check for duplicate (defense in depth - should have been caught in validation)
        if epoch_number in self.attestations:
            if validator_address in self.attestations[epoch_number]:
                print(f"⚠️  Duplicate attestation during apply: {validator_address[:20]}... epoch {epoch_number}")
                return False
        
        # Record attestation
        if epoch_number not in self.attestations:
            self.attestations[epoch_number] = {}
        
        self.attestations[epoch_number][validator_address] = block_height
        return True
    
    def get_attestations_for_epoch(self, epoch_number: int) -> Dict[str, int]:
        """
        Get all attestations for a specific epoch.
        
        Returns:
            Dictionary mapping validator_address → block_height of attestation
        """
        return self.attestations.get(epoch_number, {})
    
    def has_attested(self, epoch_number: int, validator_address: str) -> bool:
        """Check if a validator has attested for a specific epoch"""
        if epoch_number not in self.attestations:
            return False
        return validator_address in self.attestations[epoch_number]
    
    def get_epoch_info(self, epoch_number: int, validator_set: Set[str], current_block: int) -> EpochInfo:
        """
        Get comprehensive information about an epoch.
        
        Args:
            epoch_number: Epoch to query
            validator_set: Set of all registered validators
            current_block: Current block height
            
        Returns:
            EpochInfo with participation metrics
        """
        attestations = self.get_attestations_for_epoch(epoch_number)
        participating_validators = len(attestations)
        total_validators = len(validator_set)
        participation_rate = participating_validators / total_validators if total_validators > 0 else 0.0
        
        # Epoch is finalized if we're past the attestation deadline
        attestation_deadline = self.get_attestation_deadline(epoch_number)
        is_finalized = current_block > attestation_deadline or epoch_number in self.finalized_epochs
        
        return EpochInfo(
            epoch_number=epoch_number,
            start_block=self.get_epoch_start_block(epoch_number),
            end_block=self.get_epoch_end_block(epoch_number),
            attestation_deadline=attestation_deadline,
            participating_validators=participating_validators,
            total_validators=total_validators,
            participation_rate=participation_rate,
            is_finalized=is_finalized
        )
    
    def finalize_epoch(self, epoch_number: int):
        """Mark an epoch as finalized (no more attestations accepted)"""
        self.finalized_epochs.add(epoch_number)
    
    def get_active_validators_for_epoch(self, epoch_number: int, all_validators: Set[str]) -> Set[str]:
        """
        Get the set of active validators for an epoch (those who attested).
        
        Args:
            epoch_number: Epoch number
            all_validators: Set of all registered validators
            
        Returns:
            Set of validator addresses who attested (are active)
        """
        attestations = self.get_attestations_for_epoch(epoch_number)
        return set(attestations.keys())
    
    def get_committee_info(self, epoch_number: int, all_validators: Set[str]) -> Dict:
        """
        Get information about the attestation committee for an epoch.
        
        Args:
            epoch_number: Epoch number
            all_validators: Set of all registered validators
            
        Returns:
            Dict with committee statistics
        """
        committee = self.select_committee(epoch_number, all_validators)
        attestations = self.get_attestations_for_epoch(epoch_number)
        
        committee_participation = len([v for v in attestations.keys() if v in committee])
        participation_rate = committee_participation / len(committee) if committee else 0.0
        
        return {
            "epoch_number": epoch_number,
            "committee_size": len(committee),
            "total_validators": len(all_validators),
            "committee_attestations": committee_participation,
            "participation_rate": participation_rate,
            "is_full_network": len(all_validators) <= self.committee_size
        }
    
    def cleanup_old_epochs(self, current_block: int, keep_epochs: int = 10):
        """
        Clean up attestation data for old epochs to prevent memory bloat.
        Keep only recent epochs for liveness tracking.
        
        Args:
            current_block: Current block height
            keep_epochs: Number of recent epochs to keep (default: 10 epochs = ~50 minutes)
        """
        current_epoch = self.get_epoch_number(current_block)
        cutoff_epoch = max(0, current_epoch - keep_epochs)
        
        # Remove old attestations
        epochs_to_remove = [epoch for epoch in self.attestations.keys() if epoch < cutoff_epoch]
        for epoch in epochs_to_remove:
            del self.attestations[epoch]
        
        # Remove old validator sets
        epochs_to_remove = [epoch for epoch in self.epoch_validator_sets.keys() if epoch < cutoff_epoch]
        for epoch in epochs_to_remove:
            del self.epoch_validator_sets[epoch]
        
        # Remove old finalized flags
        self.finalized_epochs = {epoch for epoch in self.finalized_epochs if epoch >= cutoff_epoch}
        
        # Remove old committee caches
        committees_to_remove = [epoch for epoch in self.epoch_committees.keys() if epoch < cutoff_epoch]
        for epoch in committees_to_remove:
            del self.epoch_committees[epoch]
        
        if epochs_to_remove:
            print(f"🧹 Cleaned up {len(epochs_to_remove)} old epochs (kept last {keep_epochs} epochs)")
    
    def get_participation_statistics(self, recent_epochs: int = 10) -> Dict:
        """
        Get participation statistics for recent epochs.
        
        Args:
            recent_epochs: Number of recent epochs to analyze
            
        Returns:
            Dict with participation stats
        """
        if not self.attestations:
            return {
                "recent_epochs": 0,
                "avg_participation_rate": 0.0,
                "min_participation_rate": 0.0,
                "max_participation_rate": 0.0
            }
        
        # Get recent epoch numbers
        latest_epoch = max(self.attestations.keys())
        start_epoch = max(0, latest_epoch - recent_epochs + 1)
        
        participation_rates = []
        for epoch in range(start_epoch, latest_epoch + 1):
            if epoch in self.epoch_validator_sets:
                validator_set = self.epoch_validator_sets[epoch]
                attestations = self.get_attestations_for_epoch(epoch)
                if validator_set:
                    rate = len(attestations) / len(validator_set)
                    participation_rates.append(rate)
        
        if not participation_rates:
            return {
                "recent_epochs": 0,
                "avg_participation_rate": 0.0,
                "min_participation_rate": 0.0,
                "max_participation_rate": 0.0
            }
        
        return {
            "recent_epochs": len(participation_rates),
            "avg_participation_rate": sum(participation_rates) / len(participation_rates),
            "min_participation_rate": min(participation_rates),
            "max_participation_rate": max(participation_rates)
        }
    
    def export_snapshot(self, block_height: int = 0) -> Dict:
        """
        Export complete AttestationManager state for persistence and restoration.
        
        This is used during block commits to capture the attestation state at a
        specific height. The snapshot enables deterministic replay during reorg
        by restoring the exact attestation state that existed before the fork point.
        
        CRITICAL FOR REORG SAFETY:
        - Attestations affect proposer liveness filtering
        - Committee membership depends on epoch boundaries
        - Incorrect state restoration = invalid proposer selection
        
        DETERMINISTIC SERIALIZATION (Dec 2025 fix):
        All collections MUST be sorted to ensure deterministic hash computation.
        This prevents hash mismatches during import_snapshot() which would cause
        reorg failures. The hash is computed from JSON with sort_keys=True, but
        list ordering within values must also be deterministic.
        
        Args:
            block_height: Block height at which snapshot was taken (metadata only)
            
        Returns:
            Dict containing complete serializable state
        """
        # CRITICAL: Use deterministic serialization for all collections
        # - Sort all lists to ensure consistent ordering
        # - Use string keys for epochs (JSON compatibility)
        # - Sort dict items by key for deterministic iteration
        snapshot_data = {
            'attestations': {
                str(epoch): dict(sorted(validators.items())) 
                for epoch, validators in sorted(self.attestations.items())
            },
            'epoch_validator_sets': {
                str(epoch): list(sorted(validators)) 
                for epoch, validators in sorted(self.epoch_validator_sets.items())
            },
            'finalized_epochs': sorted(list(self.finalized_epochs)),
            'epoch_committees': {
                str(epoch): list(sorted(committee)) 
                for epoch, committee in sorted(self.epoch_committees.items())
            }
        }
        
        config = {
            'epoch_length': self.epoch_length,
            'attestation_window': self.attestation_window,
            'committee_size': self.committee_size
        }
        
        snapshot_hash = hashlib.sha256(
            json.dumps(snapshot_data, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()
        
        return {
            'snapshot_height': block_height,
            'snapshot_hash': snapshot_hash,
            'state': snapshot_data,
            'config': config,
            'version': 1
        }
    
    def import_snapshot(self, snapshot: Dict, verify_hash: bool = True) -> bool:
        """
        Restore AttestationManager state from a snapshot.
        
        This is called during reorg to restore the exact attestation state
        that existed at a previous block height before applying the new chain.
        
        SECURITY NOTE:
        - Validates snapshot hash integrity (strict check)
        - Replaces ALL mutable state atomically
        - Config params are NOT restored (assumed consistent)
        
        DETERMINISTIC SERIALIZATION (Dec 2025 fix):
        Hash verification now works correctly because export_snapshot() uses
        deterministic serialization (sorted lists, string keys). Old snapshots
        from before this fix may fail hash verification - in that case, the
        chain should be reset to ensure clean state.
        
        Args:
            snapshot: Snapshot dict from export_snapshot()
            verify_hash: Whether to verify snapshot hash integrity
            
        Returns:
            True if restoration succeeded, False if validation failed
        """
        if snapshot.get('version') != 1:
            print(f"⚠️ Unknown snapshot version: {snapshot.get('version')}")
            return False
        
        state = snapshot.get('state', {})
        
        if verify_hash:
            expected_hash = snapshot.get('snapshot_hash')
            actual_hash = hashlib.sha256(
                json.dumps(state, sort_keys=True, separators=(',', ':')).encode()
            ).hexdigest()
            if expected_hash != actual_hash:
                # STRICT: Hash mismatch indicates data corruption or version mismatch
                # With deterministic serialization in export_snapshot(), this should
                # never happen for snapshots created with the current code version.
                # If this occurs, the chain likely needs to be reset.
                print(f"❌ Snapshot hash mismatch! Expected {expected_hash[:16]}..., got {actual_hash[:16]}...")
                print(f"   This indicates data corruption or snapshot from incompatible version.")
                print(f"   Consider resetting the chain to ensure clean state.")
                return False
        
        self.attestations = {
            int(epoch): dict(validators) 
            for epoch, validators in state.get('attestations', {}).items()
        }
        
        self.epoch_validator_sets = {
            int(epoch): set(validators) 
            for epoch, validators in state.get('epoch_validator_sets', {}).items()
        }
        
        self.finalized_epochs = set(state.get('finalized_epochs', []))
        
        self.epoch_committees = {
            int(epoch): set(committee) 
            for epoch, committee in state.get('epoch_committees', {}).items()
        }
        
        snapshot_height = snapshot.get('snapshot_height', 'unknown')
        print(f"✅ AttestationManager state restored from height {snapshot_height}")
        return True
    
    def get_state_hash(self) -> str:
        """
        Calculate a hash of the current attestation state.
        
        Used to detect state divergence between nodes and verify
        that snapshot restore produced correct state.
        
        DETERMINISTIC SERIALIZATION (Dec 2025 fix):
        Uses the same serialization format as export_snapshot() to ensure
        consistent hash computation across export, import, and state hash.
        
        Returns:
            SHA256 hash of current state
        """
        # CRITICAL: Must match export_snapshot() serialization exactly
        snapshot_data = {
            'attestations': {
                str(epoch): dict(sorted(validators.items())) 
                for epoch, validators in sorted(self.attestations.items())
            },
            'epoch_validator_sets': {
                str(epoch): list(sorted(validators))
                for epoch, validators in sorted(self.epoch_validator_sets.items())
            },
            'finalized_epochs': sorted(list(self.finalized_epochs)),
            'epoch_committees': {
                str(epoch): list(sorted(committee)) 
                for epoch, committee in sorted(self.epoch_committees.items())
            }
        }
        return hashlib.sha256(
            json.dumps(snapshot_data, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()
    
    def rollback_to_height(self, target_height: int) -> int:
        """
        Remove all attestation state after target_height.
        
        This is a lighter alternative to full snapshot restore when we just
        need to undo attestations recorded after a certain block.
        
        Args:
            target_height: Block height to roll back to
            
        Returns:
            Number of attestations removed
        """
        removed_count = 0
        target_epoch = self.get_epoch_number(target_height)
        
        epochs_to_clear = [
            epoch for epoch in self.attestations.keys() 
            if epoch > target_epoch
        ]
        for epoch in epochs_to_clear:
            removed_count += len(self.attestations[epoch])
            del self.attestations[epoch]
        
        if target_epoch in self.attestations:
            validators_to_remove = [
                addr for addr, height in self.attestations[target_epoch].items()
                if height > target_height
            ]
            for addr in validators_to_remove:
                del self.attestations[target_epoch][addr]
                removed_count += 1
        
        self.finalized_epochs = {
            epoch for epoch in self.finalized_epochs 
            if epoch <= target_epoch
        }
        
        committees_to_remove = [
            epoch for epoch in self.epoch_committees.keys() 
            if epoch > target_epoch
        ]
        for epoch in committees_to_remove:
            del self.epoch_committees[epoch]
        
        if removed_count > 0:
            print(f"🔙 AttestationManager rolled back to height {target_height}: removed {removed_count} attestations")
        
        return removed_count
