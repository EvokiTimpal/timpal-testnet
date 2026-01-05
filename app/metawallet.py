"""MetaMask-style HD wallet for TIMPAL.

This module adds a *multi-vault* + *multi-account* wallet system similar to
what users expect from MetaMask and other DEX wallets.

Key ideas:
  - A single JSON file (default: ``wallets.json``) contains multiple vaults.
  - Each vault is backed by one BIP-39 mnemonic (seed phrase).
  - Each vault can derive many accounts (addresses) from the seed.
  - Creating a new wallet/vault does **not** overwrite existing vaults.

Derivation:
  - We use a BIP-44 style path:
        m/44'/4007'/0'/0/<index>
    where 4007 is the project coin-type (SLIP-44 provisional in this repo).

Security:
  - The mnemonic is encrypted using password-derived key material (Argon2id
    via ``argon2-cffi``) + Fernet authenticated encryption.
  - A separate PIN hash (SHA-256) is stored for transfer authorization.

Compatibility:
  - This wallet format is version 3.
  - Version 1 (legacy) and version 2 (seed_wallet.py) are still supported by
    ``wallet_loader.py``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from argon2 import PasswordHasher
from argon2.low_level import Type as Argon2Type
from bip_utils import (
    Bip39MnemonicGenerator,
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip32Slip10Secp256k1,
)
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from ecdsa import SECP256k1, SigningKey


WALLET_VERSION = 3
TIMPAL_COIN_CODE = 4007


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_key_to_address(public_key_hex: str) -> str:
    """Convert public key to TIMPAL address.

    Must match Transaction._public_key_to_address: double SHA-256 and first 44 hex chars.
    """
    pub_key_bytes = bytes.fromhex(public_key_hex)
    hash1 = hashlib.sha256(pub_key_bytes).digest()
    hash2 = hashlib.sha256(hash1).digest()
    return f"tmpl{hash2.hex()[:44]}"


@dataclass
class WalletAccount:
    index: int
    address: str
    public_key: str
    path: str


class MetaVault:
    """A single vault backed by one mnemonic and many derived accounts."""

    def __init__(self, vault_id: str, name: str):
        self.vault_id = vault_id
        self.name = name

        self.mnemonic: Optional[str] = None
        self.seed: Optional[bytes] = None
        self.master_key = None

        self.pin_hash: Optional[str] = None
        self.passphrase_used: bool = False

        self._accounts: List[WalletAccount] = []

        # Encryption
        self.ph = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Argon2Type.ID,
        )

    # ---------- PIN ----------
    def set_pin(self, pin: str) -> None:
        if len(pin) < 6 or (not pin.isdigit()):
            raise ValueError("PIN must be numeric and at least 6 digits")
        self.pin_hash = _sha256_hex(pin.encode())

    def validate_pin(self, pin: str) -> bool:
        if self.pin_hash is None:
            raise ValueError("PIN not set for this vault")
        return _sha256_hex(pin.encode()) == self.pin_hash

    # ---------- BIP-39/32 ----------
    def generate_mnemonic(self, words: int = 12) -> str:
        if words == 12:
            wc = Bip39WordsNum.WORDS_NUM_12
        elif words == 24:
            wc = Bip39WordsNum.WORDS_NUM_24
        else:
            raise ValueError("Word count must be 12 or 24")
        return Bip39MnemonicGenerator().FromWordsNumber(wc)

    def validate_mnemonic(self, mnemonic: str) -> bool:
        return Bip39MnemonicValidator(mnemonic).Validate()

    def _mnemonic_to_seed(self, mnemonic: str, passphrase: str = "") -> bytes:
        return Bip39SeedGenerator(mnemonic).Generate(passphrase)

    def _derive_master(self, seed: bytes) -> None:
        self.master_key = Bip32Slip10Secp256k1.FromSeed(seed)

    def _derive_private_key_hex(self, index: int) -> Tuple[str, str, str, str]:
        """Derive a TIMPAL account at m/44'/4007'/0'/0/index.

        Returns (priv_hex, pub_hex, address, path)
        """
        if self.master_key is None:
            raise ValueError("Vault not unlocked")

        path = f"m/44'/{TIMPAL_COIN_CODE}'/0'/0/{index}"
        key = self.master_key.DerivePath(path)
        priv = key.PrivateKey().Raw().ToBytes()

        # Convert to secp256k1 signing key
        sk = SigningKey.from_string(priv, curve=SECP256k1)
        vk = sk.get_verifying_key()
        pub_hex = vk.to_string().hex()
        priv_hex = priv.hex()
        addr = public_key_to_address(pub_hex)
        return priv_hex, pub_hex, addr, path

    def list_accounts(self) -> List[WalletAccount]:
        return list(self._accounts)

    def get_account(self, index: int) -> WalletAccount:
        for a in self._accounts:
            if a.index == index:
                return a
        raise KeyError(f"Account index not found: {index}")

    def add_account(self, index: Optional[int] = None) -> WalletAccount:
        if index is None:
            index = (max((a.index for a in self._accounts), default=-1) + 1)
        # Ensure uniqueness
        if any(a.index == index for a in self._accounts):
            raise ValueError(f"Account index already exists: {index}")

        _priv_hex, pub_hex, addr, path = self._derive_private_key_hex(index)
        acct = WalletAccount(index=index, address=addr, public_key=pub_hex, path=path)
        self._accounts.append(acct)
        self._accounts.sort(key=lambda x: x.index)
        return acct

    def find_account_by_address(self, address: str) -> Optional[WalletAccount]:
        for a in self._accounts:
            if a.address == address:
                return a
        return None

    # ---------- Encryption helpers (Argon2id + Fernet) ----------
    def _derive_encryption_key(self, password_hash: str, salt: bytes) -> bytes:
        """Derive a 32-byte Fernet key from an Argon2 hash and salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password_hash.encode()))

    def _encrypt_mnemonic(self, mnemonic: str, password: str) -> Tuple[str, str, str]:
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode()

        password_hash = self.ph.hash(password + salt_b64)
        key = self._derive_encryption_key(password_hash, salt)
        f = Fernet(key)
        encrypted = f.encrypt(mnemonic.encode()).decode()
        return encrypted, salt_b64, password_hash

    def _decrypt_mnemonic(self, encrypted: str, password: str, salt_b64: str, password_hash: str) -> str:
        salt = base64.b64decode(salt_b64)
        self.ph.verify(password_hash, password + salt_b64)
        key = self._derive_encryption_key(password_hash, salt)
        f = Fernet(key)
        return f.decrypt(encrypted.encode()).decode()


