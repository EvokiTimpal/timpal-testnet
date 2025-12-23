"""
TIMPAL Blockchain - Secure Key Storage
Encrypted storage for validator private keys and sensitive data
"""

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
import os
import json
import getpass
from typing import Dict, Optional


class SecureKeyStorage:
    """
    Encrypted key storage for TIMPAL validators
    
    Features:
    - AES-256 encryption for private keys
    - Password-derived encryption keys (PBKDF2)
    - Secure key file permissions (0600)
    - Automatic backup of key files
    """
    
    def __init__(self, storage_dir: str = "secure_keys"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
        self._set_secure_permissions()
        
        print(f"🔐 Secure key storage initialized at {storage_dir}")
    
    def _set_secure_permissions(self):
        """Set directory permissions to 0700 (owner only)"""
        try:
            os.chmod(self.storage_dir, 0o700)
        except Exception as e:
            print(f"⚠️  Could not set secure permissions: {e}")
    
    def _derive_encryption_key(self, password: str, salt: bytes) -> bytes:
        """Derive encryption key from password using PBKDF2"""
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        return kdf.derive(password.encode('utf-8'))
    
    def _get_cipher(self, password: str, salt: bytes) -> Fernet:
        """Create Fernet cipher with derived key"""
        key = self._derive_encryption_key(password, salt)
        import base64
        fernet_key = base64.urlsafe_b64encode(key)
        return Fernet(fernet_key)
    
    def save_validator_key(
        self,
        validator_address: str,
        private_key: str,
        password: str,
        metadata: Optional[Dict] = None
    ) -> str:
        """
        Save validator private key with encryption
        
        Args:
            validator_address: Validator's TMPL address
            private_key: Private key in hex format
            password: Encryption password
            metadata: Optional metadata (device_id, etc.)
        
        Returns:
            Path to encrypted key file
        """
        salt = os.urandom(16)
        
        cipher = self._get_cipher(password, salt)
        
        key_data = {
            'validator_address': validator_address,
            'private_key': private_key,
            'metadata': metadata or {}
        }
        
        plaintext = json.dumps(key_data).encode('utf-8')
        encrypted = cipher.encrypt(plaintext)
        
        file_data = {
            'salt': salt.hex(),
            'encrypted_data': encrypted.decode('utf-8'),
            'version': '1.0'
        }
        
        filename = f"validator_{validator_address}.enc"
        filepath = os.path.join(self.storage_dir, filename)
        
        backup_path = f"{filepath}.backup"
        if os.path.exists(filepath):
            import shutil
            shutil.copy2(filepath, backup_path)
        
        with open(filepath, 'w') as f:
            json.dump(file_data, f, indent=2)
        
        os.chmod(filepath, 0o600)
        
        print(f"🔐 Validator key saved securely: {validator_address[:20]}...")
        
        return filepath
    
    def load_validator_key(
        self,
        validator_address: str,
        password: str
    ) -> Optional[Dict]:
        """
        Load and decrypt validator private key
        
        Args:
            validator_address: Validator's TMPL address
            password: Decryption password
        
        Returns:
            Dict with private_key and metadata, or None if decryption fails
        """
        filename = f"validator_{validator_address}.enc"
        filepath = os.path.join(self.storage_dir, filename)
        
        if not os.path.exists(filepath):
            print(f"❌ Key file not found: {validator_address}")
            return None
        
        try:
            with open(filepath, 'r') as f:
                file_data = json.load(f)
            
            salt = bytes.fromhex(file_data['salt'])
            encrypted_data = file_data['encrypted_data'].encode('utf-8')
            
            cipher = self._get_cipher(password, salt)
            
            decrypted = cipher.decrypt(encrypted_data)
            
            key_data = json.loads(decrypted.decode('utf-8'))
            
            print(f"✅ Validator key loaded: {validator_address[:20]}...")
            
            return key_data
            
        except Exception as e:
            print(f"❌ Failed to decrypt key (wrong password?): {e}")
            return None
    
    def change_password(
        self,
        validator_address: str,
        old_password: str,
        new_password: str
    ) -> bool:
        """
        Change encryption password for a validator key
        
        Args:
            validator_address: Validator's TMPL address
            old_password: Current password
            new_password: New password
        
        Returns:
            True if successful, False otherwise
        """
        key_data = self.load_validator_key(validator_address, old_password)
        
        if key_data is None:
            print("❌ Could not load key with old password")
            return False
        
        self.save_validator_key(
            validator_address,
            key_data['private_key'],
            new_password,
            key_data.get('metadata')
        )
        
        print(f"✅ Password changed for: {validator_address[:20]}...")
        return True
    
    def list_validators(self) -> list:
        """List all validators with stored keys"""
        validators = []
        
        for filename in os.listdir(self.storage_dir):
            if filename.startswith('validator_') and filename.endswith('.enc'):
                address = filename.replace('validator_', '').replace('.enc', '')
                validators.append(address)
        
        return validators
    
    def export_key(
        self,
        validator_address: str,
        password: str,
        export_path: str
    ) -> bool:
        """
        Export unencrypted key to file (USE WITH CAUTION!)
        
        Args:
            validator_address: Validator's TMPL address
            password: Decryption password
            export_path: Path to export file
        
        Returns:
            True if successful
        """
        key_data = self.load_validator_key(validator_address, password)
        
        if key_data is None:
            return False
        
        with open(export_path, 'w') as f:
            json.dump(key_data, f, indent=2)
        
        os.chmod(export_path, 0o600)
        
        print(f"⚠️  UNENCRYPTED key exported to: {export_path}")
        print(f"⚠️  DELETE THIS FILE after use!")
        
        return True
    
    def delete_key(
        self,
        validator_address: str,
        password: str
    ) -> bool:
        """
        Delete validator key (requires password confirmation)
        
        Args:
            validator_address: Validator's TMPL address
            password: Password for confirmation
        
        Returns:
            True if deleted
        """
        key_data = self.load_validator_key(validator_address, password)
        
        if key_data is None:
            print("❌ Cannot delete: wrong password")
            return False
        
        filename = f"validator_{validator_address}.enc"
        filepath = os.path.join(self.storage_dir, filename)
        
        backup_path = f"{filepath}.deleted"
        import shutil
        shutil.move(filepath, backup_path)
        
        print(f"🗑️  Key deleted (backup at: {backup_path})")
        
        return True


def interactive_key_setup():
    """
    Interactive CLI for setting up validator keys
    
    Usage:
        python -m app.key_storage
    """
    print("=" * 60)
    print("TIMPAL Validator Key Setup")
    print("=" * 60)
    
    storage = SecureKeyStorage()
    
    print("\n1. Create new validator key")
    print("2. Load existing validator key")
    print("3. List all validator keys")
    print("4. Change password")
    print("5. Export key (DANGEROUS)")
    
    choice = input("\nChoice (1-5): ").strip()
    
    if choice == '1':
        address = input("Validator address: ").strip()
        private_key = input("Private key (hex): ").strip()
        device_id = input("Device ID: ").strip()
        
        password = getpass.getpass("Encryption password: ")
        confirm = getpass.getpass("Confirm password: ")
        
        if password != confirm:
            print("❌ Passwords do not match!")
            return
        
        storage.save_validator_key(
            address,
            private_key,
            password,
            {'device_id': device_id}
        )
        
        print("✅ Validator key saved securely!")
    
    elif choice == '2':
        address = input("Validator address: ").strip()
        password = getpass.getpass("Password: ")
        
        key_data = storage.load_validator_key(address, password)
        
        if key_data:
            print(f"\n✅ Key loaded successfully!")
            print(f"Address: {key_data['validator_address']}")
            print(f"Metadata: {key_data.get('metadata', {})}")
            print(f"Private key: {'*' * 40} (hidden)")
    
    elif choice == '3':
        validators = storage.list_validators()
        
        if validators:
            print(f"\n📋 Found {len(validators)} validator keys:")
            for addr in validators:
                print(f"  - {addr}")
        else:
            print("\n📋 No validator keys found")
    
    elif choice == '4':
        address = input("Validator address: ").strip()
        old_password = getpass.getpass("Old password: ")
        new_password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm new password: ")
        
        if new_password != confirm:
            print("❌ Passwords do not match!")
            return
        
        if storage.change_password(address, old_password, new_password):
            print("✅ Password changed successfully!")
    
    elif choice == '5':
        print("\n⚠️  WARNING: This will export your UNENCRYPTED private key!")
        confirm = input("Type 'YES' to continue: ").strip()
        
        if confirm != 'YES':
            print("❌ Export cancelled")
            return
        
        address = input("Validator address: ").strip()
        password = getpass.getpass("Password: ")
        export_path = input("Export file path: ").strip()
        
        storage.export_key(address, password, export_path)


if __name__ == "__main__":
    interactive_key_setup()
