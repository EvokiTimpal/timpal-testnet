"""
TIMPAL Blockchain - Pure-Python Cross-Platform Storage
Works identically on macOS, Windows, Linux, Docker, and Replit
NO system dependencies, NO C++ compilation required
"""

import json
import os
import tempfile
import shutil
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path


class BlockchainStorage:
    """
    Pure-Python storage backend for TIMPAL Genesis
    
    Features:
    - 100% portable (macOS, Windows, Linux, Docker)
    - JSON-based block and state storage
    - Atomic writes with temp files
    - No LevelDB, no plyvel, no C++ dependencies
    - Identical API to storage.py for seamless integration
    
    Storage structure:
    - data_dir/ledger/blocks/block_<height>.json  (blocks by height)
    - data_dir/ledger/hashes/<hash>.json  (blocks by hash)
    - data_dir/ledger/state.json  (balances, nonces, validator data)
    - data_dir/ledger/metadata.json  (chain height, timestamps)
    """
    
    def __init__(self, data_dir: str = "blockchain_data"):
        self.data_dir = data_dir
        self.blocks_dir = os.path.join(data_dir, "ledger", "blocks")
        self.hashes_dir = os.path.join(data_dir, "ledger", "hashes")
        self.state_file = os.path.join(data_dir, "ledger", "state.json")
        self.metadata_file = os.path.join(data_dir, "ledger", "metadata.json")
        self.snapshots_dir = os.path.join(data_dir, "snapshots")
        
        # Create directories
        os.makedirs(self.blocks_dir, exist_ok=True)
        os.makedirs(self.hashes_dir, exist_ok=True)
        os.makedirs(self.snapshots_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        
        # Initialize metadata if missing
        if not os.path.exists(self.metadata_file):
            self._atomic_write(self.metadata_file, {})
        
        # Initialize state if missing
        if not os.path.exists(self.state_file):
            self._atomic_write(self.state_file, {
                'balances': {},
                'nonces': {},
                'total_emitted_pals': 0,
                'validator_set': [],
                'validator_registry': {},
                'finality_checkpoints': {},
                'validator_economics': {}
            })
        
        print(f"📦 Pure-Python storage initialized at {self.blocks_dir}")
    
    def _atomic_write(self, file_path: str, data: Any):
        """
        Atomic file write using temp file + rename
        Prevents corruption if process crashes during write
        """
        temp_fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(file_path),
            prefix='.tmp_',
            suffix='.json'
        )
        
        try:
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            
            # Atomic rename (POSIX guarantees atomicity)
            if os.name == 'nt':  # Windows
                if os.path.exists(file_path):
                    os.remove(file_path)
            os.rename(temp_path, file_path)
        except:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    
    def _read_json(self, file_path: str) -> Optional[Any]:
        """Safely read JSON file"""
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    
    # ===== Block Storage =====
    
    def put_block(self, height: int, block_data: Dict):
        """Store block by height"""
        block_file = os.path.join(self.blocks_dir, f"block_{height}.json")
        self._atomic_write(block_file, block_data)
    
    def get_block(self, height: int) -> Optional[Dict]:
        """Retrieve block by height"""
        block_file = os.path.join(self.blocks_dir, f"block_{height}.json")
        return self._read_json(block_file)
    
    def put_block_by_hash(self, block_hash: str, block_data: Dict):
        """Store block by hash (for quick lookups)"""
        hash_file = os.path.join(self.hashes_dir, f"{block_hash}.json")
        self._atomic_write(hash_file, block_data)
    
    def get_block_by_hash(self, block_hash: str) -> Optional[Dict]:
        """Retrieve block by hash"""
        hash_file = os.path.join(self.hashes_dir, f"{block_hash}.json")
        return self._read_json(hash_file)
    
    # ===== State Storage =====
    
    def put_state(self, state_key: str, state_data: Any):
        """Store state data (balances, nonces, etc.)"""
        state = self._read_json(self.state_file) or {}
        state[state_key] = state_data
        self._atomic_write(self.state_file, state)
    
    def get_state(self, state_key: str) -> Optional[Any]:
        """Retrieve state data"""
        state = self._read_json(self.state_file) or {}
        return state.get(state_key)
    
    # ===== Metadata Storage =====
    
    def put_metadata(self, meta_key: str, meta_value: Any):
        """Store metadata (chain height, checkpoints, etc.)"""
        metadata = self._read_json(self.metadata_file) or {}
        metadata[meta_key] = meta_value
        self._atomic_write(self.metadata_file, metadata)
    
    def get_metadata(self, meta_key: str) -> Optional[Any]:
        """Retrieve metadata"""
        metadata = self._read_json(self.metadata_file) or {}
        return metadata.get(meta_key)
    
    # ===== High-Level Save/Load Operations =====
    
    def save_new_block(self, height: int, block_dict: Dict) -> bool:
        """
        Save a new block to storage (both by height and by hash)
        
        Args:
            height: Block height (0 for genesis)
            block_dict: Complete block data including transactions, timestamp, etc.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure block has a hash
            if 'block_hash' not in block_dict:
                block_json = json.dumps(block_dict, sort_keys=True)
                block_dict['block_hash'] = hashlib.sha256(block_json.encode()).hexdigest()
            
            # Save block by height
            self.put_block(height, block_dict)
            
            # Save block by hash (for quick hash lookups)
            self.put_block_by_hash(block_dict['block_hash'], block_dict)
            
            # Update chain height metadata
            current_height = self.get_metadata('chain_height')
            if current_height is None or height > current_height:
                self.put_metadata('chain_height', height)
            
            self.put_metadata('last_saved', datetime.now().isoformat())
            
            return True
            
        except Exception as e:
            print(f"❌ [Storage] Error saving block {height}: {e}")
            return False
    
    def save_state_only(self, state: Dict):
        """
        Save only state data (balances, nonces, validator data) without blocks
        
        Use this for periodic state updates without rewriting blocks.
        
        Args:
            state: Dictionary containing balances, nonces, validator data, etc.
        """
        try:
            # Update all state components atomically
            state_data = {
                'balances': state.get('balances', {}),
                'nonces': state.get('nonces', {}),
                'total_emitted_pals': state.get('total_emitted_pals', 0),
                'validator_set': state.get('validator_set', []),
                'validator_registry': state.get('validator_registry', {}),
                'finality_checkpoints': state.get('finality_checkpoints', {}),
                'validator_economics': state.get('validator_economics', {})
            }
            
            self._atomic_write(self.state_file, state_data)
            self.put_metadata('last_saved', datetime.now().isoformat())
            
        except Exception as e:
            print(f"❌ [Storage] Error saving state: {e}")
            raise
    
    def save_full_state(self, state: Dict):
        """
        Save complete blockchain state atomically (initial load only)
        
        WARNING: This rewrites entire chain - only use for initial setup or recovery!
        For normal operation, use save_new_block() and save_state_only().
        
        Args:
            state: Complete blockchain state including blocks and account data
        """
        try:
            # Save state data
            state_data = {
                'balances': state.get('balances', {}),
                'nonces': state.get('nonces', {}),
                'total_emitted_pals': state.get('total_emitted_pals', 0),
                'validator_set': state.get('validator_set', []),
                'validator_registry': state.get('validator_registry', {}),
                'finality_checkpoints': state.get('finality_checkpoints', {}),
                'validator_economics': state.get('validator_economics', {})
            }
            self._atomic_write(self.state_file, state_data)
            
            # Save all blocks
            blocks = state.get('blocks', [])
            for i, block_dict in enumerate(blocks):
                if 'block_hash' not in block_dict:
                    block_json = json.dumps(block_dict, sort_keys=True)
                    block_dict['block_hash'] = hashlib.sha256(block_json.encode()).hexdigest()
                
                height = block_dict.get('height', i)
                self.put_block(height, block_dict)
                self.put_block_by_hash(block_dict['block_hash'], block_dict)
            
            # Update metadata
            if blocks:
                max_height = len(blocks) - 1
                self.put_metadata('chain_height', max_height)
            
            self.put_metadata('last_saved', datetime.now().isoformat())
            
            print(f"✅ Saved full state to JSON: {len(blocks)} blocks")
            
        except Exception as e:
            print(f"❌ [Storage] Error saving full state: {e}")
            raise
    
    def load_full_state(self) -> Optional[Dict]:
        """
        Load complete blockchain state from storage
        
        Returns:
            Complete state dict or None if no state exists
        """
        try:
            chain_height = self.get_metadata('chain_height')
            if chain_height is None:
                return None
            
            # Load all blocks
            blocks = []
            for i in range(chain_height + 1):
                block = self.get_block(i)
                if block:
                    blocks.append(block)
                else:
                    print(f"⚠️  Warning: Block {i} missing (chain height: {chain_height})")
            
            # Load state data
            state_data = self._read_json(self.state_file) or {}
            
            state = {
                'balances': state_data.get('balances', {}),
                'nonces': state_data.get('nonces', {}),
                'total_emitted_pals': state_data.get('total_emitted_pals', 0),
                'validator_set': state_data.get('validator_set', []),
                'validator_registry': state_data.get('validator_registry', {}),
                'finality_checkpoints': state_data.get('finality_checkpoints', {}),
                'validator_economics': state_data.get('validator_economics', {}),
                'blocks': blocks
            }
            
            print(f"✅ Loaded state from JSON: {len(blocks)} blocks, {len(state['balances'])} accounts")
            
            return state
            
        except Exception as e:
            print(f"❌ [Storage] Error loading state: {e}")
            return None
    
    def verify_integrity(self) -> Dict:
        """
        Verify database integrity
        
        Returns:
            Dictionary with integrity check results
        """
        issues = []
        
        try:
            # Check metadata file
            if not os.path.exists(self.metadata_file):
                issues.append("Missing metadata.json")
            
            # Check state file
            if not os.path.exists(self.state_file):
                issues.append("Missing state.json")
            
            # Check blocks directory
            if not os.path.exists(self.blocks_dir):
                issues.append("Missing blocks directory")
            
            # Verify chain continuity
            chain_height = self.get_metadata('chain_height')
            if chain_height is not None:
                missing_blocks = []
                for i in range(chain_height + 1):
                    block_file = os.path.join(self.blocks_dir, f"block_{i}.json")
                    if not os.path.exists(block_file):
                        missing_blocks.append(i)
                
                if missing_blocks:
                    issues.append(f"Missing blocks: {missing_blocks[:10]}")  # Show first 10
            
            return {
                'healthy': len(issues) == 0,
                'checks_performed': ['metadata', 'state', 'blocks', 'continuity'],
                'issues_found': issues
            }
            
        except Exception as e:
            return {
                'healthy': False,
                'checks_performed': ['basic'],
                'issues_found': [f"Integrity check error: {e}"]
            }
    
    def create_snapshot(self, snapshot_name: str):
        """Create a database snapshot for backup/recovery"""
        try:
            snapshot_dir = os.path.join(self.snapshots_dir, snapshot_name)
            if os.path.exists(snapshot_dir):
                shutil.rmtree(snapshot_dir)
            
            # Copy entire ledger directory
            ledger_dir = os.path.dirname(self.blocks_dir)
            shutil.copytree(ledger_dir, snapshot_dir)
            
            print(f"📸 Snapshot created: {snapshot_name}")
            
        except Exception as e:
            print(f"❌ Snapshot creation failed: {e}")
    
    def restore_from_snapshot(self, snapshot_name: str):
        """Restore from a snapshot"""
        try:
            snapshot_dir = os.path.join(self.snapshots_dir, snapshot_name)
            if not os.path.exists(snapshot_dir):
                raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")
            
            # Clear current data
            ledger_dir = os.path.dirname(self.blocks_dir)
            if os.path.exists(ledger_dir):
                shutil.rmtree(ledger_dir)
            
            # Restore from snapshot
            shutil.copytree(snapshot_dir, ledger_dir)
            
            print(f"♻️  Restored from snapshot: {snapshot_name}")
            
        except Exception as e:
            print(f"❌ Snapshot restoration failed: {e}")
            raise
    
    def close(self):
        """Close storage (no-op for file-based storage, but included for API compatibility)"""
        print(f"📦 Pure-Python storage closed")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class CrashRecovery:
    """
    Crash recovery system for TIMPAL blockchain
    
    Features:
    - Detect incomplete state saves
    - Restore from last known good state
    - Automatic snapshot creation
    """
    
    def __init__(self, storage: BlockchainStorage):
        self.storage = storage
        self.snapshots_dir = storage.snapshots_dir
    
    def check_and_recover(self) -> Dict:
        """
        Check for crash indicators and recover if needed
        
        Returns:
            Recovery report
        """
        report = {
            'crash_detected': False,
            'recovery_performed': False,
            'state_restored': False,
            'integrity_check': None
        }
        
        integrity = self.storage.verify_integrity()
        report['integrity_check'] = integrity
        
        if not integrity['healthy']:
            print("⚠️  Database integrity issues detected!")
            report['crash_detected'] = True
            
            snapshots = self._list_snapshots()
            if snapshots:
                latest_snapshot = snapshots[-1]
                print(f"♻️  Attempting recovery from snapshot: {latest_snapshot}")
                
                try:
                    self.storage.restore_from_snapshot(latest_snapshot)
                    report['recovery_performed'] = True
                    report['state_restored'] = True
                    print("✅ Recovery successful!")
                except Exception as e:
                    print(f"❌ Recovery failed: {e}")
                    report['state_restored'] = False
            else:
                print("⚠️  No snapshots available for recovery")
        else:
            print("✅ Database integrity check passed")
        
        return report
    
    def _list_snapshots(self) -> List[str]:
        """List available snapshots, sorted by date"""
        if not os.path.exists(self.snapshots_dir):
            return []
        
        snapshots = []
        for item in os.listdir(self.snapshots_dir):
            path = os.path.join(self.snapshots_dir, item)
            if os.path.isdir(path):
                snapshots.append(item)
        
        return sorted(snapshots)
    
    def create_recovery_snapshot(self, block_height: int):
        """
        Create periodic recovery snapshots
        
        Call this every N blocks (e.g., every 1000 blocks)
        """
        snapshot_name = f"recovery_{block_height}"
        self.storage.create_snapshot(snapshot_name)
        
        self._cleanup_old_snapshots(keep=5)
    
    def _cleanup_old_snapshots(self, keep: int = 5):
        """Keep only the N most recent snapshots"""
        snapshots = self._list_snapshots()
        if len(snapshots) > keep:
            to_remove = snapshots[:-keep]
            for snapshot in to_remove:
                snapshot_path = os.path.join(self.snapshots_dir, snapshot)
                try:
                    shutil.rmtree(snapshot_path)
                    print(f"🗑️  Removed old snapshot: {snapshot}")
                except Exception as e:
                    print(f"⚠️  Failed to remove snapshot {snapshot}: {e}")