class MultiWallet:
    """Manages multiple vaults in a single file (MetaMask-like)."""

    def __init__(self, wallet_file: str = "wallets.json"):
        self.wallet_file = wallet_file
        self.vaults: Dict[str, MetaVault] = {}
        self.default_vault_id: Optional[str] = None

    # ---------- File IO ----------
    def exists(self) -> bool:
        return os.path.exists(self.wallet_file)

    def _load_raw(self) -> dict:
        with open(self.wallet_file, "r") as f:
            return json.load(f)

    def _save_raw(self, data: dict) -> None:
        tmp = self.wallet_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.wallet_file)

    def load(self, password: str, passphrase: str = "") -> None:
        if not self.exists():
            raise FileNotFoundError(f"Wallet file not found: {self.wallet_file}")

        data = self._load_raw()
        if data.get("version") != WALLET_VERSION:
            raise ValueError(f"Unsupported wallet version: {data.get('version')}")

        self.default_vault_id = data.get("default_vault_id")
        self.vaults = {}

        for v in data.get("vaults", []):
            vault = MetaVault(vault_id=v["id"], name=v.get("name", v["id"]))
            vault.pin_hash = v.get("pin_hash")
            vault.passphrase_used = bool(v.get("passphrase_used"))

            # Unlock mnemonic
            mnemonic = vault._decrypt_mnemonic(
                v["encrypted_mnemonic"],
                password=password,
                salt_b64=v["salt"],
                password_hash=v["password_hash"],
            )
            vault.mnemonic = mnemonic
            vault.seed = vault._mnemonic_to_seed(mnemonic, passphrase)
            vault._derive_master(vault.seed)

            # Rebuild accounts list (indices only; keys are derived when needed)
            vault._accounts = []
            for a in v.get("accounts", []):
                vault._accounts.append(
                    WalletAccount(
                        index=int(a["index"]),
                        address=a["address"],
                        public_key=a["public_key"],
                        path=a["path"],
                    )
                )
            vault._accounts.sort(key=lambda x: x.index)

            self.vaults[vault.vault_id] = vault

        if self.default_vault_id is None and self.vaults:
            self.default_vault_id = next(iter(self.vaults.keys()))

    def save(self, password: str) -> None:
        # Persist the currently loaded vaults (mnemonic must be present)
        vaults_out = []
        for vault_id, vault in self.vaults.items():
            if vault.mnemonic is None:
                raise ValueError(f"Vault not unlocked/initialized: {vault_id}")
            enc, salt_b64, password_hash = vault._encrypt_mnemonic(vault.mnemonic, password)
            vaults_out.append(
                {
                    "id": vault_id,
                    "name": vault.name,
                    "encrypted_mnemonic": enc,
                    "salt": salt_b64,
                    "password_hash": password_hash,
                    "passphrase_used": vault.passphrase_used,
                    "pin_hash": vault.pin_hash,
                    "accounts": [
                        {
                            "index": a.index,
                            "address": a.address,
                            "public_key": a.public_key,
                            "path": a.path,
                        }
                        for a in vault.list_accounts()
                    ],
                }
            )

        data = {
            "version": WALLET_VERSION,
            "default_vault_id": self.default_vault_id,
            "vaults": vaults_out,
        }
        self._save_raw(data)

    # ---------- Vault operations ----------
    def create_vault(
        self,
        name: str,
        password: str,
        pin: str,
        words: int = 12,
        passphrase: str = "",
        make_default: bool = True,
    ) -> str:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        vault_id = secrets.token_hex(8)
        vault = MetaVault(vault_id=vault_id, name=name)
        vault.set_pin(pin)
        vault.passphrase_used = bool(passphrase)
        mnemonic = vault.generate_mnemonic(words=words)
        vault.mnemonic = mnemonic
        vault.seed = vault._mnemonic_to_seed(mnemonic, passphrase)
        vault._derive_master(vault.seed)
        # Add default first account (index 0)
        vault.add_account(0)

        self.vaults[vault_id] = vault
        if make_default or self.default_vault_id is None:
            self.default_vault_id = vault_id

        # Write to disk (creating or updating file)
        self.save(password)
        return mnemonic

    def restore_vault(
        self,
        name: str,
        mnemonic: str,
        password: str,
        pin: str,
        passphrase: str = "",
        make_default: bool = False,
    ) -> None:
        vault_id = secrets.token_hex(8)
        vault = MetaVault(vault_id=vault_id, name=name)
        vault.set_pin(pin)

        if not vault.validate_mnemonic(mnemonic):
            raise ValueError("Invalid mnemonic phrase (checksum failed)")

        vault.mnemonic = mnemonic
        vault.passphrase_used = bool(passphrase)
        vault.seed = vault._mnemonic_to_seed(mnemonic, passphrase)
        vault._derive_master(vault.seed)
        vault.add_account(0)

        self.vaults[vault_id] = vault
        if make_default or self.default_vault_id is None:
            self.default_vault_id = vault_id

        self.save(password)

    def get_vault(self, vault_id: Optional[str] = None) -> MetaVault:
        vid = vault_id or self.default_vault_id
        if not vid:
            raise ValueError("No vaults available")
        if vid not in self.vaults:
            raise KeyError(f"Vault not found: {vid}")
        return self.vaults[vid]

    def list_vaults(self) -> List[Dict[str, str]]:
        return [
            {"id": v.vault_id, "name": v.name, "is_default": (v.vault_id == self.default_vault_id)}
            for v in self.vaults.values()
        ]

    def add_account(self, password: str, vault_id: Optional[str] = None, index: Optional[int] = None) -> WalletAccount:
        vault = self.get_vault(vault_id)
        acct = vault.add_account(index=index)
        # Persist
        self.save(password)
        return acct

    def export_account_private_key(self, password: str, vault_id: str, index: int, passphrase: str = "") -> Tuple[str, str, str]:
        """Return (address, public_key, private_key_hex) for an account."""
        vault = self.get_vault(vault_id)
        if vault.mnemonic is None:
            # If not loaded via load(), we need to load first
            self.load(password, passphrase=passphrase)
            vault = self.get_vault(vault_id)

        priv_hex, pub_hex, addr, _path = vault._derive_private_key_hex(index)
        return addr, pub_hex, priv_hex

    def find_account(self, address: str) -> Optional[Tuple[str, WalletAccount]]:
        """Find (vault_id, account) for a given address across all vaults."""
        for vid, vault in self.vaults.items():
            acct = vault.find_account_by_address(address)
            if acct:
                return vid, acct
        return None
