from typing import List, Dict, Optional
from app.transaction import Transaction
from collections import defaultdict
import time


class Mempool:
    # Transaction types allowed in mempool
    # - transfer: User money transfers (highest priority)
    # - validator_registration: New validator registrations (must be on-chain)
    # Excluded: heartbeats, attestations (handled by their own systems, would flood mempool)
    ALLOWED_TX_TYPES = {"transfer", "validator_registration"}
    
    def __init__(self, max_tx_per_address: int = 10, max_total_tx: int = 10000):
        self.pending_transactions: Dict[str, Transaction] = {}
        self.tx_count_by_address: Dict[str, int] = defaultdict(int)
        self.pending_nonces: Dict[str, int] = defaultdict(int)
        self.max_tx_per_address = max_tx_per_address
        self.max_total_tx = max_total_tx
    
    def add_transaction(self, tx: Transaction) -> bool:
        """
        Add transaction to mempool.
        
        CRITICAL: Only user money transfers and validator_registration belong in the mempool.
        Heartbeats and attestations are handled by their own systems and should not be
        queued as pending transactions (they would flood the mempool).
        """
        # Only accept allowed transaction types in mempool
        if tx.tx_type not in self.ALLOWED_TX_TYPES:
            return False  # Reject heartbeats, attestations, etc.
        
        if tx.tx_hash in self.pending_transactions:
            return False
        
        if len(self.pending_transactions) >= self.max_total_tx:
            return False
        
        if self.tx_count_by_address[tx.sender] >= self.max_tx_per_address:
            return False
        
        self.pending_transactions[tx.tx_hash] = tx
        self.tx_count_by_address[tx.sender] += 1
        self.pending_nonces[tx.sender] = max(self.pending_nonces[tx.sender], tx.nonce + 1)
        return True
    
    def get_pending_nonce(self, address: str) -> int:
        return self.pending_nonces.get(address, 0)
    
    def get_sender_pending_count(self, address: str) -> int:
        """Get count of pending transactions from a specific sender"""
        return self.tx_count_by_address.get(address, 0)
    
    def get_pending_transactions(self, limit: int = 700) -> List[Transaction]:
        transactions = list(self.pending_transactions.values())
        # PRIORITY QUEUE: Transfer transactions FIRST (user money), then others
        # This prevents heartbeat flooding from blocking user transfers
        transfer_txs = [tx for tx in transactions if tx.tx_type == "transfer"]
        other_txs = [tx for tx in transactions if tx.tx_type != "transfer"]
        
        # Sort each category by timestamp (oldest first within category)
        transfer_txs.sort(key=lambda tx: tx.timestamp)
        other_txs.sort(key=lambda tx: tx.timestamp)
        
        # Return transfers first, then others, up to limit
        prioritized = transfer_txs + other_txs
        return prioritized[:limit]
    
    def remove_transaction(self, tx_hash: str):
        if tx_hash in self.pending_transactions:
            tx = self.pending_transactions[tx_hash]
            del self.pending_transactions[tx_hash]
            self.tx_count_by_address[tx.sender] = max(0, self.tx_count_by_address[tx.sender] - 1)
            
            if self.tx_count_by_address[tx.sender] == 0:
                self.pending_nonces.pop(tx.sender, None)
    
    def remove_transactions(self, tx_hashes: List[str]):
        for tx_hash in tx_hashes:
            self.remove_transaction(tx_hash)
    
    def clear(self):
        self.pending_transactions.clear()
        self.tx_count_by_address.clear()
        self.pending_nonces.clear()
    
    def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        return self.pending_transactions.get(tx_hash)
    
    def size(self) -> int:
        return len(self.pending_transactions)
