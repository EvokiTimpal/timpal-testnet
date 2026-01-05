"""
Unified wallet loader for TIMPAL - supports both legacy (v1) and BIP-39 (v2) wallets
"""

import os
from typing import Optional, Tuple
from app.wallet import Wallet
from app.seed_wallet import SeedWallet
from app.metawallet import MultiWallet


def load_wallet_unified(wallet_path: str, password: str, passphrase: str = "") -> Tuple[str, str, str]:
    """
    Load wallet from either v1 (legacy) or v2 (BIP-39) format.
    
    Args:
        wallet_path: Path to wallet file
        password: PIN (for v1) or password (for v2)
        passphrase: Optional BIP-39 passphrase (v2 only)
        
    Returns:
        Tuple of (address, public_key, private_key)
        
    Raises:
        FileNotFoundError: If wallet file doesn't exist
        ValueError: If password/PIN is incorrect or wallet is corrupted
    """
    if not os.path.exists(wallet_path):
        raise FileNotFoundError(f"Wallet not found: {wallet_path}")
    
    # Detect wallet version by checking file content
    import json
    try:
        with open(wallet_path, 'r') as f:
            wallet_data = json.load(f)
        
        version = wallet_data.get("version", 1)
        
        if version == 3:
            # Load v3 multi-vault wallet (MetaMask-like)
            mw = MultiWallet(wallet_path)
            mw.load(password, passphrase=passphrase)

            # Optional selection via env vars
            vault_id = os.getenv("TIMPAL_WALLET_ID") or mw.default_vault_id
            acct_index = int(os.getenv("TIMPAL_WALLET_ACCOUNT", "0"))

            vault = mw.get_vault(vault_id)
            acct = vault.get_account(acct_index)
            addr, pub, priv = mw.export_account_private_key(password, vault_id=vault.vault_id, index=acct.index, passphrase=passphrase)
            return addr, pub, priv

        if version == 2:
            # Load v2 wallet (BIP-39)
            wallet = SeedWallet(wallet_path)
            wallet.load_wallet(password, passphrase=passphrase)
            account = wallet.get_account(0)
            return account["address"], account["public_key"], account["private_key"]
        else:
            # Load v1 wallet (legacy)
            wallet = Wallet(wallet_path)
            wallet.load_wallet(password)
            return wallet.address, wallet.public_key, wallet.private_key
    
    except json.JSONDecodeError:
        raise ValueError(f"Corrupted wallet file: {wallet_path}")
    except Exception as e:
        raise ValueError(f"Failed to load wallet: {e}")


def detect_wallet_version(wallet_path: str) -> Optional[int]:
    """
    Detect wallet file version without loading it.
    
    Returns:
        1 for legacy, 2 for BIP-39, None if file doesn't exist
    """
    if not os.path.exists(wallet_path):
        return None
    
    try:
        import json
        with open(wallet_path, 'r') as f:
            wallet_data = json.load(f)
        return wallet_data.get("version", 1)
    except:
        return None


def get_wallet_info(wallet_path: str) -> dict:
    """
    Get wallet metadata without requiring password.
    
    Returns:
        Dict with version, file_path, exists, etc.
    """
    info = {
        "file_path": wallet_path,
        "exists": os.path.exists(wallet_path),
        "version": None,
        "type": None
    }
    
    if info["exists"]:
        version = detect_wallet_version(wallet_path)
        info["version"] = version
        if version == 3:
            info["type"] = "Multi-vault HD (v3)"
        elif version == 2:
            info["type"] = "BIP-39 (v2)"
        else:
            info["type"] = "Legacy (v1)"
    
    return info
