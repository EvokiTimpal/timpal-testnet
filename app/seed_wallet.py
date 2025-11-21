import hashlib
import json
import os
import secrets
import base64
from typing import Optional, Dict, List
from ecdsa import SigningKey, VerifyingKey, SECP256k1
from argon2 import PasswordHasher
from argon2.low_level import Type as Argon2Type
from bip_utils import (
    Bip39MnemonicGenerator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip39Languages,
    Bip39MnemonicValidator,
    Bip32Slip10Secp256k1,
)
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


WALLET_VERSION = 2
TIMPAL_COIN_CODE = 4007  # SLIP-44 coin type (provisional)


class SeedWallet:
    """
    Production-grade wallet with BIP-39 mnemonic and BIP-32 deterministic key derivation.
    
    Features:
    - BIP-39 compliant 12 or 24-word mnemonic with checksum
    - BIP-32 hierarchical deterministic key derivation
    - BIP-44 standard derivation path: m/44'/4007'/account'/change/index
    - Argon2id encryption for enhanced security
    - Wallet recovery from seed phrase
    - Multiple account support from single seed
    """
    
    def __init__(self, wallet_file: str = "wallet_v2.json"):
        self.wallet_file = wallet_file
        self.mnemonic = None
        self.seed = None
        self.master_key = None
        
        # Derived keys cache
        self.accounts: Dict[int, Dict] = {}
        
        # Encryption
        self.ph = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Argon2Type.ID
        )
        self.salt = None
        self.pin_hash = None  # Store PIN hash for transfer authorization
        
    @staticmethod
    def public_key_to_address(public_key_hex: str) -> str:
        """Convert public key to TIMPAL address (44 hex chars after 'tmpl')."""
        pub_key_bytes = bytes.fromhex(public_key_hex)
        hash1 = hashlib.sha256(pub_key_bytes).digest()
        hash2 = hashlib.sha256(hash1).digest()
        address_hash = hash2.hex()[:44]  # CRITICAL: Must be 44 hex chars to match Transaction._public_key_to_address
        return f"tmpl{address_hash}"
    
    def set_pin(self, pin: str):
        """
        Set PIN for transfer authorization (6+ digits required).
        
        Args:
            pin: Numeric PIN (minimum 6 digits)
            
        Raises:
            ValueError: If PIN doesn't meet requirements
        """
        if len(pin) < 6:
            raise ValueError("PIN must be at least 6 digits")
        if not pin.isdigit():
            raise ValueError("PIN must contain only numbers")
        
        # Hash PIN using SHA-256 (simple but secure for this use case)
        self.pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    
    def validate_pin(self, pin: str) -> bool:
        """
        Validate PIN for transfer authorization.
        
        Args:
            pin: PIN to validate
            
        Returns:
            True if PIN is correct, False otherwise
        """
        if self.pin_hash is None:
            raise ValueError("PIN not set for this wallet")
        
        pin_test_hash = hashlib.sha256(pin.encode()).hexdigest()
        return pin_test_hash == self.pin_hash
    
    def generate_mnemonic(self, words: int = 12) -> str:
        """
        Generate BIP-39 compliant mnemonic with checksum.
        
        Args:
            words: Number of words (12 or 24)
            
        Returns:
            Space-separated mnemonic phrase
        """
        if words == 12:
            word_count = Bip39WordsNum.WORDS_NUM_12
        elif words == 24:
            word_count = Bip39WordsNum.WORDS_NUM_24
        else:
            raise ValueError("Word count must be 12 or 24")
        
        mnemonic = Bip39MnemonicGenerator().FromWordsNumber(word_count)
        return str(mnemonic)
    
    def validate_mnemonic(self, mnemonic: str) -> bool:
        """Validate BIP-39 mnemonic checksum."""
        try:
            return Bip39MnemonicValidator(Bip39Languages.ENGLISH).IsValid(mnemonic)
        except:
            return False
    
    def _mnemonic_to_seed(self, mnemonic: str, passphrase: str = "") -> bytes:
        """Convert mnemonic to BIP-39 seed."""
        return Bip39SeedGenerator(mnemonic).Generate(passphrase)
    
    def _derive_master_key(self, seed: bytes):
        """Derive BIP-32 master private key from seed."""
        self.master_key = Bip32Slip10Secp256k1.FromSeed(seed)
    
    def _derive_account_key(self, account: int = 0, change: int = 0, index: int = 0):
        """
        Derive key using BIP-44 path: m/44'/4007'/account'/change/index
        
        Args:
            account: Account number (default 0)
            change: 0 for external (receiving), 1 for internal (change)
            index: Address index within the account
            
        Returns:
            Dict with private_key, public_key, address
        """
        if self.master_key is None:
            raise ValueError("Master key not initialized")
        
        # BIP-44 path: m/44'/coin_type'/account'/change/index
        # Using hardened derivation for account level (apostrophe)
        path = f"m/44'/{TIMPAL_COIN_CODE}'/{account}'/{change}/{index}"
        
        # Derive child key
        child_key = self.master_key.DerivePath(path)
        
        # Get private key bytes
        private_key_bytes = child_key.PrivateKey().Raw().ToBytes()
        
        # Create ECDSA signing key
        sk = SigningKey.from_string(private_key_bytes, curve=SECP256k1)
        private_key_hex = sk.to_string().hex()
        
        # Get public key
        vk = sk.get_verifying_key()
        public_key_hex = vk.to_string().hex()
        
        # Generate address
        address = self.public_key_to_address(public_key_hex)
        
        return {
            "private_key": private_key_hex,
            "public_key": public_key_hex,
            "address": address,
            "path": path,
            "account": account,
            "change": change,
            "index": index
        }
    
    def _derive_encryption_key(self, password: str, salt: bytes) -> bytes:
        """Derive Fernet-compatible key from password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA512(),
            length=32,
            salt=salt,
            iterations=210000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def _encrypt_mnemonic(self, mnemonic: str, password: str) -> tuple:
        """Encrypt mnemonic using Argon2id-derived password hash."""
        if self.salt is None:
            self.salt = os.urandom(16)
        
        # Hash password with Argon2id
        password_hash = self.ph.hash(password + base64.b64encode(self.salt).decode())
        
        # Derive encryption key from hashed password
        encryption_key = self._derive_encryption_key(password_hash, self.salt)
        
        # Encrypt mnemonic
        fernet = Fernet(encryption_key)
        encrypted = fernet.encrypt(mnemonic.encode())
        
        return encrypted.decode(), base64.b64encode(self.salt).decode(), password_hash
    
    def _decrypt_mnemonic(self, encrypted_mnemonic: str, password: str, salt_b64: str, password_hash: str) -> str:
        """Decrypt mnemonic."""
        salt_bytes = base64.b64decode(salt_b64)
        
        # Verify password
        try:
            self.ph.verify(password_hash, password + base64.b64encode(salt_bytes).decode())
        except:
            raise ValueError("Incorrect password")
        
        # Derive encryption key
        encryption_key = self._derive_encryption_key(password_hash, salt_bytes)
        
        # Decrypt mnemonic
        fernet = Fernet(encryption_key)
        decrypted = fernet.decrypt(encrypted_mnemonic.encode())
        
        return decrypted.decode()
    
    def create_new_wallet(self, password: str, pin: str, words: int = 12, passphrase: str = "", account: int = 0) -> str:
        """
        Create new wallet with BIP-39 mnemonic.
        
        Args:
            password: Password to encrypt wallet file (min 8 characters)
            pin: 6+ digit PIN for transfer authorization
            words: Mnemonic word count (12 or 24)
            passphrase: Optional BIP-39 passphrase (25th word)
            account: Account number to derive (default 0)
            
        Returns:
            Mnemonic phrase (MUST be backed up!)
        """
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        
        # Set PIN
        self.set_pin(pin)
        
        # Generate mnemonic
        self.mnemonic = self.generate_mnemonic(words)
        
        # Derive seed
        self.seed = self._mnemonic_to_seed(self.mnemonic, passphrase)
        
        # Derive master key
        self._derive_master_key(self.seed)
        
        # Derive default account (m/44'/4007'/0'/0/0)
        account_data = self._derive_account_key(account=account, change=0, index=0)
        self.accounts[account] = account_data
        
        # Save wallet
        self.save_wallet(password, passphrase)
        
        return self.mnemonic
    
    def restore_wallet(self, mnemonic: str, password: str, pin: str, passphrase: str = "", account: int = 0):
        """
        Restore wallet from BIP-39 mnemonic.
        
        Args:
            mnemonic: 12 or 24-word mnemonic phrase
            password: Password to encrypt wallet file
            pin: 6+ digit PIN for transfer authorization
            passphrase: Optional BIP-39 passphrase (25th word)
            account: Account number to derive (default 0)
        """
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        
        # Set PIN
        self.set_pin(pin)
        
        # Validate mnemonic
        if not self.validate_mnemonic(mnemonic):
            raise ValueError("Invalid mnemonic phrase (checksum failed)")
        
        self.mnemonic = mnemonic
        
        # Derive seed
        self.seed = self._mnemonic_to_seed(mnemonic, passphrase)
        
        # Derive master key
        self._derive_master_key(self.seed)
        
        # Derive default account
        account_data = self._derive_account_key(account=account, change=0, index=0)
        self.accounts[account] = account_data
        
        # Save wallet
        self.save_wallet(password, passphrase)
    
    def save_wallet(self, password: str, passphrase: str = ""):
        """Save encrypted wallet to file."""
        if self.mnemonic is None:
            raise ValueError("Wallet not initialized - create or restore wallet first")
        
        encrypted_mnemonic, salt_b64, password_hash = self._encrypt_mnemonic(self.mnemonic, password)
        
        wallet_data = {
            "version": WALLET_VERSION,
            "encrypted_mnemonic": encrypted_mnemonic,
            "salt": salt_b64,
            "password_hash": password_hash,
            "passphrase_used": passphrase != "",
            "pin_hash": self.pin_hash,  # Store PIN hash for transfer authorization
            "accounts": {
                str(acc_num): {
                    "address": acc_data["address"],
                    "public_key": acc_data["public_key"],
                    "path": acc_data["path"],
                    "imported": acc_data.get("imported", False),
                    "private_key_encrypted": self._encrypt_data(acc_data["private_key"], password)[0] if acc_data.get("imported") else None
                }
                for acc_num, acc_data in self.accounts.items()
            }
        }
        
        with open(self.wallet_file, 'w') as f:
            json.dump(wallet_data, f, indent=2)
    
    def _encrypt_data(self, data: str, password: str) -> tuple:
        """Encrypt arbitrary data (for imported keys)."""
        if self.salt is None:
            self.salt = os.urandom(16)
        
        password_hash = self.ph.hash(password + base64.b64encode(self.salt).decode())
        encryption_key = self._derive_encryption_key(password_hash, self.salt)
        fernet = Fernet(encryption_key)
        encrypted = fernet.encrypt(data.encode())
        
        return encrypted.decode(), base64.b64encode(self.salt).decode()
    
    def _decrypt_data(self, encrypted_data: str, password: str, salt_b64: str, password_hash: str) -> str:
        """Decrypt arbitrary data (for imported keys)."""
        salt_bytes = base64.b64decode(salt_b64)
        
        try:
            self.ph.verify(password_hash, password + base64.b64encode(salt_bytes).decode())
        except:
            raise ValueError("Incorrect password")
        
        encryption_key = self._derive_encryption_key(password_hash, salt_bytes)
        fernet = Fernet(encryption_key)
        decrypted = fernet.decrypt(encrypted_data.encode())
        
        return decrypted.decode()
    
    def load_wallet(self, password: str, passphrase: str = ""):
        """Load and decrypt wallet from file."""
        if not os.path.exists(self.wallet_file):
            raise FileNotFoundError(f"Wallet file not found: {self.wallet_file}")
        
        with open(self.wallet_file, 'r') as f:
            wallet_data = json.load(f)
        
        if wallet_data.get("version") != WALLET_VERSION:
            raise ValueError(f"Unsupported wallet version: {wallet_data.get('version')}")
        
        # Load PIN hash
        self.pin_hash = wallet_data.get("pin_hash")
        
        # Decrypt mnemonic
        self.mnemonic = self._decrypt_mnemonic(
            wallet_data["encrypted_mnemonic"],
            password,
            wallet_data["salt"],
            wallet_data["password_hash"]
        )
        
        # Derive seed and master key
        self.seed = self._mnemonic_to_seed(self.mnemonic, passphrase)
        self._derive_master_key(self.seed)
        
        # Re-derive accounts
        self.accounts = {}
        for acc_num_str, acc_meta in wallet_data["accounts"].items():
            acc_num = int(acc_num_str)
            # Parse path to get account/change/index
            path_parts = acc_meta["path"].split('/')
            account = int(path_parts[3].replace("'", ""))
            change = int(path_parts[4])
            index = int(path_parts[5])
            
            # Re-derive full account data
            self.accounts[acc_num] = self._derive_account_key(account, change, index)
    
    def get_account(self, account: int = 0) -> Dict:
        """Get account data (creates if doesn't exist)."""
        if account not in self.accounts:
            self.accounts[account] = self._derive_account_key(account=account, change=0, index=0)
        return self.accounts[account]
    
    def derive_new_address(self, account: int = 0, index: Optional[int] = None) -> Dict:
        """Derive new receiving address for account."""
        if index is None:
            # Find next unused index
            existing_indices = [
                acc["index"] for acc in self.accounts.values()
                if acc["account"] == account and acc["change"] == 0
            ]
            index = max(existing_indices, default=-1) + 1
        
        return self._derive_account_key(account=account, change=0, index=index)
    
    def import_legacy_key(self, private_key_hex: str, account: int = 999) -> Dict:
        """
        Import a legacy private key into the wallet (preserves validator address).
        
        This allows migrating from v1 wallet while keeping the same address.
        The imported key is stored in a special account (999 by default) to
        distinguish it from BIP-44 derived keys.
        
        Args:
            private_key_hex: Hexadecimal private key from legacy wallet
            account: Account number to store imported key (default: 999 for legacy)
            
        Returns:
            Dict with address, public_key, private_key
        """
        # Recreate key pair from private key
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        
        # Get public key
        vk = sk.get_verifying_key()
        public_key_hex = vk.to_string().hex()
        
        # Generate address
        address = self.public_key_to_address(public_key_hex)
        
        # Store in accounts with special path indicating it's imported
        imported_account = {
            "private_key": private_key_hex,
            "public_key": public_key_hex,
            "address": address,
            "path": f"m/legacy/imported/{account}",  # Special path for imported keys
            "account": account,
            "change": 0,
            "index": 0,
            "imported": True  # Flag to indicate this is an imported key
        }
        
        self.accounts[account] = imported_account
        return imported_account
