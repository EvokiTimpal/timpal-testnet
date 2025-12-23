"""
Cryptographic utilities for TIMPAL blockchain

Simple wrapper functions for key generation, signing, and hashing.
"""

import hashlib
from ecdsa import SigningKey, VerifyingKey, SECP256k1, BadSignatureError


def generate_keypair():
    """
    Generate a new ECDSA keypair.
    
    Returns:
        tuple: (private_key_hex, public_key_hex)
    """
    sk = SigningKey.generate(curve=SECP256k1)
    vk = sk.get_verifying_key()
    
    private_key = sk.to_string().hex()
    public_key = vk.to_string().hex()
    
    return private_key, public_key


def sign_message(message: bytes, private_key_hex: str) -> str:
    """
    Sign a message with a private key.
    
    Args:
        message: Message to sign (bytes)
        private_key_hex: Private key in hex format
    
    Returns:
        str: Signature in hex format
    """
    sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
    signature = sk.sign(message)
    return signature.hex()


def verify_signature(message: bytes, signature_hex: str, public_key_hex: str) -> bool:
    """
    Verify a signature.
    
    Args:
        message: Original message (bytes)
        signature_hex: Signature in hex format
        public_key_hex: Public key in hex format
    
    Returns:
        bool: True if signature is valid
    """
    try:
        vk = VerifyingKey.from_string(bytes.fromhex(public_key_hex), curve=SECP256k1)
        vk.verify(bytes.fromhex(signature_hex), message)
        return True
    except (BadSignatureError, Exception):
        return False


def hash_data(data: bytes) -> str:
    """
    Hash data using SHA-256.
    
    Args:
        data: Data to hash (bytes)
    
    Returns:
        str: Hex-encoded hash
    """
    return hashlib.sha256(data).hexdigest()


def derive_address(public_key_hex: str) -> str:
    """
    Derive TIMPAL address from public key.
    
    Uses double SHA-256 hashing (standard blockchain practice).
    
    Args:
        public_key_hex: Public key in hex format
    
    Returns:
        str: TIMPAL address (starts with 'tmpl')
    """
    # Double SHA-256 (same as Transaction._public_key_to_address)
    hash1 = hashlib.sha256(bytes.fromhex(public_key_hex)).digest()
    hash2 = hashlib.sha256(hash1).digest()
    return f"tmpl{hash2.hex()[:44]}"
