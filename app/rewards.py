import config
from typing import List, Tuple, Dict


class RewardCalculator:
    def __init__(self, ledger=None):
        """
        Initialize reward calculator.
        
        Args:
            ledger: Optional, kept for backward compatibility with old tests
                   (not used in current implementation)
        """
        self.fee_pool = 0
        self.ledger = ledger  # Kept for backward compatibility
    
    def calculate_reward(self, active_nodes: List[str], collected_fees: int, total_emitted_pals: int) -> Tuple[Dict[str, int], int, int]:
        """
        Calculate block rewards and transaction fee distribution.
        
        Args:
            active_nodes: List of active validator addresses
            collected_fees: Transaction fees collected in this block (in pals)
            total_emitted_pals: Total coins emitted so far (for emission cap calculation)
        
        Returns:
            Tuple of (reward_allocations, total_reward_pals, block_reward_pals)
            - reward_allocations: Dict mapping validator addresses to reward amounts
            - total_reward_pals: Total rewards distributed (block reward + fees)
            - block_reward_pals: NEW coins minted (for total supply tracking)
        
        CRITICAL: block_reward_pals should be added to total_emitted_pals
                  Transaction fees should NOT be added (already in circulation)
        """
        remaining_emission = config.MAX_SUPPLY_PALS - total_emitted_pals
        
        if remaining_emission > 0:
            block_reward_pals = min(config.EMISSION_PER_BLOCK_PALS, remaining_emission)
        else:
            block_reward_pals = 0
        
        total_reward_pals = block_reward_pals + collected_fees
        
        num_nodes = len(active_nodes)
        if num_nodes == 0:
            return {}, total_reward_pals, block_reward_pals
        
        sorted_nodes = sorted(active_nodes)
        
        reward_per_node_pals = total_reward_pals // num_nodes
        remainder = total_reward_pals % num_nodes
        
        rewards = {}
        for i, node in enumerate(sorted_nodes):
            rewards[node] = reward_per_node_pals
            if i < remainder:
                rewards[node] += 1
        
        return rewards, total_reward_pals, block_reward_pals
    
    def calculate_block_reward(self, block_height: int) -> int:
        """
        LEGACY API: Calculate block reward (kept for backward compatibility with old tests).
        
        Args:
            block_height: Block height (unused, kept for API compatibility)
        
        Returns:
            Block reward in pals
        
        Note: This method uses self.ledger.total_emitted_pals if ledger was provided.
              For new code, use calculate_reward() instead.
        """
        if self.ledger:
            total_emitted = self.ledger.total_emitted_pals
        else:
            total_emitted = 0
        
        remaining_emission = config.MAX_SUPPLY_PALS - total_emitted
        
        if remaining_emission > 0:
            block_reward_pals = min(config.EMISSION_PER_BLOCK_PALS, remaining_emission)
        else:
            block_reward_pals = 0
        
        return block_reward_pals
    
    def reset_pool(self):
        self.fee_pool = 0
