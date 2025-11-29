import asyncio
import json
import uuid
import time
import ssl
import hashlib
from typing import Dict, Set, Optional, List, Any
from collections import defaultdict
import websockets
from ecdsa import SigningKey, VerifyingKey, SECP256k1
from p2p_security import P2PSecurityManager


class P2PNetwork:
    def __init__(self, device_id: str, port: int = 8765, seed_nodes: Optional[List[str]] = None,
                 private_key: Optional[str] = None, public_key: Optional[str] = None, testnet_mode: bool = False):
        self.device_id = device_id
        self.port = port
        self.seed_nodes = seed_nodes or []
        self.peers: Dict[str, Any] = {}
        self.outbound_peers: Dict[str, Any] = {}
        self.known_device_ids: Set[str] = {device_id}
        self.known_peer_addresses: Set[str] = set()
        self.message_handlers = {}
        self.sync_handler = None
        self.private_key = private_key
        self.public_key = public_key
        self.peer_public_keys: Dict[str, str] = {}
        
        self.MAX_TOTAL_PEERS = 100
        self.MAX_PEERS_PER_IP = 10 if testnet_mode else 3
        self.CONNECTION_RATE_LIMIT = 10
        self.RATE_LIMIT_WINDOW = 60
        self.testnet_mode = testnet_mode
        
        if testnet_mode:
            print(f"🧪 TESTNET MODE: MAX_PEERS_PER_IP increased to {self.MAX_PEERS_PER_IP} (allows localhost co-location)")
        
        self.peer_ips: Dict[str, str] = {}
        self.ip_connection_count: Dict[str, int] = defaultdict(int)
        self.connection_attempts: Dict[str, List[float]] = defaultdict(list)
        self.banned_ips: Set[str] = set()
        
        self.peer_validator_addresses: Dict[str, str] = {}
        self.peer_last_seen: Dict[str, float] = {}
        
        # LIVENESS FIX: Track validator last seen by VALIDATOR ADDRESS (not peer_id)
        # This is used for P2P-based liveness detection for explorer display
        # Note: asyncio is single-threaded, so no lock needed for dict updates
        # Use get_all_validator_last_seen() to get a safe copy for iteration
        self._validator_last_seen: Dict[str, float] = {}
        
        # PEER REPUTATION SYSTEM (P2P-only, does NOT affect consensus)
        # Score range: 0-100, starts at 50, quarantine threshold at 20
        self.peer_reputation: Dict[str, int] = {}
        self.quarantined_peers: Set[str] = set()
        
        # Reputation score constants
        self.REP_DEFAULT_SCORE = 50
        self.REP_MIN_SCORE = 0
        self.REP_MAX_SCORE = 100
        self.REP_QUARANTINE_THRESHOLD = 20
        
        # Positive adjustments
        self.REP_GOOD_BLOCK = 5
        self.REP_VALID_SYNC = 3
        self.REP_GOOD_TX = 2
        self.REP_STABLE_CONNECTION = 2
        
        # Negative adjustments
        self.REP_INVALID_BLOCK = -15
        self.REP_INVALID_TX = -10
        self.REP_SPAM_FLOOD = -20
        self.REP_EMPTY_RESPONSE = -5
        self.REP_DISCONNECT_RAPID = -3
        self.REP_AUTH_FAILURE = -10
        
        print(f"📊 Peer reputation system initialized (quarantine threshold: {self.REP_QUARANTINE_THRESHOLD})")
        
        # P2P RATE LIMITER (spam protection, does NOT affect consensus)
        # Thresholds for different message types
        self.RATE_MAX_MESSAGES_PER_SECOND = 30
        self.RATE_MAX_SYNC_PER_MINUTE = 8
        self.RATE_MAX_TX_PER_SECOND = 10
        self.RATE_HARD_LIMIT_DURATION = 5.0  # Seconds to ignore peer after hard limit
        
        # Per-peer rate tracking (peer_id -> counters)
        self.peer_message_counts: Dict[str, int] = defaultdict(int)
        self.peer_sync_counts: Dict[str, int] = defaultdict(int)
        self.peer_tx_counts: Dict[str, int] = defaultdict(int)
        self.peer_hard_limited: Dict[str, float] = {}  # peer_id -> time until unblock
        
        # Track last reset times for counter cleanup
        self._last_second_reset = time.time()
        self._last_minute_reset = time.time()
        
        print(f"🛡️  P2P Rate limiter initialized (msg: {self.RATE_MAX_MESSAGES_PER_SECOND}/s, sync: {self.RATE_MAX_SYNC_PER_MINUTE}/min, tx: {self.RATE_MAX_TX_PER_SECOND}/s)")
        
        # CRITICAL SECURITY: Initialize security manager for mandatory authentication
        self.security_manager = P2PSecurityManager()
        print("🔒 P2P Security Manager initialized - mandatory authentication enabled")
    
    def register_handler(self, message_type: str, handler):
        self.message_handlers[message_type] = handler
    
    def register_sync_handler(self, handler):
        self.sync_handler = handler
    
    def _get_peer_ip(self, websocket) -> str:
        try:
            remote_address = websocket.remote_address
            if remote_address:
                return remote_address[0]
        except Exception:
            pass
        return "unknown"
    
    def _is_ip_rate_limited(self, ip: str) -> bool:
        if ip in self.banned_ips:
            return True
        
        current_time = time.time()
        attempts = self.connection_attempts[ip]
        
        recent_attempts = [t for t in attempts if current_time - t < self.RATE_LIMIT_WINDOW]
        self.connection_attempts[ip] = recent_attempts
        
        if len(recent_attempts) >= self.CONNECTION_RATE_LIMIT:
            self.banned_ips.add(ip)
            return True
        
        return False
    
    def _can_accept_peer(self, ip: str) -> bool:
        total_peers = len(self.peers) + len(self.outbound_peers)
        if total_peers >= self.MAX_TOTAL_PEERS:
            print(f"🚫 P2P: Rejected peer from {ip} - total peer limit reached ({total_peers}/{self.MAX_TOTAL_PEERS})")
            return False
        
        if self.ip_connection_count[ip] >= self.MAX_PEERS_PER_IP:
            print(f"🚫 P2P: Rejected peer from {ip} - per-IP limit reached ({self.ip_connection_count[ip]}/{self.MAX_PEERS_PER_IP})")
            return False
        
        return True
    
    # ============================================================
    # VALIDATOR LIVENESS TRACKING (Thread-Safe API)
    # ============================================================
    
    def update_validator_last_seen(self, validator_address: str, timestamp: float = None):
        """
        Update validator liveness timestamp.
        
        LIVENESS FIX: Provides clean API for updating validator last seen times.
        Safe in asyncio context (single-threaded event loop).
        
        Args:
            validator_address: The validator address to update
            timestamp: Optional timestamp (defaults to current time)
        """
        if timestamp is None:
            timestamp = time.time()
        self._validator_last_seen[validator_address] = timestamp
    
    def get_validator_last_seen(self, validator_address: str) -> float:
        """
        Get validator liveness timestamp.
        
        Args:
            validator_address: The validator address to query
            
        Returns:
            Last seen timestamp, or 0 if never seen
        """
        return self._validator_last_seen.get(validator_address, 0)
    
    def get_all_validator_last_seen(self) -> Dict[str, float]:
        """
        Get a copy of all validator liveness timestamps.
        
        Returns a COPY to prevent "dict changed size during iteration" errors
        when the explorer iterates over liveness data while updates occur.
        
        Returns:
            Dict copy of validator addresses to last seen timestamps
        """
        return dict(self._validator_last_seen)
    
    # ============================================================
    # PEER REPUTATION SYSTEM
    # P2P-only feature - does NOT affect consensus
    # ============================================================
    
    def get_peer_score(self, peer_id: str) -> int:
        """Get reputation score for a peer (default: 50)."""
        return self.peer_reputation.get(peer_id, self.REP_DEFAULT_SCORE)
    
    def adjust_peer_score(self, peer_id: str, adjustment: int, reason: str = "") -> int:
        """
        Adjust peer reputation score.
        
        Args:
            peer_id: Peer identifier
            adjustment: Score change (+ve for good, -ve for bad behavior)
            reason: Optional reason for logging
            
        Returns:
            New score after adjustment
        """
        old_score = self.get_peer_score(peer_id)
        new_score = max(self.REP_MIN_SCORE, min(self.REP_MAX_SCORE, old_score + adjustment))
        self.peer_reputation[peer_id] = new_score
        
        # Log score changes
        direction = "+" if adjustment > 0 else ""
        peer_short = peer_id[:16] if len(peer_id) > 16 else peer_id
        print(f"📊 PEER SCORE: {peer_short}... {old_score} → {new_score} ({direction}{adjustment}) {reason}")
        
        # Check quarantine status
        if new_score < self.REP_QUARANTINE_THRESHOLD:
            if peer_id not in self.quarantined_peers:
                self.quarantined_peers.add(peer_id)
                print(f"⚠️  PEER QUARANTINED: {peer_short}... (score {new_score} < {self.REP_QUARANTINE_THRESHOLD})")
        else:
            # Remove from quarantine if score recovered
            if peer_id in self.quarantined_peers:
                self.quarantined_peers.discard(peer_id)
                print(f"✅ PEER UNQUARANTINED: {peer_short}... (score {new_score} >= {self.REP_QUARANTINE_THRESHOLD})")
        
        return new_score
    
    def is_peer_quarantined(self, peer_id: str) -> bool:
        """Check if peer is quarantined (low reputation)."""
        return peer_id in self.quarantined_peers
    
    def reward_good_block(self, peer_id: str):
        """Reward peer for sending a valid block."""
        self.adjust_peer_score(peer_id, self.REP_GOOD_BLOCK, "[valid block]")
    
    def reward_valid_sync(self, peer_id: str):
        """Reward peer for providing valid sync data."""
        self.adjust_peer_score(peer_id, self.REP_VALID_SYNC, "[valid sync]")
    
    def reward_good_tx(self, peer_id: str):
        """Reward peer for relaying a valid transaction."""
        self.adjust_peer_score(peer_id, self.REP_GOOD_TX, "[valid tx]")
    
    def reward_stable_connection(self, peer_id: str):
        """Reward peer for maintaining stable connection."""
        self.adjust_peer_score(peer_id, self.REP_STABLE_CONNECTION, "[stable]")
    
    def penalize_invalid_block(self, peer_id: str):
        """Penalize peer for sending an invalid block."""
        self.adjust_peer_score(peer_id, self.REP_INVALID_BLOCK, "[INVALID BLOCK]")
    
    def penalize_invalid_tx(self, peer_id: str):
        """Penalize peer for sending an invalid transaction."""
        self.adjust_peer_score(peer_id, self.REP_INVALID_TX, "[INVALID TX]")
    
    def penalize_spam(self, peer_id: str):
        """Penalize peer for flooding/spamming."""
        self.adjust_peer_score(peer_id, self.REP_SPAM_FLOOD, "[SPAM/FLOOD]")
    
    def penalize_empty_response(self, peer_id: str):
        """Penalize peer for sending empty/bogus responses."""
        self.adjust_peer_score(peer_id, self.REP_EMPTY_RESPONSE, "[empty response]")
    
    def penalize_disconnect(self, peer_id: str):
        """Penalize peer for rapid/unexpected disconnection."""
        self.adjust_peer_score(peer_id, self.REP_DISCONNECT_RAPID, "[disconnect]")
    
    def penalize_auth_failure(self, peer_id: str):
        """Penalize peer for authentication failure."""
        self.adjust_peer_score(peer_id, self.REP_AUTH_FAILURE, "[AUTH FAIL]")
    
    def get_peers_by_reputation(self) -> List[str]:
        """
        Get list of all connected peers sorted by reputation (highest first).
        Excludes quarantined peers from the list.
        
        Returns:
            List of peer_ids sorted by score descending
        """
        all_peers = list(self.peers.keys()) + list(self.outbound_peers.keys())
        
        # Filter out quarantined peers
        available_peers = [p for p in all_peers if not self.is_peer_quarantined(p)]
        
        # Sort by reputation score (highest first)
        available_peers.sort(key=lambda p: self.get_peer_score(p), reverse=True)
        
        return available_peers
    
    def get_best_sync_peer(self) -> Optional[str]:
        """
        Get the best peer for syncing (highest reputation, not quarantined).
        Falls back to any available peer if all are quarantined.
        
        Returns:
            Best peer_id for sync, or None if no peers available
        """
        ranked_peers = self.get_peers_by_reputation()
        
        if ranked_peers:
            return ranked_peers[0]
        
        # Fallback: if all peers are quarantined, use any available (testnet friendly)
        all_peers = list(self.peers.keys()) + list(self.outbound_peers.keys())
        if all_peers:
            print(f"⚠️  All peers quarantined, using fallback peer for sync")
            return all_peers[0]
        
        return None
    
    def get_reputation_stats(self) -> Dict[str, Any]:
        """Get summary stats about peer reputation."""
        all_peers = list(self.peers.keys()) + list(self.outbound_peers.keys())
        scores = [self.get_peer_score(p) for p in all_peers]
        
        return {
            "total_peers": len(all_peers),
            "quarantined": len(self.quarantined_peers),
            "avg_score": sum(scores) / len(scores) if scores else 0,
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
        }
    
    # ============================================================
    # P2P RATE LIMITER
    # Spam protection - does NOT affect consensus
    # ============================================================
    
    def _reset_rate_counters(self):
        """Reset rate counters periodically (called from message handlers)."""
        current_time = time.time()
        
        # Reset per-second counters every second
        if current_time - self._last_second_reset >= 1.0:
            self.peer_message_counts.clear()
            self.peer_tx_counts.clear()
            self._last_second_reset = current_time
        
        # Reset per-minute counters every minute
        if current_time - self._last_minute_reset >= 60.0:
            self.peer_sync_counts.clear()
            self._last_minute_reset = current_time
        
        # Clear expired hard limits
        expired = [p for p, t in self.peer_hard_limited.items() if current_time > t]
        for peer_id in expired:
            del self.peer_hard_limited[peer_id]
            print(f"🔓 RATE LIMIT: Peer {peer_id[:16]}... unblocked after hard limit")
    
    def is_peer_rate_limited(self, peer_id: str) -> bool:
        """Check if peer is currently hard-limited."""
        if peer_id in self.peer_hard_limited:
            if time.time() < self.peer_hard_limited[peer_id]:
                return True
            else:
                # Expired, remove
                del self.peer_hard_limited[peer_id]
        return False
    
    def _apply_hard_limit(self, peer_id: str, reason: str):
        """Apply hard limit to peer (ignore for RATE_HARD_LIMIT_DURATION seconds)."""
        self.peer_hard_limited[peer_id] = time.time() + self.RATE_HARD_LIMIT_DURATION
        print(f"🚫 RATE LIMIT HARD: Peer {peer_id[:16]}... blocked for {self.RATE_HARD_LIMIT_DURATION}s ({reason})")
        # Apply reputation penalty
        self.penalize_spam(peer_id)
    
    async def check_rate_limit(self, peer_id: str, message_type: str) -> tuple:
        """
        Check rate limits for a peer and message type.
        
        Returns:
            (allowed: bool, should_delay: bool)
            - allowed=False means hard limit, reject message
            - should_delay=True means soft limit, add delay
        """
        self._reset_rate_counters()
        
        # Check if peer is hard-limited
        if self.is_peer_rate_limited(peer_id):
            return (False, False)  # Reject
        
        # Increment and check general message counter
        self.peer_message_counts[peer_id] += 1
        msg_count = self.peer_message_counts[peer_id]
        
        # Check message type specific limits
        if message_type in ("new_transaction", "transaction"):
            self.peer_tx_counts[peer_id] += 1
            tx_count = self.peer_tx_counts[peer_id]
            
            # TX hard limit: 2x threshold
            if tx_count > self.RATE_MAX_TX_PER_SECOND * 2:
                self._apply_hard_limit(peer_id, f"TX flood: {tx_count}/s")
                return (False, False)
            
            # TX soft limit (apply minor penalty on first exceed per window)
            if tx_count == self.RATE_MAX_TX_PER_SECOND + 1:
                print(f"⚠️  RATE LIMIT: Peer {peer_id[:16]}... exceeded TX limit ({tx_count}/s)")
                self.adjust_reputation(peer_id, -5)  # Minor penalty for soft limit
            if tx_count > self.RATE_MAX_TX_PER_SECOND:
                return (True, True)  # Allow with delay
        
        elif message_type == "sync_request":
            self.peer_sync_counts[peer_id] += 1
            sync_count = self.peer_sync_counts[peer_id]
            
            # Sync hard limit: 2x threshold
            if sync_count > self.RATE_MAX_SYNC_PER_MINUTE * 2:
                self._apply_hard_limit(peer_id, f"Sync flood: {sync_count}/min")
                return (False, False)
            
            # Sync soft limit (apply minor penalty on first exceed per window)
            if sync_count == self.RATE_MAX_SYNC_PER_MINUTE + 1:
                print(f"⚠️  RATE LIMIT: Peer {peer_id[:16]}... exceeded sync limit ({sync_count}/min)")
                self.adjust_reputation(peer_id, -5)  # Minor penalty for soft limit
            if sync_count > self.RATE_MAX_SYNC_PER_MINUTE:
                return (True, True)  # Allow with delay
        
        # General message hard limit: 2x threshold
        if msg_count > self.RATE_MAX_MESSAGES_PER_SECOND * 2:
            self._apply_hard_limit(peer_id, f"Message flood: {msg_count}/s")
            return (False, False)
        
        # General message soft limit (apply minor penalty on first exceed per window)
        if msg_count == self.RATE_MAX_MESSAGES_PER_SECOND + 1:
            print(f"⚠️  RATE LIMIT: Peer {peer_id[:16]}... exceeded message limit ({msg_count}/s)")
            self.adjust_reputation(peer_id, -5)  # Minor penalty for soft limit
        if msg_count > self.RATE_MAX_MESSAGES_PER_SECOND:
            return (True, True)  # Allow with delay
        
        return (True, False)  # Allow, no delay
    
    def get_rate_limit_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics."""
        return {
            "hard_limited_peers": len(self.peer_hard_limited),
            "active_message_counts": len(self.peer_message_counts),
            "active_sync_counts": len(self.peer_sync_counts),
            "active_tx_counts": len(self.peer_tx_counts),
        }
    
    def _sign_message(self, message: str) -> str:
        if not self.private_key:
            return ""
        try:
            sk = SigningKey.from_string(bytes.fromhex(self.private_key), curve=SECP256k1)
            message_hash = hashlib.sha256(message.encode()).digest()
            signature = sk.sign(message_hash)
            return signature.hex()
        except:
            return ""
    
    def _verify_message(self, message: str, signature: str, public_key: str) -> bool:
        if not signature or not public_key:
            return False
        try:
            vk = VerifyingKey.from_string(bytes.fromhex(public_key), curve=SECP256k1)
            message_hash = hashlib.sha256(message.encode()).digest()
            signature_bytes = bytes.fromhex(signature)
            return vk.verify(signature_bytes, message_hash)
        except:
            return False
    
    async def handle_client(self, websocket, path: str = ""):
        peer_ip = self._get_peer_ip(websocket)
        
        if self._is_ip_rate_limited(peer_ip):
            print(f"🚫 P2P: Rejected incoming connection from {peer_ip} - rate limited or banned")
            await websocket.close()
            return
        
        self.connection_attempts[peer_ip].append(time.time())
        
        if not self._can_accept_peer(peer_ip):
            await websocket.close()
            return
        
        peer_id = str(uuid.uuid4())
        self.peers[peer_id] = websocket
        self.peer_ips[peer_id] = peer_ip
        self.ip_connection_count[peer_ip] += 1
        
        # Track connection start time for rapid disconnect detection
        connection_start = time.time()
        MIN_CONNECTION_DURATION = 5.0  # Seconds - connections shorter than this are suspicious
        
        print(f"✅ P2P: Accepted incoming connection from {peer_ip} (peer_id: {peer_id[:8]}...)")
        
        try:
            async for message in websocket:
                await self.handle_message(message, peer_id, websocket)
        except websockets.exceptions.ConnectionClosed:
            print(f"🔌 P2P: Connection closed from {peer_ip} (peer_id: {peer_id[:8]}...)")
        finally:
            # REPUTATION: Check for rapid disconnect (possible attack pattern)
            connection_duration = time.time() - connection_start
            if connection_duration < MIN_CONNECTION_DURATION:
                self.penalize_disconnect(peer_id)
            elif connection_duration > 60:  # Stable connection for 60+ seconds
                self.reward_stable_connection(peer_id)
            
            # LIVENESS FIX: Clean up validator address tracking when peer disconnects
            if peer_id in self.peer_validator_addresses:
                del self.peer_validator_addresses[peer_id]
            
            if peer_id in self.peers:
                del self.peers[peer_id]
            if peer_id in self.peer_ips:
                ip = self.peer_ips[peer_id]
                del self.peer_ips[peer_id]
                self.ip_connection_count[ip] = max(0, self.ip_connection_count[ip] - 1)
            if peer_id in self.peer_public_keys:
                del self.peer_public_keys[peer_id]
            if peer_id in self.peer_last_seen:
                del self.peer_last_seen[peer_id]
    
    async def handle_message(self, message: str, peer_id: str, websocket):
        """
        Handle incoming P2P message with MANDATORY authentication.
        
        CRITICAL SECURITY: All messages MUST be authenticated.
        Unsigned or invalid messages are REJECTED immediately.
        
        Args:
            message: JSON-encoded message string
            peer_id: Peer identifier (UUID or address)
            websocket: WebSocket connection object for direct replies
        """
        try:
            data = json.loads(message)
            message_type = data.get("type")
            
            # CRITICAL SECURITY FIX: MANDATORY authentication via security manager
            # Old code only verified IF peer key was known - now we ALWAYS verify!
            
            # Validate message authentication (signature, timestamp, nonce)
            valid, reason = self.security_manager.validate_message_auth(
                data, 
                peer_id,
                verify_signature_func=self._verify_message
            )
            
            if not valid:
                print(f"🚫 SECURITY: Rejected message from peer {peer_id}: {reason}")
                # REPUTATION: Penalize for auth failure
                self.penalize_auth_failure(peer_id)
                # Ban peer if security manager flagged them
                if not self.security_manager.is_peer_trusted(peer_id):
                    peer_ip = self.peer_ips.get(peer_id, "unknown")
                    self.banned_ips.add(peer_ip)
                    print(f"🚫 SECURITY: Banned peer {peer_id} (IP: {peer_ip}) for repeated auth failures")
                return  # REJECT the message
            
            # Authentication passed - record the verified message
            self.security_manager.record_verified_message(data, peer_id)
            
            # RATE LIMITING: Check if peer is flooding messages
            allowed, should_delay = await self.check_rate_limit(peer_id, message_type)
            
            if not allowed:
                # Hard limit - reject message silently
                return
            
            if should_delay:
                # Soft limit - add delay to slow down spammer
                await asyncio.sleep(0.05)
            
            # Store/update peer public key
            message_public_key = data.get("public_key", "")
            if message_public_key:
                self.peer_public_keys[peer_id] = message_public_key
            
            # LIVENESS FIX: Track validator last seen on ANY incoming message
            # This ensures we have accurate P2P liveness for explorer display
            if peer_id in self.peer_validator_addresses:
                validator_addr = self.peer_validator_addresses[peer_id]
                self.update_validator_last_seen(validator_addr)
            
            # Process authenticated message
            if message_type == "announce_node":
                device_id = data.get("device_id")
                reward_address = data.get("reward_address")
                
                if reward_address:
                    self.peer_validator_addresses[peer_id] = reward_address
                    self.peer_last_seen[peer_id] = time.time()
                    # LIVENESS FIX: Also track by validator address (thread-safe)
                    self.update_validator_last_seen(reward_address)
                
                if device_id and device_id not in self.known_device_ids:
                    self.known_device_ids.add(device_id)
                    print(f"🤝 P2P: Completed handshake with peer {peer_id[:8]}... (device: {device_id[:16]}..., validator: {reward_address[:16] if reward_address else 'N/A'}...)")
            
            elif message_type == "peer_list":
                peers = data.get("peers", [])
                for peer_addr in peers:
                    if peer_addr not in self.known_peer_addresses:
                        self.known_peer_addresses.add(peer_addr)
                        asyncio.create_task(self.connect_to_peer(peer_addr))
            
            elif message_type == "sync_request":
                if self.sync_handler:
                    # CRITICAL FIX: Pass websocket directly for reliable sync responses
                    await self.sync_handler(data, peer_id, websocket)
            
            if message_type in self.message_handlers:
                handler = self.message_handlers[message_type]
                await handler(data, peer_id)
            
        except json.JSONDecodeError:
            # Invalid JSON - ignore malformed messages from peers
            print(f"⚠️  Received invalid JSON from peer {peer_id}")
            pass
        except Exception as e:
            # Log unexpected errors during message handling
            # Don't crash the handler - just skip the message
            import traceback
            print(f"⚠️  Error handling P2P message from peer {peer_id}: {e}")
            traceback.print_exc()
    
    async def broadcast(self, message_type: str, data: dict, exclude_peer: Optional[str] = None):
        """
        Broadcast message to all peers with mandatory security fields.
        
        CRITICAL SECURITY: Adds timestamp, nonce, signature, and public_key.
        
        Args:
            message_type: Type of message to broadcast
            data: Message data dictionary
            exclude_peer: Optional peer_id to exclude from broadcast (for gossip)
        """
        message_data = {
            "type": message_type,
            "device_id": self.device_id,
            **data
        }
        
        # CRITICAL SECURITY FIX: Add timestamp and nonce via security manager
        message_data = self.security_manager.create_secure_message(message_data)
        
        # Add public key
        if self.public_key:
            message_data["public_key"] = self.public_key
        else:
            raise ValueError("Cannot broadcast: node has no public key for authentication")
        
        # Sign the message (excluding signature field)
        message_str = json.dumps({k: message_data[k] for k in sorted(message_data.keys()) if k != "signature"}, sort_keys=True)
        signature = self._sign_message(message_str)
        
        if not signature:
            raise ValueError("Cannot broadcast: message signing failed")
        
        message_data["signature"] = signature
        message = json.dumps(message_data)
        
        # CRITICAL FIX: Create snapshot to prevent "dictionary changed size during iteration"
        # Other tasks can add/remove peers concurrently, so we need a stable copy
        disconnected = []
        for peer_id, websocket in list(self.peers.items()):
            # Skip excluded peer (for block gossip to prevent loops)
            if exclude_peer and peer_id == exclude_peer:
                continue
                
            try:
                await websocket.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.append(peer_id)
            except Exception:
                disconnected.append(peer_id)
        
        for peer_id in disconnected:
            if peer_id in self.peers:
                del self.peers[peer_id]
        
        disconnected_outbound = []
        for peer_addr, websocket in list(self.outbound_peers.items()):
            try:
                await websocket.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected_outbound.append(peer_addr)
            except Exception:
                disconnected_outbound.append(peer_addr)
        
        for peer_addr in disconnected_outbound:
            if peer_addr in self.outbound_peers:
                del self.outbound_peers[peer_addr]
    
    async def send_to_peer(self, peer_id: str, message_type: str, data: dict):
        """
        Send message to a SPECIFIC peer (targeted messaging for sync responses).
        
        CRITICAL FIX: Use this instead of broadcast() for sync responses.
        This ensures blocks are delivered directly to the requesting peer,
        preventing authentication or routing mismatches.
        
        Args:
            peer_id: Peer identifier (UUID for inbound, address for outbound)
            message_type: Type of message (e.g., "new_block")
            data: Message payload
        """
        message_data = {
            "type": message_type,
            "device_id": self.device_id,
            **data
        }
        
        # Add security fields (timestamp, nonce) via security manager
        message_data = self.security_manager.create_secure_message(message_data)
        
        # Add public key
        if self.public_key:
            message_data["public_key"] = self.public_key
        else:
            print(f"⚠️  SYNC SEND FAILED: No public key for authentication to peer {peer_id}")
            return False
        
        # Sign the message
        message_str = json.dumps({k: message_data[k] for k in sorted(message_data.keys()) if k != "signature"}, sort_keys=True)
        signature = self._sign_message(message_str)
        
        if not signature:
            print(f"⚠️  SYNC SEND FAILED: Message signing failed for peer {peer_id}")
            return False
        
        message_data["signature"] = signature
        message = json.dumps(message_data)
        
        # Try to send to inbound peer first (UUID-based peer_id)
        if peer_id in self.peers:
            try:
                await self.peers[peer_id].send(message)
                print(f"✅ SYNC SENT: {message_type} to inbound peer {peer_id[:8]}...")
                return True
            except websockets.exceptions.ConnectionClosed:
                del self.peers[peer_id]
                print(f"⚠️  SYNC SEND FAILED: Connection closed to peer {peer_id[:8]}...")
                return False
            except Exception as e:
                print(f"⚠️  SYNC SEND FAILED: Error sending to peer {peer_id[:8]}... - {e}")
                return False
        
        # Try outbound peer (address-based peer_id)
        if peer_id in self.outbound_peers:
            try:
                await self.outbound_peers[peer_id].send(message)
                print(f"✅ SYNC SENT: {message_type} to outbound peer {peer_id}")
                return True
            except websockets.exceptions.ConnectionClosed:
                del self.outbound_peers[peer_id]
                print(f"⚠️  SYNC SEND FAILED: Connection closed to peer {peer_id}")
                return False
            except Exception as e:
                print(f"⚠️  SYNC SEND FAILED: Error sending to peer {peer_id} - {e}")
                return False
        
        print(f"⚠️  SYNC SEND FAILED: Peer {peer_id} not found in peers or outbound_peers")
        return False
    
    async def send_to_websocket(self, websocket, message_type: str, data: dict):
        """
        Send message directly to a SPECIFIC websocket (for sync responses).
        
        CRITICAL FIX: Bypasses peer_id lookups by sending directly to websocket.
        This ensures sync responses reach the requesting peer even if the peer_id
        is removed from dictionaries during connection churn.
        
        Args:
            websocket: WebSocket connection object
            message_type: Type of message (e.g., "new_block")
            data: Message payload
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        try:
            message_data = {
                "type": message_type,
                "device_id": self.device_id,
                **data
            }
            
            # Add security fields (timestamp, nonce) via security manager
            message_data = self.security_manager.create_secure_message(message_data)
            
            # Add public key
            if self.public_key:
                message_data["public_key"] = self.public_key
            else:
                print(f"⚠️  WEBSOCKET SEND FAILED: No public key for authentication")
                return False
            
            # Sign the message
            message_str = json.dumps({k: message_data[k] for k in sorted(message_data.keys()) if k != "signature"}, sort_keys=True)
            signature = self._sign_message(message_str)
            
            if not signature:
                print(f"⚠️  WEBSOCKET SEND FAILED: Message signing failed")
                return False
            
            message_data["signature"] = signature
            message = json.dumps(message_data)
            
            # Send directly to websocket
            await websocket.send(message)
            return True
            
        except websockets.exceptions.ConnectionClosed:
            print(f"⚠️  WEBSOCKET SEND FAILED: Connection closed")
            return False
        except Exception as e:
            print(f"⚠️  WEBSOCKET SEND FAILED: {e}")
            return False
    
    async def connect_to_peer(self, peer_address: str):
        if peer_address in self.outbound_peers:
            return
        
        if peer_address == f"ws://localhost:{self.port}":
            return
        
        print(f"🔄 P2P: Attempting to connect to {peer_address}...")
        
        try:
            websocket = await asyncio.wait_for(
                websockets.connect(peer_address),
                timeout=5.0
            )
            self.outbound_peers[peer_address] = websocket
            
            # CRITICAL SECURITY FIX: Use security_manager for handshake authentication
            # This adds mandatory timestamp/nonce fields for P2P security
            announce_msg = {
                "type": "announce_node",
                "device_id": self.device_id
            }
            
            # Add security fields (timestamp, nonce) via security manager
            announce_msg = self.security_manager.create_secure_message(announce_msg)
            
            # Add public key (required for authentication)
            if self.public_key:
                announce_msg["public_key"] = self.public_key
            else:
                raise ValueError("Cannot connect: node has no public key for authentication")
            
            # Sign the message (same pattern as broadcast())
            message_str = json.dumps({k: announce_msg[k] for k in sorted(announce_msg.keys()) if k != "signature"}, sort_keys=True)
            signature = self._sign_message(message_str)
            if not signature:
                raise ValueError("Cannot connect: message signing failed")
            announce_msg["signature"] = signature
            
            await websocket.send(json.dumps(announce_msg))
            
            print(f"✅ P2P: Connected to {peer_address} successfully!")
            
            # Track connection start time for reputation tracking
            connection_start = time.time()
            asyncio.create_task(self.handle_outbound_peer(peer_address, websocket, connection_start))
            
        except asyncio.TimeoutError:
            print(f"⏱️  P2P: Connection to {peer_address} timed out after 5 seconds")
            print(f"   Possible causes: Node offline, firewall blocking, wrong IP/port")
        except ConnectionRefusedError:
            print(f"🚫 P2P: Connection to {peer_address} refused")
            print(f"   Possible causes: Node not running, port not open, firewall blocking")
        except OSError as e:
            print(f"⚠️  P2P: Network error connecting to {peer_address}: {e}")
            print(f"   Possible causes: DNS resolution failed, network unreachable, firewall")
        except ValueError as e:
            print(f"🔐 P2P: Authentication error connecting to {peer_address}: {e}")
        except Exception as e:
            print(f"❌ P2P: Unexpected error connecting to {peer_address}: {type(e).__name__}: {e}")
    
    async def handle_outbound_peer(self, peer_address: str, websocket, connection_start: float = None):
        """Handle outbound peer with reputation tracking."""
        MIN_CONNECTION_DURATION = 5.0  # Seconds - connections shorter than this are suspicious
        
        # Use current time if connection_start not provided (backward compat)
        if connection_start is None:
            connection_start = time.time()
        
        try:
            async for message in websocket:
                await self.handle_message(message, peer_address, websocket)
        except websockets.exceptions.ConnectionClosed as e:
            print(f"🔌 P2P: Outbound connection to {peer_address} closed")
            if e.code:
                print(f"   Close code: {e.code}, reason: {e.reason or 'none'}")
        except Exception as e:
            print(f"⚠️  P2P: Unexpected error in outbound connection to {peer_address}")
            print(f"   Error: {type(e).__name__}: {e}")
        finally:
            # REPUTATION: Check for rapid disconnect
            connection_duration = time.time() - connection_start
            if connection_duration < MIN_CONNECTION_DURATION:
                self.penalize_disconnect(peer_address)
            elif connection_duration > 60:  # Stable connection for 60+ seconds
                self.reward_stable_connection(peer_address)
            
            if peer_address in self.outbound_peers:
                del self.outbound_peers[peer_address]
                print(f"🔗 P2P: Removed outbound peer {peer_address} from active connections")
    
    async def connect_to_seeds(self):
        for seed_node in self.seed_nodes:
            if seed_node != f"ws://localhost:{self.port}":
                asyncio.create_task(self.connect_to_peer(seed_node))
                await asyncio.sleep(0.5)
    
    async def peer_discovery_loop(self):
        while True:
            await asyncio.sleep(30)
            
            peer_list = list(self.known_peer_addresses)
            await self.broadcast("peer_list", {"peers": peer_list})
            
            for seed_node in self.seed_nodes:
                if seed_node not in self.outbound_peers:
                    asyncio.create_task(self.connect_to_peer(seed_node))
    
    async def start_server(self):
        async with websockets.serve(self.handle_client, "0.0.0.0", self.port):
            await asyncio.Future()
    
    def get_peer_count(self) -> int:
        return len(self.peers) + len(self.outbound_peers)
    
    def get_known_nodes(self) -> Set[str]:
        return self.known_device_ids.copy()
    
    def sign_message(self, message: dict) -> dict:
        """
        Sign a P2P message with this node's private key.
        
        Args:
            message: Dictionary containing message data
            
        Returns:
            Dictionary with message data, public_key, and signature
            
        CRITICAL SECURITY: All P2P messages MUST be signed to prevent:
        - Message forgery (attackers creating fake messages)
        - Replay attacks (re-sending old messages)
        - Man-in-the-middle attacks
            
        CRITICAL FIX: Must match handle_message() verification path exactly.
        The signed payload includes public_key to align with network verification.
        """
        if not self.private_key or not self.public_key:
            raise ValueError("Cannot sign message: node has no private/public key")
        
        # Create message copy with public key
        signed_message = dict(message)
        signed_message["public_key"] = self.public_key
        
        # Sign the message INCLUDING public_key (to match handle_message verification)
        # Exclude only the signature field
        message_str = json.dumps({k: signed_message[k] for k in sorted(signed_message.keys())}, sort_keys=True)
        signature = self._sign_message(message_str)
        
        if not signature:
            raise ValueError("Failed to generate message signature")
        
        signed_message["signature"] = signature
        return signed_message
    
    def verify_message(self, message: dict) -> bool:
        """
        Verify a P2P message signature.
        
        Args:
            message: Dictionary containing message data with signature and public_key
            
        Returns:
            True if signature is valid, False otherwise
            
        CRITICAL SECURITY: Always verify message signatures before processing to prevent:
        - Accepting forged messages from attackers
        - Processing tampered messages (modified after signing)
        - Replay attacks with stolen signatures
        """
        # Check if message has required fields
        if "signature" not in message:
            return False
        if "public_key" not in message:
            return False
        
        signature = message["signature"]
        public_key = message["public_key"]
        
        # Reconstruct original message (exclude only signature, keep public_key)
        # CRITICAL: Must match handle_message() and sign_message() - includes public_key
        message_data = {k: v for k, v in message.items() if k != "signature"}
        message_str = json.dumps({k: message_data[k] for k in sorted(message_data.keys())}, sort_keys=True)
        
        # Verify signature
        return self._verify_message(message_str, signature, public_key)
