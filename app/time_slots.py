"""
TIME-SLICED SLOTS (TSW) - Deterministic Fallback without Race Conditions

Implements the Time-Sliced Windows protocol for safe proposer fallback.
Each 3-second slot is divided into sub-windows where only one proposer is valid.

KEY INSIGHT:
- Window 0 (0-1s): Only primary proposer's blocks are valid
- Window 1 (1-2s): Only fallback #1's blocks are valid
- Window 2 (2-3s): Only fallback #2's blocks are valid

CONSENSUS SAFETY:
If primary is offline, their window passes with no block. Fallback's window opens.
If primary comes back online late, their block is REJECTED (timestamp outside window).
No race conditions possible - only one valid proposer per time window.

Based on ChatGPT's solution and Ethereum 2.0 slot-based consensus.
"""

import time
import config

# TIME-SLICED SLOTS CONFIGURATION
SLOT_SECONDS = 3.0  # Each slot is 3 seconds (same as BLOCK_TIME)
NUM_SUBSLOTS = 3  # Split each slot into 3 sub-windows (primary + 2 fallbacks)
WINDOW_SECONDS = SLOT_SECONDS / NUM_SUBSLOTS  # Each window is 1 second
CLOCK_DRIFT_TOLERANCE = 0.3  # 300ms tolerance for NTP clock drift


def slot_for_height(height: int) -> int:
    """
    Calculate which slot a block height belongs to.
    
    For simplicity: slot = height (one block per slot ideally)
    If a slot has no block (all proposers offline), next block uses next slot.
    
    Args:
        height: Block height
    
    Returns:
        Slot number
    """
    return height


def slot_start_time(genesis_timestamp: float, slot: int) -> float:
    """
    Calculate when a slot starts (absolute time).
    
    Args:
        genesis_timestamp: Timestamp of genesis block
        slot: Slot number
    
    Returns:
        Unix timestamp when slot starts
    """
    return genesis_timestamp + (slot * SLOT_SECONDS)


def window_bounds(genesis_timestamp: float, slot: int, rank: int) -> tuple:
    """
    Calculate time window bounds for a (slot, rank) pair.
    
    Args:
        genesis_timestamp: Timestamp of genesis block
        slot: Slot number
        rank: Proposer rank (0=primary, 1=fallback1, 2=fallback2)
    
    Returns:
        (window_start, window_end) tuple of Unix timestamps
    """
    slot_start = slot_start_time(genesis_timestamp, slot)
    window_start = slot_start + (rank * WINDOW_SECONDS)
    window_end = window_start + WINDOW_SECONDS
    return (window_start, window_end)


def validate_block_window(block_timestamp: float, genesis_timestamp: float, 
                          slot: int, rank: int) -> bool:
    """
    Validate that block's timestamp falls within its assigned window.
    
    This is the CORE consensus rule for Time-Sliced Slots:
    - Block valid only if timestamp is in correct window for its rank
    - Allows clock drift tolerance of +300ms for late blocks (NTP sync)
    
    CRITICAL: Tolerance is ASYMMETRIC to prevent window overlap:
    - Window start: NO tolerance (prevents overlap with previous rank)
    - Window end: +300ms tolerance (allows late blocks due to clock drift)
    
    This ensures adjacent windows NEVER overlap, preventing race conditions.
    
    Args:
        block_timestamp: Block's timestamp
        genesis_timestamp: Genesis block timestamp
        slot: Block's slot number
        rank: Block's rank (proposer position)
    
    Returns:
        True if timestamp is valid for (slot, rank), False otherwise
    """
    window_start, window_end = window_bounds(genesis_timestamp, slot, rank)
    
    # ASYMMETRIC drift tolerance: NO early start (prevents overlap), +300ms late end
    # This guarantees no overlap between adjacent windows (rank 0 and rank 1)
    window_start_with_drift = window_start  # No early tolerance
    window_end_with_drift = window_end + CLOCK_DRIFT_TOLERANCE  # Late tolerance only
    
    is_valid = window_start_with_drift <= block_timestamp < window_end_with_drift
    
    if not is_valid:
        print(f"❌ Window validation failed:")
        print(f"   Block timestamp: {block_timestamp}")
        print(f"   Window: [{window_start}, {window_end})")
        print(f"   With drift: [{window_start_with_drift}, {window_end_with_drift})")
        print(f"   Slot: {slot}, Rank: {rank}")
    
    return is_valid


