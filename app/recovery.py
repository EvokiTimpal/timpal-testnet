#!/usr/bin/env python3
"""
TIMPAL Blockchain - Emergency Recovery Tool
Restore blockchain from snapshots or corrupted state
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from storage import BlockchainStorage, CrashRecovery
import argparse


def list_snapshots(storage: BlockchainStorage):
    """List all available snapshots"""
    snapshots_dir = storage.snapshots_dir
    
    if not os.path.exists(snapshots_dir):
        print("No snapshots directory found")
        return []
    
    snapshots = []
    for item in os.listdir(snapshots_dir):
        path = os.path.join(snapshots_dir, item)
        if os.path.isdir(path):
            size = sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)))
            snapshots.append((item, size))
    
    return sorted(snapshots)


def main():
    parser = argparse.ArgumentParser(description='TIMPAL Blockchain Emergency Recovery')
    parser.add_argument('--data-dir', default='blockchain_data', help='Blockchain data directory')
    parser.add_argument('--list-snapshots', action='store_true', help='List available snapshots')
    parser.add_argument('--auto-recover', action='store_true', help='Automatically recover from latest snapshot')
    parser.add_argument('--restore', type=str, help='Restore from specific snapshot name')
    parser.add_argument('--create-snapshot', action='store_true', help='Create a new snapshot')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("TIMPAL Blockchain - Emergency Recovery Tool")
    print("=" * 70)
    print()
    
    storage = BlockchainStorage(args.data_dir)
    recovery = CrashRecovery(storage)
    
    if args.list_snapshots:
        print("📸 Available Snapshots:")
        print("-" * 70)
        
        snapshots = list_snapshots(storage)
        
        if not snapshots:
            print("   No snapshots found")
        else:
            for name, size in snapshots:
                print(f"   {name} ({size / 1024 / 1024:.2f} MB)")
        
        print()
    
    elif args.create_snapshot:
        print("📸 Creating new snapshot...")
        
        current_height = storage.get_metadata('chain_height') or 0
        snapshot_path = storage.create_snapshot()
        
        print(f"✅ Snapshot created at: {snapshot_path}")
        print(f"   Chain height: {current_height:,}")
        print()
    
    elif args.auto_recover:
        print("🔧 Running automatic recovery...")
        print("-" * 70)
        
        report = recovery.check_and_recover()
        
        print()
        print("Recovery Report:")
        print(f"   Crash detected: {report['crash_detected']}")
        print(f"   Recovery performed: {report['recovery_performed']}")
        print(f"   State restored: {report['state_restored']}")
        
        if report['integrity_check']:
            print(f"   Database healthy: {report['integrity_check']['healthy']}")
        
        print()
        
        if report['state_restored']:
            print("✅ Recovery successful!")
        else:
            print("⚠️  Recovery not performed (database may be healthy)")
        
        print()
    
    elif args.restore:
        print(f"♻️  Restoring from snapshot: {args.restore}")
        print("-" * 70)
        print()
        
        confirm = input("⚠️  This will REPLACE your current database! Type 'YES' to continue: ")
        
        if confirm != 'YES':
            print("❌ Restore cancelled")
            storage.close()
            return
        
        try:
            storage.restore_from_snapshot(args.restore)
            print()
            print("✅ Restore successful!")
            
            stats = storage.get_statistics()
            print(f"   Chain height: {stats['chain_height']:,}")
            print(f"   Total accounts: {stats['total_accounts']:,}")
            print()
        
        except Exception as e:
            print(f"❌ Restore failed: {e}")
            print()
    
    else:
        parser.print_help()
    
    storage.close()


if __name__ == "__main__":
    main()
