import hashlib
import json
from typing import Optional
from ecdsa import SigningKey, VerifyingKey, SECP256k1, BadSignatureError
import config


class Transaction:
    def __init__(self, sender: str, recipient: str, amount: int, fee: int, timestamp: float, nonce: int = 0, signature: Optional[str] = None, tx_hash: Optional[str] = None, public_key: Optional[str] = None, tx_type: str = "transfer", device_id: Optional[str] = None, epoch_number: Optional[int] = None, timeout_vote_data: Optional[dict] = None, timeout_cert_data: Optional[dict] = None):
        self.sender = sender
        self.recipient = recipient
        self.amount = amount
        self.fee = fee
        self.timestamp = timestamp
        self.nonce = nonce
        self.signature = signature
        self.public_key = public_key
        self.tx_type = tx_type  # "transfer", "validator_registration", "validator_heartbeat", "epoch_attestation", "timeout_vote", "timeout_certificate"
        self.device_id = device_id  # Only used for validator_registration
        self.epoch_number = epoch_number  # Only used for epoch_attestation
        self.timeout_vote_data = timeout_vote_data  # Only used for timeout_vote
        self.timeout_cert_data = timeout_cert_data  # Only used for timeout_certificate
        self.tx_hash = tx_hash or self.calculate_hash()
    
    def calculate_hash(self) -> str:
        if self.tx_type == "validator_registration":
            # For validator registration: hash includes sender, public_key, device_id, timestamp, nonce
            tx_data = f"{self.tx_type}{self.sender}{self.public_key}{self.device_id}{self.timestamp}{self.nonce}"
        elif self.tx_type == "validator_heartbeat":
            # For heartbeat: hash includes sender, timestamp (heartbeats don't use nonce)
            tx_data = f"{self.tx_type}{self.sender}{self.timestamp}"
        elif self.tx_type == "epoch_attestation":
            # For epoch attestation: hash includes sender, epoch_number, timestamp
            # Epoch attestations are committee-only and don't use nonce
            tx_data = f"{self.tx_type}{self.sender}{self.epoch_number}{self.timestamp}"
        elif self.tx_type == "timeout_vote":
            # For timeout vote: hash includes height, round, proposer, voter
            # Use TimeoutVote data directly for hash consistency
            if self.timeout_vote_data:
                tx_data = f"{self.tx_type}{self.timeout_vote_data.get('height')}{self.timeout_vote_data.get('round')}{self.timeout_vote_data.get('proposer')}{self.timeout_vote_data.get('voter')}{self.timeout_vote_data.get('vote_timestamp')}"
            else:
                tx_data = f"{self.tx_type}{self.sender}{self.timestamp}"
        elif self.tx_type == "timeout_certificate":
            # For timeout certificate: hash includes height, round, proposer, aggregated votes
            if self.timeout_cert_data:
                # Create deterministic hash from certificate data
                votes_hashes = "".join(sorted([v.get('vote_signature', '') for v in self.timeout_cert_data.get('votes', [])]))
                tx_data = f"{self.tx_type}{self.timeout_cert_data.get('height')}{self.timeout_cert_data.get('round')}{self.timeout_cert_data.get('proposer')}{votes_hashes}{self.timeout_cert_data.get('aggregated_power')}"
            else:
                tx_data = f"{self.tx_type}{self.sender}{self.timestamp}"
        else:
            # For regular transfers
            tx_data = f"{self.tx_type}{self.sender}{self.recipient}{self.amount}{self.fee}{self.timestamp}{self.nonce}"
        return hashlib.sha256(tx_data.encode()).hexdigest()
    
    def sign(self, private_key_hex: str):
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        message = self.tx_hash.encode()
        signature = sk.sign(message)
        self.signature = signature.hex()
    
    def verify(self) -> bool:
        if not self.signature or not self.public_key:
            return False
        try:
            expected_address = self._public_key_to_address(self.public_key)
            if expected_address != self.sender:
                return False
            
            vk = VerifyingKey.from_string(bytes.fromhex(self.public_key), curve=SECP256k1)
            message = self.tx_hash.encode()
            signature = bytes.fromhex(self.signature)
            vk.verify(signature, message)
            return True
        except (BadSignatureError, ValueError):
            return False
    
    @staticmethod
    def _public_key_to_address(public_key_hex: str) -> str:
        hash1 = hashlib.sha256(bytes.fromhex(public_key_hex)).digest()
        hash2 = hashlib.sha256(hash1).digest()
        return f"tmpl{hash2.hex()[:44]}"
    
    def is_valid(self, balances: dict, nonces: dict = None) -> bool:
        # Validator registration transactions have different validation rules
        if self.tx_type == "validator_registration":
            return self.is_valid_validator_registration(balances, nonces)
        
        # Validator heartbeat transactions have minimal validation
        if self.tx_type == "validator_heartbeat":
            return self.is_valid_validator_heartbeat(balances, nonces)
        
        # Epoch attestation transactions have minimal validation
        if self.tx_type == "epoch_attestation":
            return self.is_valid_epoch_attestation(balances, nonces)
        
        # Timeout vote transactions
        if self.tx_type == "timeout_vote":
            return self.is_valid_timeout_vote(balances, nonces)
        
        # Timeout certificate transactions
        if self.tx_type == "timeout_certificate":
            return self.is_valid_timeout_certificate(balances, nonces)
        
        # Regular transfer validation
        if self.amount <= 0:
            return False
        
        # CRITICAL SECURITY: Prevent integer overflow and excessive amounts
        if self.amount > config.MAX_TRANSACTION_AMOUNT:
            return False
        if self.amount > 2**63 - 1:  # Prevent integer overflow
            return False
        
        if self.fee != config.FEE:
            return False
        if self.sender == self.recipient:
            return False
        
        sender_balance = balances.get(self.sender, 0)
        if sender_balance < self.amount + self.fee:
            return False
        
        if nonces is not None:
            expected_nonce = nonces.get(self.sender, 0)
            if self.nonce != expected_nonce:
                return False
        
        return True
    
    def is_valid_validator_registration(self, balances: dict, nonces: dict = None) -> bool:
        """Validate a validator registration transaction"""
        # Must have public key and device_id
        if not self.public_key or not self.device_id:
            return False
        
        # Sender must match derived address from public key
        expected_address = self._public_key_to_address(self.public_key)
        if self.sender != expected_address:
            return False
        
        # Public key must be valid format (128 hex chars)
        if len(self.public_key) != 128:
            return False
        
        try:
            int(self.public_key, 16)  # Verify valid hex
        except ValueError:
            return False
        
        # Device ID must be valid SHA256 hash (64 hex chars) for Sybil resistance
        # Legacy support: also accept old wallet address format ("tmpl" + 44 hex = 48 chars)
        is_valid_device_id = False
        
        # Option 1: SHA256 hash format (canonical - used by all new nodes)
        if len(self.device_id) == 64:
            try:
                int(self.device_id, 16)  # Verify it's valid hex
                is_valid_device_id = True
            except ValueError:
                pass
        
        # Option 2: Legacy wallet address format (backward compatibility)
        elif len(self.device_id) == 48 and self.device_id.startswith("tmpl"):
            try:
                int(self.device_id[4:], 16)  # Verify the part after "tmpl" is valid hex
                is_valid_device_id = True
            except ValueError:
                pass
        
        if not is_valid_device_id:
            return False
        
        # Nonce validation (if provided)
        if nonces is not None:
            expected_nonce = nonces.get(self.sender, 0)
            if self.nonce != expected_nonce:
                return False
        
        # No balance requirement for validator registration (it's free to join!)
        return True
    
    def is_valid_validator_heartbeat(self, balances: dict, nonces: dict = None) -> bool:
        """Validate a validator heartbeat transaction"""
        # Heartbeat must be from a registered validator (we'll check this in ledger)
        # Amount and fee must be 0 (heartbeats are free)
        if self.amount != 0 or self.fee != 0:
            return False
        
        # NOTE: We do NOT validate timestamp against current time because
        # historical blocks from the past would fail validation during sync.
        # The timestamp was already validated when the block was first created.
        
        return True
    
    def is_valid_epoch_attestation(self, balances: dict, nonces: dict = None) -> bool:
        """Validate an epoch attestation transaction"""
        # Attestation must be from a registered validator (checked in ledger)
        # Amount and fee must be 0 (attestations are free)
        if self.amount != 0 or self.fee != 0:
            return False
        
        # Must have epoch_number
        if self.epoch_number is None:
            return False
        
        # Epoch number must be non-negative
        if self.epoch_number < 0:
            return False
        
        return True
    
    def is_valid_timeout_vote(self, balances: dict, nonces: dict = None) -> bool:
        """Validate a timeout vote transaction"""
        # Amount and fee must be 0 (timeout votes are free - consensus mechanism)
        if self.amount != 0 or self.fee != 0:
            return False
        
        # Must have timeout_vote_data
        if not self.timeout_vote_data:
            return False
        
        # Required fields in timeout_vote_data
        required_fields = ['height', 'round', 'proposer', 'voter', 'vote_timestamp', 'voter_public_key', 'vote_signature']
        for field in required_fields:
            if field not in self.timeout_vote_data:
                return False
        
        # Height and round must be non-negative
        if self.timeout_vote_data['height'] < 0 or self.timeout_vote_data['round'] < 0:
            return False
        
        # Voter must match sender
        if self.timeout_vote_data['voter'] != self.sender:
            return False
        
        return True
    
    def is_valid_timeout_certificate(self, balances: dict, nonces: dict = None) -> bool:
        """Validate a timeout certificate transaction"""
        # Amount and fee must be 0 (timeout certificates are free - consensus mechanism)
        if self.amount != 0 or self.fee != 0:
            return False
        
        # Must have timeout_cert_data
        if not self.timeout_cert_data:
            return False
        
        # Required fields in timeout_cert_data
        required_fields = ['height', 'round', 'proposer', 'votes', 'aggregated_power', 'issuer']
        for field in required_fields:
            if field not in self.timeout_cert_data:
                return False
        
        # Height and round must be non-negative
        if self.timeout_cert_data['height'] < 0 or self.timeout_cert_data['round'] < 0:
            return False
        
        # Must have at least one vote
        if not self.timeout_cert_data['votes'] or len(self.timeout_cert_data['votes']) == 0:
            return False
        
        # Aggregated power must be positive
        if self.timeout_cert_data['aggregated_power'] <= 0:
            return False
        
        # Issuer must match sender
        if self.timeout_cert_data['issuer'] != self.sender:
            return False
        
        # NOTE: Deeper validation (2/3 quorum, signature verification) happens in ledger.add_block()
        # This is just basic structural validation
        
        return True
    
    def to_dict(self):
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "fee": self.fee,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "signature": self.signature,
            "public_key": self.public_key,
            "tx_hash": self.tx_hash,
            "tx_type": self.tx_type,
            "device_id": self.device_id,
            "epoch_number": self.epoch_number,
            "timeout_vote_data": self.timeout_vote_data,
            "timeout_cert_data": self.timeout_cert_data
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            sender=data["sender"],
            recipient=data.get("recipient", ""),  # Optional for validator_registration
            amount=data.get("amount", 0),  # Optional for validator_registration
            fee=data.get("fee", 0),  # Optional for validator_registration
            timestamp=data["timestamp"],
            nonce=data.get("nonce", 0),
            signature=data.get("signature"),
            public_key=data.get("public_key"),
            tx_hash=data.get("tx_hash"),
            tx_type=data.get("tx_type", "transfer"),  # Default to transfer for backward compat
            device_id=data.get("device_id"),
            epoch_number=data.get("epoch_number"),
            timeout_vote_data=data.get("timeout_vote_data"),
            timeout_cert_data=data.get("timeout_cert_data")
        )
    
    @staticmethod
    def create_validator_registration(sender: str, public_key: str, device_id: str, timestamp: float, nonce: int = 0) -> 'Transaction':
        """
        Create a validator registration transaction.
        
        Args:
            sender: Validator address (must match public_key)
            public_key: ECDSA public key (128 hex chars)
            device_id: Device fingerprint hash (64 hex chars)
            timestamp: Transaction timestamp
            nonce: Transaction nonce
        
        Returns:
            Transaction object of type validator_registration
        """
        return Transaction(
            sender=sender,
            recipient="",  # No recipient for validator registration
            amount=0,  # No amount transfer
            fee=0,  # Free to register!
            timestamp=timestamp,
            nonce=nonce,
            tx_type="validator_registration",
            public_key=public_key,
            device_id=device_id
        )
    
    @staticmethod
    def create_validator_heartbeat(sender: str, timestamp: float) -> 'Transaction':
        """
        Create a validator heartbeat transaction.
        
        Args:
            sender: Validator address
            timestamp: Transaction timestamp
        
        Returns:
            Transaction object of type validator_heartbeat
        """
        return Transaction(
            sender=sender,
            recipient="",  # No recipient for heartbeat
            amount=0,  # No amount transfer
            fee=0,  # Free heartbeat!
            timestamp=timestamp,
            nonce=0,  # Heartbeats don't use nonce
            tx_type="validator_heartbeat"
        )
    
    @staticmethod
    def create_epoch_attestation(sender: str, epoch_number: int, timestamp: float) -> 'Transaction':
        """
        Create an epoch attestation transaction for scalable liveness tracking.
        
        Only validators in the rotating committee for this epoch should create attestations.
        Replaces continuous heartbeat transactions with periodic epoch-based attestations.
        
        Args:
            sender: Validator address
            epoch_number: Epoch number to attest for
            timestamp: Transaction timestamp
        
        Returns:
            Transaction object of type epoch_attestation
        """
        return Transaction(
            sender=sender,
            recipient="",  # No recipient for attestation
            amount=0,  # No amount transfer
            fee=0,  # Free attestation!
            timestamp=timestamp,
            nonce=0,  # Attestations don't use nonce
            tx_type="epoch_attestation",
            epoch_number=epoch_number
        )