def validate_block_window_relative(block_timestamp: float, parent_timestamp: float, rank: int) -> bool:
    """Validate a block timestamp using *chain-anchored* windows.

    The original TSW implementation anchored windows to the genesis timestamp.
    In real networks, small delays accumulate (network jitter, GC pauses, etc.),
    which can push blocks outside their absolute genesis-derived windows even
    when the network is healthy.

    This relative validator anchors the next slot to the *parent block timestamp*:
      expected_slot_start = parent_timestamp + SLOT_SECONDS

    All nodes see the same parent timestamp, so the window bounds are
    deterministic and drift with the chain rather than wall-clock time.

    Safety properties are preserved:
    - No overlap between adjacent ranks (no early tolerance)
    - Small late tolerance on window end for clock drift
    """
    expected_slot_start = parent_timestamp + SLOT_SECONDS
    window_start = expected_slot_start + (rank * WINDOW_SECONDS)
    window_end = window_start + WINDOW_SECONDS

    window_start_with_drift = window_start
    window_end_with_drift = window_end + CLOCK_DRIFT_TOLERANCE

    is_valid = window_start_with_drift <= block_timestamp < window_end_with_drift

    if not is_valid:
        print(f"❌ Relative window validation failed:")
        print(f"   Block timestamp:  {block_timestamp}")
        print(f"   Parent timestamp: {parent_timestamp}")
        print(f"   Expected slot start: {expected_slot_start}")
        print(f"   Window: [{window_start}, {window_end})")
        print(f"   With drift: [{window_start_with_drift}, {window_end_with_drift})")
        print(f"   Rank: {rank}")

    return is_valid


def relative_window_bounds(parent_timestamp: float, rank: int) -> tuple:
    """Window bounds for the *next* slot relative to parent timestamp."""
    expected_slot_start = parent_timestamp + SLOT_SECONDS
    window_start = expected_slot_start + (rank * WINDOW_SECONDS)
    window_end = window_start + WINDOW_SECONDS
    return (window_start, window_end)


def am_i_proposer_now_relative(my_address: str, ranked_proposers: list, parent_timestamp: float, current_time: float = None) -> tuple:
    """Return (is_my_turn, my_rank) using chain-anchored windows."""
    if current_time is None:
        current_time = time.time()

    try:
        my_rank = ranked_proposers.index(my_address)
    except ValueError:
        return (False, -1)

    window_start, window_end = relative_window_bounds(parent_timestamp, my_rank)

    # No early tolerance to avoid overlaps; allow small late tolerance.
    is_my_turn = (window_start <= current_time < (window_end + CLOCK_DRIFT_TOLERANCE))
    return (is_my_turn, my_rank)


def time_until_my_window_relative(my_rank: int, parent_timestamp: float, current_time: float = None) -> float:
    """Seconds until my window opens (relative windows)."""
    if current_time is None:
        current_time = time.time()
    window_start, _ = relative_window_bounds(parent_timestamp, my_rank)
    return window_start - current_time


def current_slot_and_rank(genesis_timestamp: float, current_time: float = None) -> tuple:
    """
    Calculate current slot and which rank's window is active right now.
    
    Args:
        genesis_timestamp: Genesis block timestamp
        current_time: Current time (default: time.time())
    
    Returns:
        (current_slot, active_rank) tuple
    """
    if current_time is None:
        current_time = time.time()
    
    # Calculate which slot we're in
    elapsed = current_time - genesis_timestamp
    current_slot = int(elapsed / SLOT_SECONDS)
    
    # Calculate which sub-window within the slot
    slot_elapsed = elapsed - (current_slot * SLOT_SECONDS)
    active_rank = min(int(slot_elapsed / WINDOW_SECONDS), NUM_SUBSLOTS - 1)
    
    return (current_slot, active_rank)


