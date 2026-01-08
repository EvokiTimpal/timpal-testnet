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
        
        # CRITICAL SECURITY: Initialize security manager for mandatory authentication
        self.security_manager = P2PSecurityManager()
        print("🔒 P2P Security Manager initialized - mandatory authentication enabled")
        
        # Callback for when peer connects (used for sync-after-reconnect)
        self.on_peer_connected_callback = None
        
        # TIMPAL 10-BLOCK REWARD CUTOFF: Track peer-to-validator mapping
        # Maps peer_id -> validator_address for liveness tracking
        self.peer_validator_addresses: Dict[str, str] = {}
        
        # Callbacks for validator liveness tracking
        self.on_validator_offline_callback = None  # Called when validator disconnects
        self.on_validator_online_callback = None   # Called when validator reconnects
        
        # TIMPAL 10-BLOCK REWARD CUTOFF: Store reward_address for handshake
        # This is set by Node after P2P initialization
        self.reward_address: Optional[str] = None
    
    def register_handler(self, message_type: str, handler):
        self.message_handlers[message_type] = handler
    
    def register_sync_handler(self, handler):
        self.sync_handler = handler
    
    def register_on_peer_connected(self, callback):
        """Register callback for when a peer successfully connects.
        Used by Node to request sync after reconnection."""
        self.on_peer_connected_callback = callback
    
    def register_validator_liveness_callbacks(self, on_offline, on_online):
        """
        Register callbacks for validator liveness tracking.
        
        TIMPAL 10-BLOCK REWARD CUTOFF:
        - on_offline(validator_address): Called when a validator disconnects
        - on_online(validator_address): Called when a validator reconnects
        
        These callbacks allow the ledger to track offline_since_height for each validator.
        """
        self.on_validator_offline_callback = on_offline
        self.on_validator_online_callback = on_online
    
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
        print(f"🔔 P2P: handle_client called - new connection attempt")
        peer_ip = self._get_peer_ip(websocket)
        print(f"🔔 P2P: Peer IP detected as: {peer_ip}")
        
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
        
        print(f"✅ P2P: Accepted incoming connection from {peer_ip} (peer_id: {peer_id[:8]}...)")
        
        try:
            async for message in websocket:
                await self.handle_message(message, peer_id, websocket)
        except websockets.exceptions.ConnectionClosed:
            print(f"🔌 P2P: Connection closed from {peer_ip} (peer_id: {peer_id[:8]}...)")
        finally:
            # TIMPAL 10-BLOCK REWARD CUTOFF: Notify ledger when validator disconnects
            if peer_id in self.peer_validator_addresses:
                validator_addr = self.peer_validator_addresses[peer_id]
                print(f"🔴 P2P LIVENESS: Peer {peer_id[:8]}... disconnected, validator: {validator_addr[:20]}...")
                del self.peer_validator_addresses[peer_id]
                if self.on_validator_offline_callback:
                    print(f"🔴 P2P LIVENESS: Calling on_validator_offline_callback for {validator_addr[:20]}...")
                    try:
                        self.on_validator_offline_callback(validator_addr)
                    except Exception as e:
                        print(f"⚠️  Error in validator offline callback: {e}")
                else:
                    print(f"⚠️  P2P LIVENESS: No on_validator_offline_callback registered!")
            else:
                print(f"🔌 P2P LIVENESS: Peer {peer_id[:8]}... disconnected but no validator mapping found")
            
            if peer_id in self.peers:
                del self.peers[peer_id]
            if peer_id in self.peer_ips:
                ip = self.peer_ips[peer_id]
                del self.peer_ips[peer_id]
                self.ip_connection_count[ip] = max(0, self.ip_connection_count[ip] - 1)
            if peer_id in self.peer_public_keys:
                del self.peer_public_keys[peer_id]
    
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
                # Ban peer if security manager flagged them
                if not self.security_manager.is_peer_trusted(peer_id):
                    peer_ip = self.peer_ips.get(peer_id, "unknown")
                    self.banned_ips.add(peer_ip)
                    print(f"🚫 SECURITY: Banned peer {peer_id} (IP: {peer_ip}) for repeated auth failures")
                return  # REJECT the message
            
            # Authentication passed - record the verified message
            self.security_manager.record_verified_message(data, peer_id)
            
            # Store/update peer public key
            message_public_key = data.get("public_key", "")
            if message_public_key:
                self.peer_public_keys[peer_id] = message_public_key
            
            # Process authenticated message
            if message_type == "announce_node":
                device_id = data.get("device_id")
                reward_address = data.get("reward_address")
                
                # TIMPAL 10-BLOCK REWARD CUTOFF: Track peer-to-validator mapping
                if reward_address:
                    old_addr = self.peer_validator_addresses.get(peer_id)
                    if old_addr != reward_address:
                        self.peer_validator_addresses[peer_id] = reward_address
                        print(f"🔗 P2P LIVENESS: Stored peer-to-validator mapping: {peer_id[:8]}... -> {reward_address[:20]}...")
                        print(f"🔗 P2P LIVENESS: Current peer_validator_addresses count: {len(self.peer_validator_addresses)}")
                        # Notify ledger that this validator is online
                        if self.on_validator_online_callback:
                            try:
                                self.on_validator_online_callback(reward_address)
                            except Exception as e:
                                print(f"⚠️  Error in validator online callback: {e}")
                        else:
                            print(f"⚠️  P2P LIVENESS: No on_validator_online_callback registered!")
                
                if device_id and device_id not in self.known_device_ids:
                    self.known_device_ids.add(device_id)
                    print(f"🤝 P2P: Completed handshake with peer {peer_id[:8]}... (device: {device_id[:16]}...)")
                    
                    # CRITICAL FIX: Trigger sync callback AFTER handshake completes (not on connection)
                    # peer_id is a URL for outbound connections, UUID for inbound
                    is_outbound = peer_id.startswith("ws://") or peer_id.startswith("wss://")
                    if is_outbound and self.on_peer_connected_callback:
                        print(f"🔄 P2P: Handshake complete, triggering sync callback for {peer_id[:30]}...")
                        asyncio.create_task(self.on_peer_connected_callback(peer_id))
            
            elif message_type == "peer_list":
                peers = data.get("peers", [])
                for peer_addr in peers:
                    if peer_addr not in self.known_peer_addresses:
                        self.known_peer_addresses.add(peer_addr)
                        asyncio.create_task(self.connect_to_peer(peer_addr))
            
            elif message_type == "sync_request":
                print(f"📨 P2P: Received sync_request from peer {peer_id[:8]}...")
                if self.sync_handler:
                    # CRITICAL FIX: Pass websocket directly for reliable sync responses
                    await self.sync_handler(data, peer_id, websocket)
                else:
                    print(f"⚠️  P2P: No sync_handler registered, cannot respond to sync_request")
            
            elif message_type == "sync_ack":
                # DIAGNOSTIC: Log when genesis acknowledges our sync_request
                genesis_height = data.get("genesis_height", 0)
                blocks_available = data.get("blocks_available", 0)
                print(f"✅ SYNC ACK RECEIVED: Genesis has {genesis_height} blocks, {blocks_available} available to sync")
            
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
        
        # DEBUG: Log sync_request broadcasts
        if message_type == "sync_request":
            print(f"📤 P2P: Broadcasting sync_request to {len(self.peers)} inbound + {len(self.outbound_peers)} outbound peers")
        
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
        # CRITICAL FIX: Normalize and validate peer_address to prevent "nodename nor servname provided" errors
        # This error occurs when URLs have trailing whitespace, newlines, or invisible characters
        raw_address = peer_address
        peer_address = peer_address.strip() if peer_address else ""
        
        # Debug logging with repr() to show invisible characters
        if raw_address != peer_address:
            print(f"🔍 P2P DEBUG: Normalized peer_address: raw={raw_address!r} -> normalized={peer_address!r}")
        
        # Validate peer_address is not empty
        if not peer_address:
            print(f"❌ P2P ERROR: Empty peer_address after stripping (raw={raw_address!r}), skipping connect")
            return
        
        # Validate URL scheme
        from urllib.parse import urlparse
        try:
            parsed = urlparse(peer_address)
            if parsed.scheme not in ("ws", "wss"):
                print(f"❌ P2P ERROR: Invalid seed URL scheme (must be ws or wss): {peer_address!r}")
                return
            if not parsed.hostname:
                print(f"❌ P2P ERROR: Missing hostname in seed URL: {peer_address!r}")
                return
        except Exception as e:
            print(f"❌ P2P ERROR: Failed to parse seed URL {peer_address!r}: {e}")
            return
        
        if peer_address in self.outbound_peers:
            return
        
        if peer_address == f"ws://localhost:{self.port}":
            return
        
        print(f"🔄 P2P: Attempting to connect to {peer_address}...")
        
        try:
            # CRITICAL FIX: Increase ping timeout to prevent spurious disconnects
            # Default websockets ping_interval=20s, ping_timeout=20s is too aggressive
            # for blockchain nodes that may be busy processing blocks
            # Setting ping_timeout=60s gives more tolerance for temporary delays
            websocket = await asyncio.wait_for(
                websockets.connect(
                    peer_address,
                    ping_interval=30,  # Send ping every 30 seconds
                    ping_timeout=60,   # Wait 60 seconds for pong before closing
                ),
                timeout=5.0
            )
            self.outbound_peers[peer_address] = websocket
            
            # CRITICAL SECURITY FIX: Use security_manager for handshake authentication
            # This adds mandatory timestamp/nonce fields for P2P security
            announce_msg = {
                "type": "announce_node",
                "device_id": self.device_id
            }
            
            # TIMPAL 10-BLOCK REWARD CUTOFF: Include reward_address in handshake
            # This allows the receiving node to track peer-to-validator mapping
            # for liveness detection (offline_since_height tracking)
            if self.reward_address:
                announce_msg["reward_address"] = self.reward_address
            
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
            
            asyncio.create_task(self.handle_outbound_peer(peer_address, websocket))
            
        except asyncio.TimeoutError:
            print(f"⏱️  P2P: Connection to {peer_address} timed out after 5 seconds")
            print(f"   Possible causes: Node offline, firewall blocking, wrong IP/port")
        except ConnectionRefusedError:
            print(f"🚫 P2P: Connection to {peer_address} refused")
            print(f"   Possible causes: Node not running, port not open, firewall blocking")
        except OSError as e:
            # Use repr() to show invisible characters in peer_address for debugging
            print(f"⚠️  P2P: Network error connecting to {peer_address!r}: {e!r}")
            print(f"   Possible causes: DNS resolution failed, network unreachable, firewall")
            print(f"   If 'nodename nor servname provided': check for whitespace/invalid chars in URL")
        except ValueError as e:
            print(f"🔐 P2P: Authentication error connecting to {peer_address}: {e}")
        except Exception as e:
            print(f"❌ P2P: Unexpected error connecting to {peer_address}: {type(e).__name__}: {e}")
    
    async def handle_outbound_peer(self, peer_address: str, websocket):
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
            # TIMPAL 10-BLOCK REWARD CUTOFF: Notify ledger when validator disconnects
            if peer_address in self.peer_validator_addresses:
                validator_addr = self.peer_validator_addresses[peer_address]
                del self.peer_validator_addresses[peer_address]
                if self.on_validator_offline_callback:
                    try:
                        self.on_validator_offline_callback(validator_addr)
                    except Exception as e:
                        print(f"⚠️  Error in validator offline callback: {e}")
            
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
        print(f"🌐 P2P: Starting WebSocket server on 0.0.0.0:{self.port}...")
        # CRITICAL FIX: Match client ping settings to prevent spurious disconnects
        # Both client and server must use the same ping/pong settings
        
        try:
            async with websockets.serve(
                self.handle_client,
                "0.0.0.0",
                self.port,
                ping_interval=30,
                ping_timeout=60,
            ):
                print(f"✅ P2P: WebSocket server listening on port {self.port}")
                await asyncio.Future()
        except OSError as e:
            if getattr(e, "errno", None) in (48, 98):
                print(f"⚠️  P2P: Port {self.port} already in use. Server not started.")
                self.is_running = False
                return
            raise

    
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
