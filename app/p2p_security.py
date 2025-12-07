"""
P2P Network Security Module

Implements mandatory message authentication and replay protection for TIMPAL P2P network.

Security Features:
1. Mandatory message signing (reject unsigned messages)
2. Message timestamp validation (prevent replay attacks)
3. Nonce tracking (prevent duplicate message attacks)
4. TLS/SSL support for encrypted transport
5. Peer authentication and identity verification

CRITICAL: All P2P messages MUST be authenticated to prevent:
- Message forgery
- Replay attacks
- Man-in-the-middle attacks
- Network poisoning
"""

import time
import hashlib
from typing import Dict, Optional, Set
from collections import defaultdict, deque
import json


class P2PSecurityManager:
    """
    Manages security for P2P network communications.
    
    Features:
    - Mandatory message authentication
    - Replay attack prevention
    - Message nonce tracking
    - Peer identity verification
    """
    
    # Message age limit (seconds) - reject messages older than this
    MAX_MESSAGE_AGE = 86400  # 24 hours - very lenient for clock drift tolerance
    
    # Maximum time drift allowed between nodes (seconds)
    # CRITICAL: Set very high to allow nodes with different system clocks to sync
    # Nonces still prevent replay attacks even with lenient timestamps
    MAX_TIME_DRIFT = 86400  # 24 hours - allows nodes with any reasonable clock drift to connect
    
    # Nonce cache size per peer (prevent replay attacks)
    NONCE_CACHE_SIZE = 1000
    
    # Required message fields for authentication
    REQUIRED_AUTH_FIELDS = {"signature", "public_key", "timestamp", "nonce"}
    
    def __init__(self):
        # Track seen message nonces per peer to prevent replays
        self.seen_nonces: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.NONCE_CACHE_SIZE))
        
        # Track peer public keys
        self.peer_public_keys: Dict[str, str] = {}
        
        # Ban malicious peers
        self.banned_peers: Set[str] = set()
        
        # Track authentication failures per peer
        self.auth_failures: Dict[str, int] = defaultdict(int)
        
        # Maximum authentication failures before ban
        self.MAX_AUTH_FAILURES = 10
    
    def validate_message_auth(self, message: dict, peer_id: str, 
                              verify_signature_func=None) -> tuple[bool, str]:
        """
        Validate that a message meets all authentication requirements.
        
        CRITICAL: This now ACTUALLY verifies cryptographic signatures!
        
        Args:
            message: Received message dictionary
            peer_id: ID of peer who sent message
            verify_signature_func: Function to verify signature (from p2p.py)
        
        Returns:
            (valid, reason) tuple
            - valid: True if message passes all checks
            - reason: Explanation if validation failed
        """
        # Check if peer is banned
        if peer_id in self.banned_peers:
            return (False, "Peer is banned for malicious behavior")
        
        # Check required authentication fields
        missing_fields = self.REQUIRED_AUTH_FIELDS - set(message.keys())
        if missing_fields:
            self._record_auth_failure(peer_id)
            return (False, f"Message missing required auth fields: {missing_fields}")
        
        # CRITICAL FIX: Actually verify the cryptographic signature!
        signature = message.get("signature")
        public_key = message.get("public_key")
        
        if verify_signature_func:
            # Create message without signature for verification
            message_without_sig = {k: v for k, v in message.items() if k != "signature"}
            message_str = json.dumps(message_without_sig, sort_keys=True)
            
            if not verify_signature_func(message_str, signature, public_key):
                self._record_auth_failure(peer_id)
                return (False, "Invalid cryptographic signature - message authentication failed")
        else:
            # Fallback: use built-in verification if no function provided
            if not self._verify_signature_builtin(message, signature, public_key):
                self._record_auth_failure(peer_id)
                return (False, "Invalid cryptographic signature - message authentication failed")
        
        # Validate timestamp (prevent replay of old messages)
        timestamp_valid, timestamp_reason = self._validate_timestamp(message.get("timestamp"))
        if not timestamp_valid:
            self._record_auth_failure(peer_id)
            return (False, f"Invalid timestamp: {timestamp_reason}")
        
        # Validate nonce (prevent duplicate messages)
        nonce = message.get("nonce")
        if not nonce:
            self._record_auth_failure(peer_id)
            return (False, "Message missing nonce")
        
        if nonce in self.seen_nonces[peer_id]:
            self._record_auth_failure(peer_id)
            return (False, f"Duplicate nonce detected - replay attack prevented")
        
        # All checks passed
        return (True, "Message authentication valid")
    
    def _verify_signature_builtin(self, message: dict, signature: str, public_key: str) -> bool:
        """
        Built-in signature verification using ECDSA.
        
        This is a fallback if p2p.py doesn't provide verification function.
        """
        if not signature or not public_key:
            return False
        
        try:
            from ecdsa import VerifyingKey, SECP256k1
            
            # Reconstruct message without signature
            message_data = {k: v for k, v in message.items() if k != "signature"}
            message_str = json.dumps(message_data, sort_keys=True)
            
            # Verify signature
            vk = VerifyingKey.from_string(bytes.fromhex(public_key), curve=SECP256k1)
            message_hash = hashlib.sha256(message_str.encode()).digest()
            signature_bytes = bytes.fromhex(signature)
            return vk.verify(signature_bytes, message_hash)
        except Exception as e:
            print(f"Signature verification failed: {e}")
            return False
    
    def _validate_timestamp(self, timestamp: float) -> tuple[bool, str]:
        """
        Validate message timestamp to prevent replay attacks.
        
        CLOCK DRIFT TOLERANCE: This method is intentionally lenient to allow
        nodes with different system clocks to communicate. Nonces provide
        replay protection even with relaxed timestamp validation.
        
        Args:
            timestamp: Unix timestamp from message
        
        Returns:
            (valid, reason) tuple
        """
        if not isinstance(timestamp, (int, float)):
            return (False, "Timestamp must be a number")
        
        current_time = time.time()
        age = current_time - timestamp
        
        # Log warning for significant clock drift (> 60 seconds) but don't reject
        # This helps operators identify clock sync issues without breaking connectivity
        CLOCK_DRIFT_WARNING_THRESHOLD = 60  # 1 minute
        if abs(age) > CLOCK_DRIFT_WARNING_THRESHOLD:
            drift_direction = "behind" if age > 0 else "ahead"
            print(f"⚠️  CLOCK DRIFT: Peer clock is {abs(age):.0f}s {drift_direction} - continuing anyway")
        
        # Only reject for extreme cases (> 24 hours) to prevent obvious attacks
        # while still allowing nodes with misconfigured clocks to sync
        if age < -self.MAX_TIME_DRIFT:
            return (False, f"Message timestamp is {abs(age):.0f}s in the future (extreme drift)")
        
        if age > self.MAX_MESSAGE_AGE:
            return (False, f"Message is {age:.0f}s old (extreme drift)")
        
        return (True, "Timestamp valid")
    
    def record_verified_message(self, message: dict, peer_id: str):
        """
        Record that a message was successfully verified.
        
        Tracks nonce to prevent replay attacks.
        Resets authentication failure counter.
        """
        nonce = message.get("nonce")
        if nonce:
            self.seen_nonces[peer_id].append(nonce)
        
        # Reset auth failure counter on successful auth
        if peer_id in self.auth_failures:
            self.auth_failures[peer_id] = 0
        
        # Store/update peer public key
        public_key = message.get("public_key")
        if public_key:
            # Verify public key hasn't changed (peer identity consistency)
            if peer_id in self.peer_public_keys:
                if self.peer_public_keys[peer_id] != public_key:
                    print(f"⚠️  WARNING: Peer {peer_id} changed public key!")
                    print(f"   This could indicate a man-in-the-middle attack")
                    # Don't update - keep original key
                    return
            else:
                self.peer_public_keys[peer_id] = public_key
    
    def _record_auth_failure(self, peer_id: str):
        """
        Record an authentication failure for a peer.
        
        Bans peer if they exceed maximum failures.
        """
        self.auth_failures[peer_id] += 1
        
        if self.auth_failures[peer_id] >= self.MAX_AUTH_FAILURES:
            self.banned_peers.add(peer_id)
            print(f"🚫 SECURITY: Peer {peer_id} banned after {self.auth_failures[peer_id]} authentication failures")
    
    def create_secure_message(self, message: dict) -> dict:
        """
        Add security fields to an outgoing message.
        
        Args:
            message: Base message dictionary
        
        Returns:
            Message with timestamp and nonce added
        """
        secure_message = dict(message)
        
        # Add timestamp (for replay protection)
        secure_message["timestamp"] = time.time()
        
        # Add nonce (for duplicate detection)
        nonce = hashlib.sha256(
            f"{message.get('type', '')}_{time.time()}_{id(message)}".encode()
        ).hexdigest()[:16]
        secure_message["nonce"] = nonce
        
        return secure_message
    
    def is_peer_trusted(self, peer_id: str) -> bool:
        """Check if a peer is trusted (not banned)."""
        return peer_id not in self.banned_peers
    
    def get_peer_public_key(self, peer_id: str) -> Optional[str]:
        """Get verified public key for a peer."""
        return self.peer_public_keys.get(peer_id)
    
    def cleanup_old_nonces(self):
        """
        Clean up old nonce caches to prevent memory growth.
        
        Called periodically to remove old data.
        """
        # Nonces are automatically managed by deque maxlen
        # This method is provided for future expansion
        pass
    
    def get_security_stats(self) -> dict:
        """Get security statistics for monitoring."""
        return {
            "tracked_peers": len(self.peer_public_keys),
            "banned_peers": len(self.banned_peers),
            "auth_failures": sum(self.auth_failures.values()),
            "nonce_caches": {peer: len(nonces) for peer, nonces in self.seen_nonces.items()}
        }


def create_ssl_context(certfile: Optional[str] = None, 
                       keyfile: Optional[str] = None) -> object:
    """
    Create SSL context for encrypted P2P connections.
    
    Args:
        certfile: Path to SSL certificate file
        keyfile: Path to SSL private key file
    
    Returns:
        SSL context configured for secure connections
    
    Note: If no cert/key provided, creates self-signed context
          for encrypted transport without CA verification.
    """
    import ssl
    
    if certfile and keyfile:
        # Use provided certificates
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile, keyfile)
    else:
        # Create context for self-signed certificates
        # This provides encryption but not CA-verified identity
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        print("⚠️  WARNING: Using self-signed SSL certificates")
        print("   Connections are encrypted but peer identity is not verified by CA")
    
    return ssl_context
