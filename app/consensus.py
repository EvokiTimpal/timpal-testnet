from typing import List, Optional
import time
import config


class Consensus:
    def __init__(self, active_nodes: List[str], validator_set: Optional[List[str]] = None, ledger=None):
        self.active_nodes = active_nodes
        self.last_seen = {}
        self.validator_set = validator_set or list(active_nodes)
        self.ledger = ledger  # Reference to ledger for Tendermint proposer selection
        
        for node in active_nodes:
            self.last_seen[node] = time.time()
    
    def set_validator_set(self, validator_set: List[str]):
        self.validator_set = list(validator_set)
        for validator in validator_set:
            if validator not in self.active_nodes:
                self.active_nodes.append(validator)
                self.last_seen[validator] = time.time()
    
    def add_node(self, node_id: str):
        if node_id not in self.active_nodes:
            self.active_nodes.append(node_id)
            self.last_seen[node_id] = time.time()
    
    def remove_node(self, node_id: str):
        if node_id in self.active_nodes:
            self.active_nodes.remove(node_id)
            if node_id in self.last_seen:
                del self.last_seen[node_id]
    
    def update_node_activity(self, node_id: str):
        self.last_seen[node_id] = time.time()
    
    def get_next_proposer(self, block_height: int, online_only: bool = True) -> Optional[str]:
        """
        Get the next block proposer using VRF-based selection with epoch committees.
        
        Args:
            block_height: The height of the block to propose
            online_only: DEPRECATED - liveness now determined by epoch attestations
        
        Returns:
            Address of the next proposer, or None if no validators available
        
        VRF ALGORITHM: Uses Verifiable Random Function to select proposer from
        epoch committee (1,000 validators). Scales to 100,000+ validators with
        O(1) verification complexity.
        """
        if self.ledger:
            # Use VRF-based proposer selection with epoch committees
            # Falls back to pool-based if VRF isn't ready (bootstrap period)
            selected = self.ledger.select_proposer_vrf_based(block_height)
            if selected:
                return selected
        
        # Fallback to simple round-robin (shouldn't happen in production)
        active_validators = sorted(self.validator_set)
        
        if len(active_validators) == 0:
            print(f"⚠️  No validators available at height {block_height}")
            return None
        
        proposer_index = block_height % len(active_validators)
        selected = active_validators[proposer_index]
        
        print(f"⚠️  Fallback round-robin [height {block_height}]: {selected[:20]}...")
        
        return selected
    
    def get_online_nodes(self, timeout: int = None) -> List[str]:
        """
        Deterministic online validator view.
        Prefer the ledger's callback (which can consult P2P), otherwise fall back
        to timestamp heuristics.
        """
        if timeout is None:
            timeout = config.BLOCK_TIME * 2  # ~6s

        # Primary: ask the Ledger (which may use attestations and P2P callback)
        if self.ledger and hasattr(self.ledger, "_online_validators_callback"):
            try:
                online_set = self.ledger._online_validators_callback()
                if isinstance(online_set, (set, list, tuple)):
                    # Keep only validators that are actually in current validator_set
                    return sorted([v for v in online_set if v in self.validator_set])
            except Exception:
                pass

        # Fallback: soft liveness by last_seen timestamps
        current_time = time.time()
        online = []
        for v in self.validator_set:
            if v in self.last_seen and (current_time - self.last_seen[v]) < timeout:
                online.append(v)
        return sorted(online)
    
    def get_active_nodes(self) -> List[str]:
        return sorted(list(self.validator_set))
    
    def is_valid_proposer(self, node_id: str, block_height: int) -> bool:
        """
        Check if a node is the valid proposer for a given block height.
        
        Uses online-only validator set by default to allow automatic
        skipping of offline validators.
        """
        expected_proposer = self.get_next_proposer(block_height, online_only=True)
        return node_id == expected_proposer
