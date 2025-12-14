"""
HEIGHT-BASED FINALITY SYSTEM FOR TIMPAL BLOCKCHAIN

Provides deterministic finality guarantees based on block depth.

KEY CONCEPTS:
- CONFIRMED: Transaction included in a block at height H (1-block confirmation, ~3s)
- FINAL: Block at height H when chain height >= H + FINALITY_DEPTH
- Once FINAL: Blocks <= H are immutable. Reorg that changes them is FORBIDDEN.

ARCHITECTURE:
- Finality is purely height-based (no voting, no quorum, no attestations)
- Block becomes FINAL when FINALITY_DEPTH blocks are built on top of it
- finalized_height is monotonically increasing
- NO REORG at heights <= finalized_height (hard invariant)

MAINNET-SAFE PARAMETERS:
- FINALITY_DEPTH = 2 (finalize block H when head >= H+2)
"""


# ============================================================
# CONSTANTS (MAINNET-SAFE, IMMUTABLE)
# ============================================================

# Finality depth: block at height H is FINAL when chain height >= H + FINALITY_DEPTH
# Using 2 provides a good balance between fast finality (~6s) and safety
FINALITY_DEPTH = 2


# ============================================================
# CANONICAL VALIDATOR ID (SINGLE SOURCE OF TRUTH)
# ============================================================

def canonical_validator_id(addr: str) -> str:
    """
    Canonical validator identity for VRF comparison.
    
    CRITICAL: VRF input, VRF winner, and local node identity MUST use the SAME
    canonical string. This function ensures consistent identity comparison.
    
    Args:
        addr: Validator address (may have mixed case, whitespace, etc.)
        
    Returns:
        Canonical lowercase, stripped address string
    """
    return (addr or "").strip().lower()


# ============================================================
# HEIGHT-BASED FINALITY FUNCTIONS
# ============================================================

def compute_finalized_height(chain_height: int) -> int:
    """
    Compute the finalized height based on current chain height.
    
    A block at height H is FINAL when chain_height >= H + FINALITY_DEPTH.
    Therefore: finalized_height = chain_height - FINALITY_DEPTH
    
    Args:
        chain_height: Current chain height (tip height)
        
    Returns:
        Finalized height (-1 if chain is too short for any finality)
    """
    if chain_height < FINALITY_DEPTH:
        return -1
    return chain_height - FINALITY_DEPTH


def is_height_finalized(height: int, chain_height: int) -> bool:
    """
    Check if a specific height is finalized.
    
    Args:
        height: Block height to check
        chain_height: Current chain height
        
    Returns:
        True if height <= finalized_height
    """
    finalized = compute_finalized_height(chain_height)
    return height <= finalized


def get_finality_info(height: int, chain_height: int) -> dict:
    """
    Get finality information for a block.
    
    Args:
        height: Block height to check
        chain_height: Current chain height
        
    Returns:
        Dict with finality status
    """
    finalized_height = compute_finalized_height(chain_height)
    is_finalized = height <= finalized_height
    blocks_until_final = max(0, (height + FINALITY_DEPTH) - chain_height)
    
    return {
        "height": height,
        "is_finalized": is_finalized,
        "finalized_height": finalized_height,
        "chain_height": chain_height,
        "finality_depth": FINALITY_DEPTH,
        "blocks_until_final": blocks_until_final
    }
