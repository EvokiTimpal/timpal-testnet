"""
ATTESTATION-BASED FINALITY SYSTEM FOR TIMPAL BLOCKCHAIN

Provides cryptographic finality guarantees through validator attestations.

KEY CONCEPTS:
- CONFIRMED: Transaction included in a block at height H (1-block confirmation)
- FINAL: Block at height H has >= QUORUM attestations from eligible validators
- Once FINAL: Blocks <= H are immutable. Reorg that changes them is FORBIDDEN.

ARCHITECTURE:
- Validators sign attestations for blocks they observe as canonical
- Attestations propagate via P2P
- Block becomes FINAL when quorum attestations gathered
- finalized_height is monotonically increasing
- NO REORG at heights <= finalized_height (hard invariant)

MAINNET-SAFE PARAMETERS:
- FINALITY_QUORUM = max(2, floor(eligible_validators / 3))
- FINALITY_DEPTH = 1 (finalize block H when head >= H+1)
"""

import hashlib
import json
import os
import sqlite3
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

import config


# ============================================================
# CONSTANTS (MAINNET-SAFE, IMMUTABLE)
# ============================================================

# Finality depth: finalize block H when head >= H + FINALITY_DEPTH
# Using 1 allows attestations to arrive reliably before finalization
FINALITY_DEPTH = 1

# Attestation message prefix for domain separation
ATTESTATION_PREFIX = "TIMPAL_ATTEST_V1"


