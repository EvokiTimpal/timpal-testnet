"""
SQLITE PERSISTENCE LAYER FOR HISTORICAL STATE

Provides ACID-compliant storage for historical state data with:
- Crash consistency (all-or-nothing writes)
- Integrity verification (checksums on all data)
- Efficient retrieval (indexed by height and hash)
- Automatic recovery from partial writes

ARCHITECTURE:
- Main tables: historical_records, validator_frames, epoch_snapshots, am_snapshots
- All writes are transactional
- Checksums stored with each record for integrity verification
- WAL mode for concurrent reads during writes

USAGE:
    storage = SQLiteHistoricalStorage("ledger/history.db")
    storage.store_record(record, frame, epoch_snapshot, am_snapshot)
    record = storage.get_record(height)
"""

import sqlite3
import json
import hashlib
import os
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import asdict

from app.historical_state import (
    ValidatorStateFrame, EpochSnapshot, HistoricalStateRecord,
    ValidatorEntry, LivenessFilterState
)


class SQLiteHistoricalStorage:
    """
    SQLite-based persistence for historical state data.
    
    Provides ACID guarantees for all operations, ensuring:
    - Atomicity: All writes succeed or all fail
    - Consistency: Data integrity is always maintained
    - Isolation: Concurrent reads don't see partial writes
    - Durability: Committed data survives crashes
    """
    
    SCHEMA_VERSION = 1
    
    def __init__(self, db_path: str, auto_migrate: bool = True):
        """
        Initialize SQLite storage.
        
        Args:
            db_path: Path to SQLite database file
            auto_migrate: Automatically run migrations on open
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        
        if auto_migrate:
            self._initialize_schema()
    
    def _initialize_schema(self):
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historical_records (
                height INTEGER PRIMARY KEY,
                block_hash TEXT NOT NULL,
                timestamp REAL NOT NULL,
                epoch_number INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                record_checksum TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS validator_frames (
                height INTEGER PRIMARY KEY,
                block_hash TEXT NOT NULL,
                is_full_frame INTEGER NOT NULL,
                frame_json TEXT NOT NULL,
                frame_checksum TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (height) REFERENCES historical_records(height) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS epoch_snapshots (
                height INTEGER PRIMARY KEY,
                epoch_number INTEGER NOT NULL,
                epoch_seed TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                snapshot_checksum TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS am_snapshots (
                height INTEGER PRIMARY KEY,
                epoch_number INTEGER NOT NULL,
                epoch_seed TEXT NOT NULL,
                combined_liveness_set TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                snapshot_checksum TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (height) REFERENCES historical_records(height) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_epoch ON historical_records(epoch_number)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_hash ON historical_records(block_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_epoch_snapshots_epoch ON epoch_snapshots(epoch_number)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_am_snapshots_epoch ON am_snapshots(epoch_number)
        """)
        
        cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))
        
        self.conn.commit()
    
    def _compute_checksum(self, data: str) -> str:
        """Compute SHA-256 checksum of data."""
        return hashlib.sha256(data.encode()).hexdigest()
    
    def _verify_checksum(self, data: str, checksum: str) -> bool:
        """Verify data integrity against checksum."""
        return self._compute_checksum(data) == checksum
    
    def store(
        self,
        record: HistoricalStateRecord,
        validator_frame: ValidatorStateFrame,
        epoch_snapshot: Optional[EpochSnapshot] = None,
        am_snapshot: Optional[Dict] = None
    ) -> bool:
        """
        Store historical state atomically.
        
        All data is written in a single transaction - either all succeed
        or all fail, ensuring consistency.
        
        Args:
            record: HistoricalStateRecord to store
            validator_frame: ValidatorStateFrame for this height
            epoch_snapshot: EpochSnapshot if this is an epoch boundary
            am_snapshot: AttestationManager snapshot dict
        
        Returns:
            True if stored successfully, False on error
        """
        try:
            cursor = self.conn.cursor()
            
            record_json = json.dumps(record.to_dict(), sort_keys=True)
            record_checksum = self._compute_checksum(record_json)
            
            cursor.execute("""
                INSERT OR REPLACE INTO historical_records 
                (height, block_hash, timestamp, epoch_number, record_json, record_checksum)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                record.block_height,
                record.block_hash,
                record.timestamp,
                record.epoch_number,
                record_json,
                record_checksum
            ))
            
            frame_json = json.dumps(validator_frame.to_dict(), sort_keys=True)
            frame_checksum = self._compute_checksum(frame_json)
            
            cursor.execute("""
                INSERT OR REPLACE INTO validator_frames
                (height, block_hash, is_full_frame, frame_json, frame_checksum)
                VALUES (?, ?, ?, ?, ?)
            """, (
                validator_frame.block_height,
                validator_frame.block_hash,
                1 if validator_frame.is_full_frame else 0,
                frame_json,
                frame_checksum
            ))
            
            if epoch_snapshot:
                snapshot_json = json.dumps(epoch_snapshot.to_dict(), sort_keys=True)
                snapshot_checksum = self._compute_checksum(snapshot_json)
                
                cursor.execute("""
                    INSERT OR REPLACE INTO epoch_snapshots
                    (height, epoch_number, epoch_seed, snapshot_json, snapshot_checksum)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    record.block_height,
                    epoch_snapshot.epoch_number,
                    epoch_snapshot.epoch_seed,
                    snapshot_json,
                    snapshot_checksum
                ))
            
            if am_snapshot:
                am_json = json.dumps(am_snapshot, sort_keys=True)
                am_checksum = self._compute_checksum(am_json)
                
                epoch_seed = am_snapshot.get('epoch_seed', '')
                epoch_number = am_snapshot.get('epoch_number', 0)
                combined_liveness = json.dumps(am_snapshot.get('combined_liveness_set', []))
                
                cursor.execute("""
                    INSERT OR REPLACE INTO am_snapshots
                    (height, epoch_number, epoch_seed, combined_liveness_set, snapshot_json, snapshot_checksum)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    record.block_height,
                    epoch_number,
                    epoch_seed,
                    combined_liveness,
                    am_json,
                    am_checksum
                ))
            
            self.conn.commit()
            return True
            
        except Exception as e:
            self.conn.rollback()
            print(f"❌ SQLite store failed at height {record.block_height}: {e}")
            return False
    
    def get_record(self, height: int) -> Optional[HistoricalStateRecord]:
        """
        Retrieve HistoricalStateRecord by height.
        
        Verifies checksum before returning to detect corruption.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT record_json, record_checksum FROM historical_records WHERE height = ?
        """, (height,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        record_json, checksum = row['record_json'], row['record_checksum']
        
        if not self._verify_checksum(record_json, checksum):
            print(f"⚠️ INTEGRITY ERROR: Record at height {height} failed checksum")
            return None
        
        try:
            data = json.loads(record_json)
            return HistoricalStateRecord.from_dict(data)
        except Exception as e:
            print(f"⚠️ Parse error at height {height}: {e}")
            return None
    
    def get_frame(self, height: int) -> Optional[ValidatorStateFrame]:
        """
        Retrieve ValidatorStateFrame by height.
        
        Verifies checksum before returning.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT frame_json, frame_checksum FROM validator_frames WHERE height = ?
        """, (height,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        frame_json, checksum = row['frame_json'], row['frame_checksum']
        
        if not self._verify_checksum(frame_json, checksum):
            print(f"⚠️ INTEGRITY ERROR: Frame at height {height} failed checksum")
            return None
        
        try:
            data = json.loads(frame_json)
            return ValidatorStateFrame.from_dict(data)
        except Exception as e:
            print(f"⚠️ Parse error at height {height}: {e}")
            return None
    
    def get_epoch_snapshot(self, height: int) -> Optional[EpochSnapshot]:
        """
        Retrieve EpochSnapshot by height.
        
        Returns None if no epoch snapshot at this height.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT snapshot_json, snapshot_checksum FROM epoch_snapshots WHERE height = ?
        """, (height,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        snapshot_json, checksum = row['snapshot_json'], row['snapshot_checksum']
        
        if not self._verify_checksum(snapshot_json, checksum):
            print(f"⚠️ INTEGRITY ERROR: Epoch snapshot at height {height} failed checksum")
            return None
        
        try:
            data = json.loads(snapshot_json)
            return EpochSnapshot.from_dict(data)
        except Exception as e:
            print(f"⚠️ Parse error at height {height}: {e}")
            return None
    
    def get_am_snapshot(self, height: int) -> Optional[Dict]:
        """
        Retrieve AttestationManager snapshot by height.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT snapshot_json, snapshot_checksum FROM am_snapshots WHERE height = ?
        """, (height,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        snapshot_json, checksum = row['snapshot_json'], row['snapshot_checksum']
        
        if not self._verify_checksum(snapshot_json, checksum):
            print(f"⚠️ INTEGRITY ERROR: AM snapshot at height {height} failed checksum")
            return None
        
        try:
            return json.loads(snapshot_json)
        except Exception as e:
            print(f"⚠️ Parse error at height {height}: {e}")
            return None
    
    def get_nearest_epoch_snapshot(self, height: int) -> Tuple[Optional[EpochSnapshot], int]:
        """
        Get the nearest epoch snapshot at or before the given height.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT height, snapshot_json, snapshot_checksum 
            FROM epoch_snapshots 
            WHERE height <= ?
            ORDER BY height DESC
            LIMIT 1
        """, (height,))
        
        row = cursor.fetchone()
        if not row:
            return None, -1
        
        snap_height = row['height']
        snapshot_json = row['snapshot_json']
        checksum = row['snapshot_checksum']
        
        if not self._verify_checksum(snapshot_json, checksum):
            print(f"⚠️ INTEGRITY ERROR: Epoch snapshot at height {snap_height} failed checksum")
            return None, -1
        
        try:
            data = json.loads(snapshot_json)
            return EpochSnapshot.from_dict(data), snap_height
        except Exception as e:
            print(f"⚠️ Parse error at height {snap_height}: {e}")
            return None, -1
    
    def remove_above_height(self, height: int) -> int:
        """
        Remove all records above a certain height (for rollback).
        
        Returns number of records removed.
        """
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("SELECT COUNT(*) as cnt FROM historical_records WHERE height > ?", (height,))
            count = cursor.fetchone()['cnt']
            
            cursor.execute("DELETE FROM am_snapshots WHERE height > ?", (height,))
            cursor.execute("DELETE FROM epoch_snapshots WHERE height > ?", (height,))
            cursor.execute("DELETE FROM validator_frames WHERE height > ?", (height,))
            cursor.execute("DELETE FROM historical_records WHERE height > ?", (height,))
            
            self.conn.commit()
            
            if count > 0:
                print(f"🗑️ SQLite: Removed {count} records above height {height}")
            
            return count
            
        except Exception as e:
            self.conn.rollback()
            print(f"❌ SQLite remove_above_height failed: {e}")
            return 0
    
    def get_height_range(self) -> Tuple[int, int]:
        """Get the range of heights stored (min, max)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT MIN(height) as min_h, MAX(height) as max_h FROM historical_records")
        row = cursor.fetchone()
        
        min_h = row['min_h'] if row['min_h'] is not None else 0
        max_h = row['max_h'] if row['max_h'] is not None else -1
        
        return min_h, max_h
    
    def has_height(self, height: int) -> bool:
        """Check if we have a record for the given height."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM historical_records WHERE height = ?", (height,))
        return cursor.fetchone() is not None
    
    def get_state_at_height(self, height: int) -> Optional[Dict[str, Any]]:
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
        
        return {
            'record': record,
            'frame': self.get_frame(height),
            'epoch_snapshot': self.get_epoch_snapshot(height),
            'am_snapshot': self.get_am_snapshot(height)
        }
    
    def get_proposer_queue_at_height(self, height: int) -> Optional[List[str]]:
        """
        Get the VRF-ordered proposer queue for a specific height.
        
        Uses stored proposer_queue if available from the record.
        
        Args:
            height: Block height for proposer queue
        
        Returns:
            Ordered list of proposer addresses, or None if unavailable
        """
        record = self.get_record(height)
        if record and record.proposer_queue:
            return record.proposer_queue
        return None
    
    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """
        Verify integrity of all stored data.
        
        Returns:
            Tuple of (all_valid, list_of_errors)
        """
        errors = []
        cursor = self.conn.cursor()
        
        cursor.execute("SELECT height, record_json, record_checksum FROM historical_records")
        for row in cursor.fetchall():
            if not self._verify_checksum(row['record_json'], row['record_checksum']):
                errors.append(f"Record at height {row['height']} checksum mismatch")
        
        cursor.execute("SELECT height, frame_json, frame_checksum FROM validator_frames")
        for row in cursor.fetchall():
            if not self._verify_checksum(row['frame_json'], row['frame_checksum']):
                errors.append(f"Frame at height {row['height']} checksum mismatch")
        
        cursor.execute("SELECT height, snapshot_json, snapshot_checksum FROM epoch_snapshots")
        for row in cursor.fetchall():
            if not self._verify_checksum(row['snapshot_json'], row['snapshot_checksum']):
                errors.append(f"Epoch snapshot at height {row['height']} checksum mismatch")
        
        cursor.execute("SELECT height, snapshot_json, snapshot_checksum FROM am_snapshots")
        for row in cursor.fetchall():
            if not self._verify_checksum(row['snapshot_json'], row['snapshot_checksum']):
                errors.append(f"AM snapshot at height {row['height']} checksum mismatch")
        
        cursor.execute("SELECT height, epoch_seed FROM am_snapshots")
        for row in cursor.fetchall():
            if not row['epoch_seed'] or len(row['epoch_seed']) == 0:
                errors.append(f"AM snapshot at height {row['height']} has empty epoch_seed")
        
        cursor.execute("SELECT height, combined_liveness_set FROM am_snapshots")
        for row in cursor.fetchall():
            try:
                liveness = json.loads(row['combined_liveness_set'])
                if not liveness or len(liveness) == 0:
                    errors.append(f"AM snapshot at height {row['height']} has empty liveness set")
            except:
                errors.append(f"AM snapshot at height {row['height']} has invalid liveness set")
        
        all_valid = len(errors) == 0
        
        if all_valid:
            min_h, max_h = self.get_height_range()
            print(f"✅ SQLite integrity check passed: heights {min_h} to {max_h}")
        else:
            print(f"❌ SQLite integrity check failed: {len(errors)} errors found")
            for error in errors[:10]:
                print(f"   - {error}")
            if len(errors) > 10:
                print(f"   ... and {len(errors) - 10} more errors")
        
        return all_valid, errors
    
    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        cursor = self.conn.cursor()
        
        stats = {}
        
        cursor.execute("SELECT COUNT(*) as cnt FROM historical_records")
        stats['total_records'] = cursor.fetchone()['cnt']
        
        cursor.execute("SELECT COUNT(*) as cnt FROM validator_frames")
        stats['total_frames'] = cursor.fetchone()['cnt']
        
        cursor.execute("SELECT COUNT(*) as cnt FROM epoch_snapshots")
        stats['total_epoch_snapshots'] = cursor.fetchone()['cnt']
        
        cursor.execute("SELECT COUNT(*) as cnt FROM am_snapshots")
        stats['total_am_snapshots'] = cursor.fetchone()['cnt']
        
        min_h, max_h = self.get_height_range()
        stats['min_height'] = min_h
        stats['max_height'] = max_h
        
        if os.path.exists(self.db_path):
            stats['db_size_bytes'] = os.path.getsize(self.db_path)
            stats['db_size_mb'] = stats['db_size_bytes'] / (1024 * 1024)
        
        return stats
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class MigrationManager:
    """
    Handles migration from JSON-based storage to SQLite.
    """
    
    @staticmethod
    def migrate_from_json_log(
        json_log,  # HistoricalStateLog instance
        sqlite_storage: SQLiteHistoricalStorage,
        batch_size: int = 100,
        strict_integrity: bool = True
    ) -> Tuple[int, int]:
        """
        Migrate data from JSON-based HistoricalStateLog to SQLite.
        
        IMPORTANT: After migration, runs integrity verification and fails hard
        if any empty epoch_seeds or liveness sets are detected (unless strict=False).
        
        Args:
            json_log: Source HistoricalStateLog instance
            sqlite_storage: Target SQLiteHistoricalStorage instance
            batch_size: Number of records to migrate per batch
            strict_integrity: If True, raises exception on integrity failures
        
        Returns:
            Tuple of (records_migrated, records_failed)
            
        Raises:
            ValueError: If strict_integrity=True and integrity check fails
        """
        migrated = 0
        failed = 0
        
        min_h, max_h = json_log.get_height_range()
        
        if max_h < 0:
            print("📦 No records to migrate")
            return 0, 0
        
        print(f"📦 Migrating {max_h - min_h + 1} records from JSON to SQLite...")
        
        for height in range(min_h, max_h + 1):
            try:
                record = json_log.get_record(height)
                frame = json_log.get_frame(height)
                epoch_snapshot = json_log.get_epoch_snapshot(height)
                am_snapshot = json_log.get_am_snapshot(height)
                
                if record and frame:
                    success = sqlite_storage.store(record, frame, epoch_snapshot, am_snapshot)
                    if success:
                        migrated += 1
                    else:
                        failed += 1
                        print(f"⚠️ Failed to migrate height {height}")
                
                if migrated > 0 and migrated % batch_size == 0:
                    print(f"   Migrated {migrated} records...")
                    
            except Exception as e:
                failed += 1
                print(f"⚠️ Error migrating height {height}: {e}")
        
        print(f"📦 Migration import: {migrated} migrated, {failed} failed")
        
        print("🔍 Running post-migration integrity verification...")
        is_valid, errors = sqlite_storage.verify_integrity()
        
        if not is_valid:
            error_msg = f"❌ MIGRATION FAILED: {len(errors)} integrity errors detected"
            print(error_msg)
            for error in errors[:5]:
                print(f"   - {error}")
            
            if strict_integrity:
                raise ValueError(
                    f"Migration integrity check failed: {len(errors)} errors. "
                    "Legacy data contains empty epoch_seeds or liveness sets. "
                    "Set strict_integrity=False to proceed with warnings only."
                )
        else:
            print(f"✅ Migration complete with verified integrity: {migrated} records")
        
        return migrated, failed
    
    @staticmethod
    def verify_migration(
        json_log,
        sqlite_storage: SQLiteHistoricalStorage,
        sample_size: int = 100
    ) -> Tuple[bool, List[str]]:
        """
        Verify migration by comparing random samples.
        
        Returns:
            Tuple of (all_match, list_of_mismatches)
        """
        mismatches = []
        
        min_h, max_h = json_log.get_height_range()
        
        if max_h < 0:
            return True, []
        
        import random
        heights_to_check = list(range(min_h, max_h + 1))
        random.shuffle(heights_to_check)
        heights_to_check = heights_to_check[:sample_size]
        
        for height in heights_to_check:
            json_record = json_log.get_record(height)
            sqlite_record = sqlite_storage.get_record(height)
            
            if json_record and not sqlite_record:
                mismatches.append(f"Height {height}: Missing in SQLite")
            elif json_record and sqlite_record:
                if json_record.block_hash != sqlite_record.block_hash:
                    mismatches.append(f"Height {height}: Block hash mismatch")
        
        all_match = len(mismatches) == 0
        
        if all_match:
            print(f"✅ Migration verified: {len(heights_to_check)} samples matched")
        else:
            print(f"❌ Migration verification failed: {len(mismatches)} mismatches")
        
        return all_match, mismatches
