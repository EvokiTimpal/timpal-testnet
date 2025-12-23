"""
Device Fingerprinting - Enforce One Node Per Device
====================================================
Critical security feature to prevent reward gaming
"""

import hashlib
import platform
import subprocess
import uuid
import os
import fcntl
import json
from pathlib import Path
from typing import Optional


class DeviceFingerprint:
    """
    Creates a unique, persistent device fingerprint based on hardware.
    Prevents multiple nodes from running on the same physical device.
    """
    
    LOCKFILE_PATH = os.path.expanduser("~/.timpal_node.lock")
    DEVICE_ID_PATH = os.path.expanduser("~/.timpal_device_id")
    
    def __init__(self):
        self.device_id = self._get_or_create_device_id()
        self.lockfile = None
    
    def _get_hardware_identifiers(self) -> list:
        """Collect multiple hardware identifiers for robust fingerprinting"""
        identifiers = []
        
        try:
            if platform.system() == "Linux":
                try:
                    result = subprocess.run(
                        ["cat", "/etc/machine-id"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        identifiers.append(("machine_id", result.stdout.strip()))
                except:
                    pass
                
                try:
                    result = subprocess.run(
                        ["cat", "/var/lib/dbus/machine-id"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        identifiers.append(("dbus_machine_id", result.stdout.strip()))
                except:
                    pass
            
            elif platform.system() == "Darwin":
                try:
                    result = subprocess.run(
                        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        for line in result.stdout.split("\n"):
                            if "IOPlatformUUID" in line:
                                uuid_value = line.split('"')[3]
                                identifiers.append(("platform_uuid", uuid_value))
                except:
                    pass
            
            elif platform.system() == "Windows":
                try:
                    result = subprocess.run(
                        ["wmic", "csproduct", "get", "UUID"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        lines = result.stdout.strip().split("\n")
                        if len(lines) > 1:
                            identifiers.append(("windows_uuid", lines[1].strip()))
                except:
                    pass
            
            hostname = platform.node()
            if hostname:
                identifiers.append(("hostname", hostname))
            
            mac = uuid.getnode()
            identifiers.append(("mac", str(mac)))
            
        except Exception:
            pass
        
        if not identifiers:
            identifiers.append(("fallback", str(uuid.uuid4())))
        
        return identifiers
    
    def _compute_device_hash(self, identifiers: list) -> str:
        """Create deterministic hash from hardware identifiers"""
        combined = "|".join([f"{k}:{v}" for k, v in identifiers])
        hash_obj = hashlib.sha256(combined.encode())
        return hash_obj.hexdigest()
    
    def _get_or_create_device_id(self) -> str:
        """Get existing device ID or create new one from hardware"""
        if os.path.exists(self.DEVICE_ID_PATH):
            try:
                with open(self.DEVICE_ID_PATH, 'r') as f:
                    data = json.load(f)
                    return data.get('device_id')
            except:
                pass
        
        identifiers = self._get_hardware_identifiers()
        device_id = self._compute_device_hash(identifiers)
        
        os.makedirs(os.path.dirname(self.DEVICE_ID_PATH) or ".", exist_ok=True)
        with open(self.DEVICE_ID_PATH, 'w') as f:
            json.dump({
                'device_id': device_id,
                'identifiers': identifiers,
                'platform': platform.system()
            }, f, indent=2)
        
        os.chmod(self.DEVICE_ID_PATH, 0o600)
        
        return device_id
    
    def acquire_device_lock(self) -> bool:
        """
        Acquire exclusive device lock to prevent multiple nodes.
        Returns True if lock acquired, False if another node is running.
        """
        try:
            os.makedirs(os.path.dirname(self.LOCKFILE_PATH) or ".", exist_ok=True)
            
            self.lockfile = open(self.LOCKFILE_PATH, 'w')
            
            try:
                fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                self.lockfile.write(json.dumps({
                    'device_id': self.device_id,
                    'pid': os.getpid(),
                    'platform': platform.system()
                }))
                self.lockfile.flush()
                
                return True
            except IOError:
                return False
        except Exception:
            return False
    
    def release_device_lock(self):
        """Release the device lock"""
        if self.lockfile:
            try:
                fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_UN)
                self.lockfile.close()
            except:
                pass
            finally:
                self.lockfile = None
    
    def get_device_id(self) -> str:
        """Get the unique device identifier"""
        return self.device_id
    
    def __del__(self):
        """Ensure lock is released on cleanup"""
        self.release_device_lock()


def enforce_single_node() -> DeviceFingerprint:
    """
    Enforce that only one node can run on this device.
    Raises RuntimeError if another node is already running.
    """
    fingerprint = DeviceFingerprint()
    
    if not fingerprint.acquire_device_lock():
        raise RuntimeError(
            "CRITICAL: Another TIMPAL node is already running on this device.\n"
            "Only ONE node per device is allowed to maintain fairness.\n"
            "Please stop the existing node before starting a new one."
        )
    
    return fingerprint


if __name__ == "__main__":
    print("Testing Device Fingerprinting...")
    print("=" * 60)
    
    fp1 = DeviceFingerprint()
    print(f"Device ID: {fp1.device_id}")
    
    if fp1.acquire_device_lock():
        print("✅ Lock acquired successfully")
        
        try:
            fp2 = DeviceFingerprint()
            if fp2.acquire_device_lock():
                print("❌ FAILED: Second lock should not be acquired!")
            else:
                print("✅ Second lock correctly blocked")
        finally:
            fp1.release_device_lock()
            print("✅ Lock released")
    else:
        print("❌ Failed to acquire lock")
    
    print("=" * 60)