@dataclass
class FinalityAttestation:
    """
    A validator's attestation for a specific block.
    
    Attestations prove that a validator observed a block as canonical.
    When quorum attestations are gathered, the block becomes FINAL.
    """
    height: int
    block_hash: str
    validator_id: str  # Canonical validator ID (lowercase, stripped)
    signature: str     # ECDSA signature of attestation message
    seen_ts: int       # Unix timestamp when attestation was received
    
    def to_dict(self) -> dict:
        return {
            "height": self.height,
            "block_hash": self.block_hash,
            "validator_id": self.validator_id,
            "signature": self.signature,
            "seen_ts": self.seen_ts
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "FinalityAttestation":
        return cls(
            height=data["height"],
            block_hash=data["block_hash"],
            validator_id=data["validator_id"],
            signature=data["signature"],
            seen_ts=data["seen_ts"]
        )


def compute_attestation_message(height: int, block_hash: str) -> bytes:
    """
    Compute the message to be signed for a finality attestation.
    
    Format: hash(TIMPAL_ATTEST_V1 || chain_id || height || block_hash)
    
    This provides domain separation and ensures attestations are
    chain-specific and height-specific.
    
    Args:
        height: Block height being attested
        block_hash: Hash of the block being attested
        
    Returns:
        Message bytes to be signed
    """
    chain_id = getattr(config, 'CHAIN_ID', 'timpal-mainnet')
    message = f"{ATTESTATION_PREFIX}|{chain_id}|{height}|{block_hash}"
    return hashlib.sha256(message.encode()).digest()


def create_attestation_signature(height: int, block_hash: str, private_key: str) -> str:
    """
    Create a signature for a finality attestation.
    
    Uses the same ECDSA SECP256k1 curve as block signatures.
    
    Args:
        height: Block height being attested
        block_hash: Hash of the block being attested
        private_key: Validator's private key (hex string)
        
    Returns:
        Signature as hex string
    """
    from ecdsa import SigningKey, SECP256k1
    
    message = compute_attestation_message(height, block_hash)
    sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
    signature = sk.sign(message)
    return signature.hex()


def verify_attestation_signature(height: int, block_hash: str, 
                                  validator_public_key: str, signature: str) -> bool:
    """
    Verify a finality attestation signature.
    
    Args:
        height: Block height being attested
        block_hash: Hash of the block being attested
        validator_public_key: Validator's public key (hex string)
        signature: Signature to verify (hex string)
        
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        from ecdsa import VerifyingKey, SECP256k1
        
        message = compute_attestation_message(height, block_hash)
        vk = VerifyingKey.from_string(bytes.fromhex(validator_public_key), curve=SECP256k1)
        sig_bytes = bytes.fromhex(signature)
        return vk.verify(sig_bytes, message)
    except Exception:
        return False


def compute_finality_quorum(eligible_count: int) -> int:
    """
    Compute the quorum required for finality.
    
    FINALITY_QUORUM = max(2, floor(eligible_validators / 3))
    
    This ensures:
    - At least 2 validators must attest (prevents single-validator finality)
    - ~33% of validators must agree for finality
    
    Args:
        eligible_count: Number of eligible validators
        
    Returns:
        Quorum threshold for finality
    """
    return max(2, eligible_count // 3)


class FinalityManager:
    """
    Manages attestation-based finality for TIMPAL blockchain.
    
    Responsibilities:
    - Store and retrieve finality attestations
    - Track finalized_height (monotonically increasing)
    - Check if blocks have reached finality quorum
    - Provide finality barrier for fork choice
    
    HARD INVARIANT: NO REORG at heights <= finalized_height
    """
    
    def __init__(self, db_path: str):
        """
        Initialize finality manager with SQLite storage.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for concurrent reads during writes
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        
        self._initialize_schema()
        
        # Cache finalized_height for fast access
        self._finalized_height = self._load_finalized_height()
        self._finalized_hash = self._load_finalized_hash()
    
    def _initialize_schema(self):
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()
        
        # Finality attestations table
        # Unique constraint on (height, block_hash, validator_id) prevents duplicates
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS finality_attestations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                height INTEGER NOT NULL,
                block_hash TEXT NOT NULL,
                validator_id TEXT NOT NULL,
                signature TEXT NOT NULL,
                seen_ts INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(height, block_hash, validator_id)
            )
        """)
        
        # Index for efficient quorum counting
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attestations_height_hash 
            ON finality_attestations(height, block_hash)
        """)
        
        # Index for validator lookup
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attestations_validator 
            ON finality_attestations(validator_id)
        """)
        
        # Finalized blocks table (tracks finalized_height monotonically)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS finalized_blocks (
                height INTEGER PRIMARY KEY,
                block_hash TEXT NOT NULL,
                finalized_at INTEGER NOT NULL
            )
        """)
        
        # Finality state table (single row for current finalized_height)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS finality_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                finalized_height INTEGER NOT NULL DEFAULT -1,
                finalized_hash TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        
        # Initialize finality state if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO finality_state (id, finalized_height, finalized_hash, updated_at)
            VALUES (1, -1, NULL, ?)
        """, (int(time.time()),))
        
        self.conn.commit()
    
    def _load_finalized_height(self) -> int:
        """Load finalized_height from database."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT finalized_height FROM finality_state WHERE id = 1")
        row = cursor.fetchone()
        return row['finalized_height'] if row else -1
    
    def _load_finalized_hash(self) -> Optional[str]:
        """Load finalized_hash from database."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT finalized_hash FROM finality_state WHERE id = 1")
        row = cursor.fetchone()
        return row['finalized_hash'] if row else None
    
    def get_finalized_height(self) -> int:
        """
        Get the current finalized height.
        
        Returns:
            Finalized height (-1 if nothing finalized yet)
        """
        return self._finalized_height
    
    def get_finalized_hash(self) -> Optional[str]:
        """
        Get the hash of the block at finalized_height.
        
        Returns:
            Block hash at finalized_height, or None if nothing finalized
        """
        return self._finalized_hash
    
    def is_height_finalized(self, height: int) -> bool:
        """
        Check if a height is finalized.
        
        Args:
            height: Block height to check
            
        Returns:
            True if height <= finalized_height
        """
        return height <= self._finalized_height
    
    def record_attestation(self, attestation: FinalityAttestation) -> bool:
        """
        Record a finality attestation.
        
        Attestations are deduplicated by (height, block_hash, validator_id).
        
        Args:
            attestation: FinalityAttestation to record
            
        Returns:
            True if recorded (new attestation), False if duplicate
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO finality_attestations 
                (height, block_hash, validator_id, signature, seen_ts)
                VALUES (?, ?, ?, ?, ?)
            """, (
                attestation.height,
                attestation.block_hash,
                attestation.validator_id,
                attestation.signature,
                attestation.seen_ts
            ))
            self.conn.commit()
            
            # Return True if a row was inserted (not a duplicate)
            return cursor.rowcount > 0
            
        except Exception as e:
            print(f"[FINALITY] Failed to record attestation: {e}")
            return False
    
    def get_attestation_count(self, height: int, block_hash: str) -> int:
        """
        Get the number of unique attestations for a block.
        
        Args:
            height: Block height
            block_hash: Block hash
            
        Returns:
            Number of unique validator attestations
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT validator_id) as cnt
            FROM finality_attestations
            WHERE height = ? AND block_hash = ?
        """, (height, block_hash))
        row = cursor.fetchone()
        return row['cnt'] if row else 0
    
    def get_attestations_for_block(self, height: int, block_hash: str) -> List[FinalityAttestation]:
        """
        Get all attestations for a specific block.
        
        Args:
            height: Block height
            block_hash: Block hash
            
        Returns:
            List of FinalityAttestation objects
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT height, block_hash, validator_id, signature, seen_ts
            FROM finality_attestations
            WHERE height = ? AND block_hash = ?
        """, (height, block_hash))
        
        attestations = []
        for row in cursor.fetchall():
            attestations.append(FinalityAttestation(
                height=row['height'],
                block_hash=row['block_hash'],
                validator_id=row['validator_id'],
                signature=row['signature'],
                seen_ts=row['seen_ts']
            ))
        return attestations
    
    def get_attesting_validators(self, height: int, block_hash: str) -> Set[str]:
        """
        Get the set of validators who have attested to a block.
        
        Args:
            height: Block height
            block_hash: Block hash
            
        Returns:
            Set of validator IDs who have attested
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT DISTINCT validator_id
            FROM finality_attestations
            WHERE height = ? AND block_hash = ?
        """, (height, block_hash))
        
        return {row['validator_id'] for row in cursor.fetchall()}
    
    def check_and_advance_finality(self, height: int, block_hash: str, 
                                    eligible_validators: Set[str],
                                    current_head_height: int) -> bool:
        """
        Check if a block has reached finality quorum and advance finalized_height.
        
        CRITICAL: finalized_height advances SEQUENTIALLY (never skip heights).
        
        Args:
            height: Block height to check
            block_hash: Block hash to check
            eligible_validators: Set of eligible validator IDs at this height
            current_head_height: Current chain head height
            
        Returns:
            True if block was finalized, False otherwise
        """
        # FINALITY_DEPTH check: only finalize when head is far enough ahead
        if current_head_height < height + FINALITY_DEPTH:
            return False
        
        # Can only finalize the next sequential height
        if height != self._finalized_height + 1:
            return False
        
        # Compute quorum
        quorum = compute_finality_quorum(len(eligible_validators))
        
        # Count attestations from eligible validators only
        attesting_validators = self.get_attesting_validators(height, block_hash)
        eligible_attestations = attesting_validators & eligible_validators
        attestation_count = len(eligible_attestations)
        
        if attestation_count >= quorum:
            # Quorum reached - finalize this block
            self._set_finalized(height, block_hash)
            print(f"[FINALITY] FINALIZED height={height} hash={block_hash[:16]}... "
                  f"quorum={attestation_count}/{quorum}")
            return True
        
        return False
    
    def _set_finalized(self, height: int, block_hash: str):
        """
        Set the finalized height and hash (internal, monotonic).
        
        CRITICAL: This must only be called with height = finalized_height + 1
        to ensure sequential advancement.
        
        Args:
            height: New finalized height
            block_hash: Hash of finalized block
        """
        if height <= self._finalized_height:
            # Safety check: never go backwards
            print(f"[FINALITY] WARNING: Attempted to set finalized_height backwards "
                  f"({height} <= {self._finalized_height})")
            return
        
        try:
            cursor = self.conn.cursor()
            now = int(time.time())
            
            # Record finalized block
            cursor.execute("""
                INSERT OR REPLACE INTO finalized_blocks (height, block_hash, finalized_at)
                VALUES (?, ?, ?)
            """, (height, block_hash, now))
            
            # Update finality state
            cursor.execute("""
                UPDATE finality_state 
                SET finalized_height = ?, finalized_hash = ?, updated_at = ?
                WHERE id = 1
            """, (height, block_hash, now))
            
            self.conn.commit()
            
            # Update cache
            self._finalized_height = height
            self._finalized_hash = block_hash
            
        except Exception as e:
            self.conn.rollback()
            print(f"[FINALITY] Failed to set finalized: {e}")
    
    def try_advance_finality(self, get_block_hash_at_height, 
                              get_eligible_validators_at_height,
                              current_head_height: int) -> int:
        """
        Try to advance finality as far as possible.
        
        This is called periodically to check if any pending blocks
        have reached finality quorum.
        
        Args:
            get_block_hash_at_height: Function(height) -> block_hash
            get_eligible_validators_at_height: Function(height) -> Set[validator_id]
            current_head_height: Current chain head height
            
        Returns:
            Number of blocks finalized
        """
        finalized_count = 0
        
        # Try to finalize blocks sequentially from finalized_height + 1
        check_height = self._finalized_height + 1
        
        while check_height <= current_head_height - FINALITY_DEPTH:
            block_hash = get_block_hash_at_height(check_height)
            if not block_hash:
                break
            
            eligible = get_eligible_validators_at_height(check_height)
            
            if self.check_and_advance_finality(check_height, block_hash, 
                                                eligible, current_head_height):
                finalized_count += 1
                check_height += 1
            else:
                # Can't finalize this height yet, stop trying
                break
        
        return finalized_count
    
    def get_finality_info(self, height: int, block_hash: str, 
                          eligible_count: int) -> Dict:
        """
        Get finality information for a block.
        
        Args:
            height: Block height
            block_hash: Block hash
            eligible_count: Number of eligible validators
            
        Returns:
            Dict with finality status and attestation info
        """
        attestation_count = self.get_attestation_count(height, block_hash)
        quorum = compute_finality_quorum(eligible_count)
        is_finalized = self.is_height_finalized(height)
        
        return {
            "height": height,
            "block_hash": block_hash,
            "is_finalized": is_finalized,
            "attestation_count": attestation_count,
            "quorum": quorum,
            "quorum_reached": attestation_count >= quorum,
            "finalized_height": self._finalized_height
        }
    
    def remove_attestations_above_height(self, height: int) -> int:
        """
        Remove attestations above a certain height (for rollback).
        
        CRITICAL: This should only be called for heights > finalized_height.
        
        Args:
            height: Height to remove attestations above
            
        Returns:
            Number of attestations removed
        """
        if height <= self._finalized_height:
            print(f"[FINALITY] VIOLATION: Attempted to remove attestations at/below "
                  f"finalized_height ({height} <= {self._finalized_height})")
            return 0
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM finality_attestations WHERE height > ?", (height,))
            count = cursor.fetchone()['cnt']
            
            cursor.execute("DELETE FROM finality_attestations WHERE height > ?", (height,))
            self.conn.commit()
            
            if count > 0:
                print(f"[FINALITY] Removed {count} attestations above height {height}")
            
            return count
            
        except Exception as e:
            self.conn.rollback()
            print(f"[FINALITY] Failed to remove attestations: {e}")
            return 0
    
    def get_recent_attestation_stats(self, from_height: int, to_height: int) -> List[Dict]:
        """
        Get attestation statistics for a range of heights.
        
        Args:
            from_height: Start height (inclusive)
            to_height: End height (inclusive)
            
        Returns:
            List of dicts with height, block_hash, attestation_count
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT height, block_hash, COUNT(DISTINCT validator_id) as cnt
            FROM finality_attestations
            WHERE height >= ? AND height <= ?
            GROUP BY height, block_hash
            ORDER BY height DESC
        """, (from_height, to_height))
        
        return [
            {
                "height": row['height'],
                "block_hash": row['block_hash'],
                "attestation_count": row['cnt']
            }
            for row in cursor.fetchall()
        ]
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