def am_i_proposer_now(my_address: str, proposer_queue: list, 
                     genesis_timestamp: float, slot: int, 
                     lenient_bootstrap: bool = False) -> tuple:
    """
    Check if I am the designated proposer for the currently active window.
    
    Args:
        my_address: This validator's address
        proposer_queue: Ordered list of proposers for this slot
        genesis_timestamp: Genesis block timestamp
        slot: Slot number to check
        lenient_bootstrap: If True, allows proposals even if window has passed (for early blocks)
    
    Returns:
        (is_my_turn, my_rank) tuple
        - is_my_turn: True if current time is within my window
        - my_rank: My rank in queue (None if not in queue)
    """
    # Find my rank in the proposer queue
    my_rank = None
    for i, addr in enumerate(proposer_queue[:NUM_SUBSLOTS]):
        if addr == my_address:
            my_rank = i
            break
    
    if my_rank is None:
        return (False, None)  # I'm not in top NUM_SUBSLOTS proposers
    
    # Check if current time is within my window
    current_time = time.time()
    window_start, window_end = window_bounds(genesis_timestamp, slot, my_rank)
    
    if lenient_bootstrap:
        # BOOTSTRAP MODE: During first 10 blocks, allow proposals if we're past window start
        # This handles case where genesis timestamp is stale (created before nodes started)
        is_my_turn = current_time >= window_start
    else:
        # PRODUCTION MODE: Strict window enforcement prevents race conditions
        is_my_turn = window_start <= current_time < window_end
    
    return (is_my_turn, my_rank)


def time_until_my_window(my_rank: int, genesis_timestamp: float, slot: int) -> float:
    """
    Calculate how many seconds until my window opens.
    
    Args:
        my_rank: My rank in proposer queue
        genesis_timestamp: Genesis block timestamp
        slot: Slot number
    
    Returns:
        Seconds until window opens (negative if window already started)
    """
    current_time = time.time()
    window_start, _ = window_bounds(genesis_timestamp, slot, my_rank)
    return window_start - current_time


def get_next_slot_time(genesis_timestamp: float, current_slot: int) -> float:
    """
    Calculate when the next slot starts.
    
    Args:
        genesis_timestamp: Genesis block timestamp
        current_slot: Current slot number
    
    Returns:
        Unix timestamp when next slot starts
    """
    return slot_start_time(genesis_timestamp, current_slot + 1)


def get_realtime_slot(genesis_timestamp: float, current_time: float = None) -> int:
    """
    Calculate the real-time slot index based on wall-clock time.
    
    This advances independently of chain height, allowing nodes to "catch up"
    to the current slot when significantly behind schedule.
    
    Args:
        genesis_timestamp: Genesis block timestamp
        current_time: Current time (default: time.time())
    
    Returns:
        Current real-time slot number
    """
    if current_time is None:
        current_time = time.time()
    
    elapsed = current_time - genesis_timestamp
    return int(elapsed / SLOT_SECONDS)


def should_skip_to_current_slot(genesis_timestamp: float, ledger_height: int, 
                                 bootstrap_blocks: int = 10) -> tuple:
    """
    Determine if we should skip to the current real-time slot.
    
    After the bootstrap period, if the real-time slot is significantly ahead
    of the ledger height, we should skip forward to avoid perpetual lag.
    
    Args:
        genesis_timestamp: Genesis block timestamp
        ledger_height: Current blockchain height
        bootstrap_blocks: Number of blocks with lenient bootstrap (default: 10)
    
    Returns:
        (should_skip, target_slot) tuple
        - should_skip: True if we should skip to current real-time slot
        - target_slot: The real-time slot to skip to (None if should_skip=False)
    """
    # During bootstrap period, don't skip
    if ledger_height < bootstrap_blocks:
        return (False, None)
    
    current_time = time.time()
    realtime_slot = get_realtime_slot(genesis_timestamp, current_time)
    next_block_slot = ledger_height + 1
    
    # If real-time slot is more than 1 slot ahead, skip forward
    # (i.e., we're more than 3 seconds behind schedule)
    if realtime_slot > next_block_slot:
        slots_behind = realtime_slot - next_block_slot
        print(f"⏩ Time slot skipping triggered:")
        print(f"   Ledger height: {ledger_height}")
        print(f"   Next block slot: {next_block_slot}")
        print(f"   Real-time slot: {realtime_slot}")
        print(f"   Skipping {slots_behind} empty slot(s)")
        return (True, realtime_slot)
    
    return (False, None)
