import hashlib
import json
import time
import config  # CRITICAL: Import at module level so sys.modules override works
from typing import List, Optional, Dict
from app.transaction import Transaction


class Block:
    def __init__(self, height: int, timestamp: float, transactions: List[Transaction], 
                 previous_hash: str, proposer: str, reward: int, 
                 reward_allocations: Optional[Dict[str, int]] = None, 
                 merkle_root: Optional[str] = None,
                 proposer_signature: Optional[str] = None,
                 block_hash: Optional[str] = None,
                 slot: Optional[int] = None,
                 rank: int = 0):
        self.height = height
        self.timestamp = timestamp
        self.transactions = transactions
        self.previous_hash = previous_hash
        self.proposer = proposer
        self.reward = reward
        self.reward_allocations = reward_allocations or {}
        self.merkle_root = merkle_root or self.calculate_merkle_root()
        self.proposer_signature = proposer_signature
        
        # TIME-SLICED SLOTS: For deterministic fallback without race conditions
        # slot: Which 3-second slot this block belongs to (monotonically increasing)
        # rank: Sub-window within slot (0=primary, 1=fallback1, 2=fallback2)
        self.slot = slot if slot is not None else height  # Default: slot = height for backward compat
        self.rank = rank  # 0 = primary proposer, 1+ = fallback proposers
        
        self.block_hash = block_hash or self.calculate_hash()
    
    def calculate_merkle_root(self) -> str:
        if not self.transactions:
            return hashlib.sha256(b"").hexdigest()
        
        # CRITICAL SECURITY: Recalculate transaction hashes to detect tampering
        # Don't use cached tx.tx_hash - always compute fresh from transaction data
        tx_hashes = [tx.calculate_hash() for tx in self.transactions]
        
        while len(tx_hashes) > 1:
            if len(tx_hashes) % 2 != 0:
                tx_hashes.append(tx_hashes[-1])
            
            new_hashes = []
            for i in range(0, len(tx_hashes), 2):
                combined = tx_hashes[i] + tx_hashes[i + 1]
                new_hash = hashlib.sha256(combined.encode()).hexdigest()
                new_hashes.append(new_hash)
            
            tx_hashes = new_hashes
        
        return tx_hashes[0]
    
    def calculate_hash(self) -> str:
        # CRITICAL SECURITY: Always recalculate merkle root to detect transaction tampering
        current_merkle = self.calculate_merkle_root()
        self.merkle_root = current_merkle  # Update cached value to stay in sync
        
        block_data = {
            "height": self.height,
            "timestamp": self.timestamp,
            "merkle_root": current_merkle,
            "previous_hash": self.previous_hash,
            "proposer": self.proposer,
            "reward": self.reward,
            "reward_allocations": self.reward_allocations,
            "slot": self.slot,
            "rank": self.rank
        }
        block_string = json.dumps(block_data, sort_keys=True)
        return hashlib.sha256(block_string.encode()).hexdigest()
    
    def sign_block(self, private_key: str):
        from ecdsa import SigningKey, SECP256k1
        sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
        message = self.block_hash.encode()
        signature = sk.sign(message)
        self.proposer_signature = signature.hex()
    
    def verify_proposer_signature(self, public_key: str) -> bool:
        if not self.proposer_signature:
            return False
        
        # CRITICAL SECURITY: Recalculate hash to ensure block wasn't modified after signing
        current_hash = self.calculate_hash()
        if current_hash != self.block_hash:
            return False  # Block was tampered with after signing!
        
        try:
            from ecdsa import VerifyingKey, SECP256k1
            vk = VerifyingKey.from_string(bytes.fromhex(public_key), curve=SECP256k1)
            message = self.block_hash.encode()
            signature = bytes.fromhex(self.proposer_signature)
            return vk.verify(signature, message)
        except:
            return False
    
    def to_dict(self):
        return {
            "height": self.height,
            "timestamp": self.timestamp,
            "transactions": [tx.to_dict() for tx in self.transactions],
            "previous_hash": self.previous_hash,
            "proposer": self.proposer,
            "reward": self.reward,
            "reward_allocations": self.reward_allocations,
            "merkle_root": self.merkle_root,
            "proposer_signature": self.proposer_signature,
            "block_hash": self.block_hash,
            "slot": self.slot,
            "rank": self.rank
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        transactions = [Transaction.from_dict(tx) for tx in data["transactions"]]
        return cls(
            height=data["height"],
            timestamp=data["timestamp"],
            transactions=transactions,
            previous_hash=data["previous_hash"],
            proposer=data["proposer"],
            reward=data["reward"],
            reward_allocations=data.get("reward_allocations", {}),
            merkle_root=data.get("merkle_root"),
            proposer_signature=data.get("proposer_signature"),
            block_hash=data.get("block_hash"),
            slot=data.get("slot"),
            rank=data.get("rank", 0)
        )
    
    @classmethod
    def create_genesis_block(cls, genesis_address: Optional[str] = None, genesis_public_key: Optional[str] = None):
        # config is imported at module level to respect sys.modules override
        from transaction import Transaction
        
        # SECURITY: ALWAYS use config.GENESIS_VALIDATORS to create identical genesis blocks
        # This ensures ALL nodes create the exact same genesis block hash for validation
        # Parameters genesis_address and genesis_public_key are IGNORED
        
        # Get genesis validator from config (first validator in GENESIS_VALIDATORS)
        if hasattr(config, 'GENESIS_VALIDATORS') and config.GENESIS_VALIDATORS:
            genesis_address = list(config.GENESIS_VALIDATORS.keys())[0]
            genesis_public_key = config.GENESIS_VALIDATORS[genesis_address]
            print(f"🔒 SECURITY: Using canonical genesis validator from config: {genesis_address[:20]}...")
        else:
            # Fallback for backward compatibility (no canonical genesis validation)
            print(f"⚠️  WARNING: No GENESIS_VALIDATORS in config, using provided parameters")
            if not genesis_address:
                genesis_address = "genesis"
        
        # Create genesis validator registration transaction
        transactions = []
        if genesis_public_key and genesis_address.startswith('tmpl'):
            # Create validator registration transaction for genesis validator
            # This ensures ALL nodes (including network syncing nodes) can extract the public key
            try:
                import hashlib
                genesis_reg_tx = Transaction(
                    sender=genesis_address,
                    recipient=genesis_address,
                    amount=0,
                    fee=0,
                    nonce=0,
                    public_key=genesis_public_key,
                    tx_type="validator_registration",
                    device_id="genesis",
                    timestamp=config.GENESIS_TIMESTAMP
                )
                # Sign the registration transaction (placeholder signature for genesis)
                genesis_reg_tx.signature = hashlib.sha256(b"genesis_validator_registration").hexdigest()
                genesis_reg_tx.tx_hash = hashlib.sha256(f"{genesis_address}{genesis_public_key}{config.GENESIS_TIMESTAMP}".encode()).hexdigest()
                transactions.append(genesis_reg_tx)
                print(f"✅ Genesis block includes validator registration for {genesis_address[:10]}...")
            except Exception as e:
                print(f"⚠️  Failed to create genesis registration tx: {e}")
        
        return cls(
            height=0,
            timestamp=config.GENESIS_TIMESTAMP,
            transactions=transactions,
            previous_hash="0" * 64,
            proposer=genesis_address if genesis_address.startswith('tmpl') else "genesis",
            reward=0,
            reward_allocations={}
        )
