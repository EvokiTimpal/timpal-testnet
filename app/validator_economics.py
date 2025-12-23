"""
Validator Economics and Bonding System

Implements economic Sybil defense through validator deposits and slashing.

CRITICAL SECURITY: Makes Sybil attacks expensive by requiring economic stake.

Features:
1. Validator deposit requirement (100 TMPL) - WAIVED during grace period
2. Grace period: First ~6 months (5M blocks) NO deposit required for network growth
3. Slashing for misbehavior
4. Deposit withdrawal mechanism
5. Economic incentive alignment

Bootstrap Strategy:
- Blocks 0-5,000,000 (~6 months): NO deposit required - rapid network growth
- Blocks 5,000,001+: 100 TMPL deposit required - Sybil defense active
"""

import config
from typing import Dict, List, Optional, Tuple, Callable


class ValidatorEconomics:
    """
    Manages validator deposits, slashing, and economic incentives.
    
    Economic Sybil Defense:
    - Require 100 TMPL deposit to become validator
    - Slash deposit for misbehavior
    - Return deposit on proper deregistration
    
    This makes running 1,000 fake validators cost 100,000 TMPL,
    making Sybil attacks economically infeasible.
    """
    
    # Deposit required to become validator (in pals)
    VALIDATOR_DEPOSIT_PALS = 100 * config.PALS_PER_TMPL  # 100 TMPL
    
    # Slashing penalties (percentage of deposit)
    SLASH_DOUBLE_SIGNING = 100  # 100% - complete loss for double-signing blocks
    SLASH_INVALID_BLOCK = 50    # 50% - major penalty for invalid blocks
    # Note: No penalty for offline - validators simply don't earn rewards if offline
    
    # Minimum deposit to maintain validator status
    MIN_DEPOSIT_PALS = 50 * config.PALS_PER_TMPL  # 50 TMPL
    
    def __init__(self):
        # Track validator deposits: address -> deposit_amount
        self.deposits: Dict[str, int] = {}
        
        # Track slashing history: address -> total_slashed_amount
        self.slashed_amounts: Dict[str, int] = {}
        
        # Track pending slashed amount awaiting redistribution in next block
        # This accumulates slashed coins to be distributed to honest validators
        self.pending_redistribution: int = 0
        
        # Track withdrawal requests: address -> withdrawal_height
        # (validators must wait for withdrawal period before getting deposit back)
        self.withdrawal_requests: Dict[str, int] = {}
        
        # Track validator status: address -> status
        # Possible statuses: "active", "inactive_pending_deposit"
        self.validator_status: Dict[str, str] = {}
        
        # Track auto-lock preference: address -> bool
        # Default True = automatically lock deposit at block 5M if balance sufficient
        # False = validator must manually deposit (will be marked inactive if they don't)
        self.auto_lock_enabled: Dict[str, bool] = {}
        
        # Track scheduled deposits: address -> scheduled_at_height
        # Validators who pre-schedule deposits during blocks 4.75M-5M
        self.scheduled_deposits: Dict[str, int] = {}
        
        # Track transition completed flag
        self.transition_completed: bool = False
        
        # Withdrawal waiting period (blocks)
        # 100 blocks = ~5 minutes, enough to detect misbehavior
        self.WITHDRAWAL_PERIOD_BLOCKS = 100
        
        # Deposit transition window constants
        self.DEPOSIT_GRACE_PERIOD = config.DEPOSIT_GRACE_PERIOD_BLOCKS  # 5,000,000 blocks
        self.ADVANCE_DEPOSIT_WINDOW_START = 4_750_000  # ~8 days before transition
        self.TRANSITION_BLOCK = 5_000_000  # Exact block where deposits become required
    
    def is_in_grace_period(self, current_block_height: int) -> bool:
        """
        Check if network is in deposit grace period.
        
        During grace period (first ~6 months), NO deposit required.
        This allows network to bootstrap and grow organically.
        
        Args:
            current_block_height: Current blockchain height
        
        Returns:
            True if in grace period (no deposit required), False otherwise
        """
        return current_block_height < config.DEPOSIT_GRACE_PERIOD_BLOCKS
    
    def calculate_deposit_requirement(self, address: str, current_block_height: Optional[int] = None) -> int:
        """
        Calculate deposit required for validator registration.
        
        Args:
            address: Validator address
            current_block_height: Current blockchain height (for grace period check)
        
        Returns:
            Required deposit in pals (100 TMPL = 10,000,000,000 pals)
            Returns 0 during grace period (first ~6 months)
        """
        # During grace period, NO deposit required
        if current_block_height is not None and self.is_in_grace_period(current_block_height):
            return 0
        
        return self.VALIDATOR_DEPOSIT_PALS
    
    def can_register_validator(self, address: str, balance: int, current_block_height: Optional[int] = None) -> Tuple[bool, str]:
        """
        Check if address can register as validator.
        
        Args:
            address: Candidate validator address
            balance: Current balance in pals
            current_block_height: Current blockchain height (for grace period check)
        
        Returns:
            (allowed, reason) tuple
        """
        required_deposit = self.calculate_deposit_requirement(address, current_block_height)
        
        # During grace period, no deposit required
        if required_deposit == 0:
            if address in self.deposits:
                return (False, "Already registered as validator")
            return (True, "Can register as validator (grace period - no deposit required)")
        
        # After grace period, enforce deposit
        if balance < required_deposit:
            return (False, 
                   f"Insufficient balance: need {required_deposit / config.PALS_PER_TMPL} {config.SYMBOL}, "
                   f"have {balance / config.PALS_PER_TMPL} {config.SYMBOL}")
        
        if address in self.deposits:
            return (False, "Already registered as validator")
        
        return (True, "Can register as validator")
    
    def process_validator_deposit(self, address: str, amount: int, current_block_height: Optional[int] = None) -> Tuple[bool, str]:
        """
        Process validator deposit.
        
        Args:
            address: Validator address
            amount: Deposit amount in pals
            current_block_height: Current blockchain height (for grace period check)
        
        Returns:
            (success, message) tuple
        """
        required = self.calculate_deposit_requirement(address, current_block_height)
        
        # During grace period, no deposit required or recorded
        if required == 0:
            # Mark as active (no deposit needed during grace period)
            self.mark_active(address)
            print(f"🌱 Grace period registration: {address} (no deposit required)")
            return (True, "Registered during grace period (no deposit required)")
        
        # After grace period, require and record deposit
        if amount < required:
            return (False, f"Deposit too small: need {required} pals, got {amount} pals")
        
        # Record deposit and mark active
        self.deposits[address] = amount
        self.mark_active(address)
        
        print(f"💰 Validator deposit recorded: {address} deposited {amount / config.PALS_PER_TMPL} {config.SYMBOL}")
        return (True, f"Deposit recorded: {amount / config.PALS_PER_TMPL} {config.SYMBOL}")
    
    def slash_validator(self, address: str, reason: str, percentage: int) -> Tuple[bool, int]:
        """
        Slash validator deposit for misbehavior.
        
        Slashed coins are added to pending_redistribution pool,
        which will be distributed equally to all honest validators in the next block.
        
        Args:
            address: Validator address
            reason: Reason for slashing
            percentage: Percentage of deposit to slash (0-100)
        
        Returns:
            (success, slashed_amount) tuple
        """
        if address not in self.deposits:
            return (False, 0)
        
        current_deposit = self.deposits[address]
        slash_amount = (current_deposit * percentage) // 100
        
        # Apply slashing
        self.deposits[address] -= slash_amount
        
        # Track total slashed
        if address not in self.slashed_amounts:
            self.slashed_amounts[address] = 0
        self.slashed_amounts[address] += slash_amount
        
        # Add to pending redistribution pool
        self.pending_redistribution += slash_amount
        
        print(f"⚔️  SLASHING: {address} slashed {slash_amount / config.PALS_PER_TMPL} {config.SYMBOL} for {reason}")
        print(f"   Remaining deposit: {self.deposits[address] / config.PALS_PER_TMPL} {config.SYMBOL}")
        print(f"   Pending redistribution: {self.pending_redistribution / config.PALS_PER_TMPL} {config.SYMBOL} (will distribute to honest validators in next block)")
        
        # Check if deposit fell below minimum - mark inactive if so
        if self.deposits[address] < self.MIN_DEPOSIT_PALS:
            self.mark_inactive(address)
            print(f"⚠️  DEACTIVATED: {address} deposit below minimum - validator marked inactive")
        
        return (True, slash_amount)
    
    def slash_double_signing(self, address: str) -> Tuple[bool, int]:
        """Slash validator for double-signing blocks (100% penalty)."""
        return self.slash_validator(address, "double-signing blocks", self.SLASH_DOUBLE_SIGNING)
    
    def slash_invalid_block(self, address: str) -> Tuple[bool, int]:
        """Slash validator for proposing invalid block (50% penalty)."""
        return self.slash_validator(address, "proposing invalid block", self.SLASH_INVALID_BLOCK)
    
    def request_withdrawal(self, address: str, current_height: int) -> Tuple[bool, str]:
        """
        Request deposit withdrawal.
        
        Validator must wait WITHDRAWAL_PERIOD_BLOCKS before withdrawal is allowed.
        This allows network to detect and punish recent misbehavior.
        
        Args:
            address: Validator address
            current_height: Current blockchain height
        
        Returns:
            (success, message) tuple
        """
        if address not in self.deposits:
            return (False, "No deposit found")
        
        if address in self.withdrawal_requests:
            return (False, "Withdrawal already requested")
        
        # Record withdrawal request
        self.withdrawal_requests[address] = current_height
        
        withdrawal_height = current_height + self.WITHDRAWAL_PERIOD_BLOCKS
        
        print(f"📤 Withdrawal requested: {address} can withdraw at height {withdrawal_height}")
        return (True, f"Withdrawal allowed at height {withdrawal_height} (~{self.WITHDRAWAL_PERIOD_BLOCKS * config.BLOCK_TIME / 60} minutes)")
    
    def can_withdraw(self, address: str, current_height: int) -> Tuple[bool, str]:
        """
        Check if validator can withdraw deposit.
        
        Args:
            address: Validator address
            current_height: Current blockchain height
        
        Returns:
            (allowed, reason) tuple
        """
        if address not in self.deposits:
            return (False, "No deposit found")
        
        if address not in self.withdrawal_requests:
            return (False, "Must request withdrawal first")
        
        request_height = self.withdrawal_requests[address]
        withdrawal_height = request_height + self.WITHDRAWAL_PERIOD_BLOCKS
        
        if current_height < withdrawal_height:
            blocks_remaining = withdrawal_height - current_height
            time_remaining = blocks_remaining * config.BLOCK_TIME
            return (False, 
                   f"Withdrawal waiting period not complete. "
                   f"Need {blocks_remaining} more blocks (~{time_remaining / 60:.1f} minutes)")
        
        return (True, "Can withdraw deposit")
    
    def process_withdrawal(self, address: str, current_height: int) -> Tuple[bool, int, str]:
        """
        Process deposit withdrawal.
        
        Args:
            address: Validator address
            current_height: Current blockchain height
        
        Returns:
            (success, withdrawal_amount, message) tuple
        """
        # Check if withdrawal allowed
        allowed, reason = self.can_withdraw(address, current_height)
        if not allowed:
            return (False, 0, reason)
        
        # Get deposit amount
        withdrawal_amount = self.deposits[address]
        
        # Remove deposit
        del self.deposits[address]
        del self.withdrawal_requests[address]
        
        print(f"💸 Withdrawal processed: {address} withdrew {withdrawal_amount / config.PALS_PER_TMPL} {config.SYMBOL}")
        return (True, withdrawal_amount, f"Withdrew {withdrawal_amount / config.PALS_PER_TMPL} {config.SYMBOL}")
    
    def get_validator_deposit(self, address: str) -> int:
        """Get current deposit amount for validator."""
        return self.deposits.get(address, 0)
    
    def get_total_slashed(self, address: str) -> int:
        """Get total amount slashed from validator."""
        return self.slashed_amounts.get(address, 0)
    
    def get_redistribution_rewards(self, active_validators: List[str]) -> Dict[str, int]:
        """
        Calculate redistribution of slashed coins to honest validators.
        
        Distributes pending_redistribution equally among all active honest validators.
        Validators with deposits below minimum are excluded (already marked inactive).
        This should be called during block reward distribution.
        
        Args:
            active_validators: List of active validator addresses (pre-filtered)
        
        Returns:
            Dict mapping validator address to redistribution amount (in pals)
        """
        if self.pending_redistribution == 0 or len(active_validators) == 0:
            return {}
        
        # Filter to only validators with sufficient deposits (honest validators)
        # This automatically excludes validators who were recently slashed below minimum
        honest_validators = [v for v in active_validators 
                           if self.is_deposit_sufficient(v)]
        
        if len(honest_validators) == 0:
            print(f"⚠️  No honest validators to receive redistribution - slashed coins burned")
            self.pending_redistribution = 0
            return {}
        
        # Calculate equal share for each honest validator
        reward_per_validator = self.pending_redistribution // len(honest_validators)
        
        # Create rewards dict
        rewards = {validator: reward_per_validator for validator in honest_validators}
        
        print(f"💰 REDISTRIBUTION: {self.pending_redistribution / config.PALS_PER_TMPL} {config.SYMBOL} slashed coins")
        print(f"   Distributed to {len(honest_validators)} honest validators (excluding slashed validators)")
        print(f"   Each validator receives: {reward_per_validator / config.PALS_PER_TMPL} {config.SYMBOL}")
        
        # Reset pending redistribution
        self.pending_redistribution = 0
        
        return rewards
    
    def is_deposit_sufficient(self, address: str) -> bool:
        """Check if validator's deposit is above minimum threshold."""
        deposit = self.deposits.get(address, 0)
        return deposit >= self.MIN_DEPOSIT_PALS
    
    def has_full_deposit(self, address: str, current_block_height: Optional[int] = None) -> bool:
        """
        Check if validator has full required deposit.
        
        CRITICAL for fairness: After grace period, ALL validators (new and existing)
        must have 100 TMPL deposit to stay active.
        
        Args:
            address: Validator address
            current_block_height: Current blockchain height
        
        Returns:
            True if validator has full deposit or is in grace period, False otherwise
        """
        # During grace period, no deposit required
        if current_block_height is not None and self.is_in_grace_period(current_block_height):
            return True
        
        # After grace period, check deposit
        deposit = self.deposits.get(address, 0)
        return deposit >= self.VALIDATOR_DEPOSIT_PALS
    
    def get_validator_status(self, address: str) -> str:
        """
        Get validator status.
        
        Returns:
            "active" or "inactive_pending_deposit"
        """
        return self.validator_status.get(address, "active")
    
    def mark_inactive(self, address: str, reason: str = "insufficient_deposit") -> None:
        """
        Mark validator as inactive pending deposit.
        
        Args:
            address: Validator address
            reason: Reason for inactivation
        """
        self.validator_status[address] = "inactive_pending_deposit"
        print(f"⏸️  INACTIVE: {address} marked inactive ({reason})")
    
    def mark_active(self, address: str) -> None:
        """Mark validator as active."""
        self.validator_status[address] = "active"
        print(f"▶️  ACTIVE: {address} marked active")
    
    def enforce_required_deposit(self, address: str, balance: int, current_block_height: int) -> Tuple[bool, int, str]:
        """
        Enforce deposit requirement for validator.
        
        This is called at the grace period boundary to enforce deposits on ALL validators.
        
        Args:
            address: Validator address
            balance: Validator's current balance in pals
            current_block_height: Current blockchain height
        
        Returns:
            (success, amount_to_deduct, message) tuple
            - success=True if deposit is sufficient or was auto-locked
            - amount_to_deduct: Amount to deduct from balance (0 if already had deposit)
            - success=False if balance insufficient, validator marked inactive
        """
        # Check if already has full deposit
        if self.has_full_deposit(address, current_block_height):
            self.mark_active(address)
            return (True, 0, f"Validator {address} already has full deposit")
        
        # Try to auto-lock deposit from balance
        required = self.VALIDATOR_DEPOSIT_PALS
        
        if balance >= required:
            # Auto-lock deposit (record in economics, caller must deduct from ledger balance)
            self.deposits[address] = required
            self.mark_active(address)
            print(f"🔒 AUTO-LOCKED: {address} deposited {required / config.PALS_PER_TMPL} {config.SYMBOL} (grace period ended)")
            return (True, required, f"Auto-locked {required / config.PALS_PER_TMPL} {config.SYMBOL} deposit")
        else:
            # Insufficient balance - mark inactive
            self.mark_inactive(address, f"insufficient balance: need {required / config.PALS_PER_TMPL} {config.SYMBOL}, have {balance / config.PALS_PER_TMPL} {config.SYMBOL}")
            return (False, 0,
                   f"Validator {address} marked INACTIVE: "
                   f"need {required / config.PALS_PER_TMPL} {config.SYMBOL}, have {balance / config.PALS_PER_TMPL} {config.SYMBOL}")
    
    def is_validator_active(self, address: str, current_block_height: Optional[int] = None) -> bool:
        """
        Check if validator is active and eligible to propose/earn rewards.
        
        A validator is active if:
        1. During grace period: registered (no deposit check)
        2. After grace period: has full deposit AND status is "active"
        
        Args:
            address: Validator address
            current_block_height: Current blockchain height
        
        Returns:
            True if validator is active, False otherwise
        """
        # Check status
        if self.get_validator_status(address) == "inactive_pending_deposit":
            return False
        
        # Check deposit requirement
        return self.has_full_deposit(address, current_block_height)
    
    def get_economics_stats(self) -> dict:
        """Get statistics about validator economics."""
        total_deposits = sum(self.deposits.values())
        total_slashed = sum(self.slashed_amounts.values())
        active_validators = len([a for a in self.validator_status.keys() if self.get_validator_status(a) == "active"])
        inactive_validators = len([a for a in self.validator_status.keys() if self.get_validator_status(a) == "inactive_pending_deposit"])
        
        return {
            "active_validators": active_validators,
            "inactive_validators": inactive_validators,
            "total_deposits_pals": total_deposits,
            "total_deposits_tmpl": total_deposits / config.PALS_PER_TMPL,
            "total_slashed_pals": total_slashed,
            "total_slashed_tmpl": total_slashed / config.PALS_PER_TMPL,
            "pending_withdrawals": len(self.withdrawal_requests),
            "scheduled_deposits": len(self.scheduled_deposits),
            "transition_completed": self.transition_completed
        }
    
    def is_in_advance_deposit_window(self, current_block_height: int) -> bool:
        """
        Check if we're in the advance deposit window (blocks 4,750,000 - 4,999,999).
        During this window, validators can schedule deposits for automatic locking at block 5M.
        """
        return self.ADVANCE_DEPOSIT_WINDOW_START <= current_block_height < self.TRANSITION_BLOCK
    
    def schedule_deposit(self, address: str, current_block_height: int) -> Tuple[bool, str]:
        """
        Schedule validator deposit for automatic locking at block 5,000,000.
        Can only be called during advance deposit window (blocks 4.75M - 5M).
        
        Args:
            address: Validator address
            current_block_height: Current blockchain height
        
        Returns:
            (success, message) tuple
        """
        if not self.is_in_advance_deposit_window(current_block_height):
            if current_block_height < self.ADVANCE_DEPOSIT_WINDOW_START:
                blocks_until_window = self.ADVANCE_DEPOSIT_WINDOW_START - current_block_height
                return (False, f"Advance deposit window not yet open. Opens at block {self.ADVANCE_DEPOSIT_WINDOW_START} ({blocks_until_window} blocks from now)")
            else:
                return (False, f"Advance deposit window closed at block {self.TRANSITION_BLOCK}. Deposits are now enforced automatically.")
        
        if address in self.scheduled_deposits:
            return (False, f"Deposit already scheduled at block {self.scheduled_deposits[address]}")
        
        # Schedule deposit for automatic locking at transition block
        self.scheduled_deposits[address] = current_block_height
        print(f"📅 DEPOSIT SCHEDULED: {address} will auto-lock 100 {config.SYMBOL} at block {self.TRANSITION_BLOCK}")
        return (True, f"Deposit scheduled for automatic locking at block {self.TRANSITION_BLOCK}")
    
    def set_auto_lock(self, address: str, enabled: bool) -> Tuple[bool, str]:
        """
        Set auto-lock preference for validator.
        
        Default is True (auto-lock enabled). Validators can disable auto-lock if they want
        manual control over when their deposit is locked.
        
        Args:
            address: Validator address
            enabled: True to enable auto-lock, False to disable
        
        Returns:
            (success, message) tuple
        """
        self.auto_lock_enabled[address] = enabled
        status = "ENABLED" if enabled else "DISABLED"
        print(f"🔧 AUTO-LOCK {status}: {address}")
        return (True, f"Auto-lock {status.lower()}")
    
    def get_auto_lock_status(self, address: str) -> bool:
        """
        Get auto-lock preference for validator (default: True).
        
        Args:
            address: Validator address
        
        Returns:
            True if auto-lock enabled (default), False if disabled
        """
        return self.auto_lock_enabled.get(address, True)  # Default True
    
    def process_transition(self, registered_validators: List[str], get_balance_func: Callable[[str], int]) -> Dict[str, Tuple[bool, int, str]]:
        """
        Process the deposit requirement transition at block 5,000,000.
        
        This is the critical transition that ensures network continuity.
        Called exactly once at block 5,000,000.
        
        For each registered validator:
        1. If scheduled deposit OR (auto-lock enabled AND balance ≥ 100 TMPL):
           → Auto-lock 100 TMPL deposit, mark active
        2. If insufficient balance OR auto-lock disabled without scheduled deposit:
           → Mark inactive_pending_deposit
        
        Args:
            registered_validators: List of all registered validator addresses
            get_balance_func: Function that takes address and returns balance in pals
        
        Returns:
            Dict mapping address -> (success, amount_locked, message)
        """
        if self.transition_completed:
            return {}
        
        results = {}
        active_count = 0
        inactive_count = 0
        
        print(f"\n{'='*80}")
        print(f"🔄 DEPOSIT TRANSITION PROCESSING (Block {self.TRANSITION_BLOCK})")
        print(f"{'='*80}")
        
        for address in registered_validators:
            balance = get_balance_func(address)
            
            # Check if validator scheduled deposit or has auto-lock enabled
            scheduled = address in self.scheduled_deposits
            auto_lock = self.get_auto_lock_status(address)
            
            # Determine if we should lock deposit
            should_lock = scheduled or (auto_lock and balance >= self.VALIDATOR_DEPOSIT_PALS)
            
            if should_lock and balance >= self.VALIDATOR_DEPOSIT_PALS:
                # Auto-lock deposit
                self.deposits[address] = self.VALIDATOR_DEPOSIT_PALS
                self.mark_active(address)
                active_count += 1
                
                reason = "pre-scheduled" if scheduled else "auto-lock enabled"
                print(f"  ✅ {address}: ACTIVE ({reason}, {balance / config.PALS_PER_TMPL:.4f} {config.SYMBOL} → {self.VALIDATOR_DEPOSIT_PALS / config.PALS_PER_TMPL:.0f} {config.SYMBOL} locked)")
                results[address] = (True, self.VALIDATOR_DEPOSIT_PALS, f"Active ({reason})")
                
            else:
                # Mark inactive
                self.mark_inactive(address, "transition: insufficient balance or auto-lock disabled")
                inactive_count += 1
                
                reason = "auto-lock disabled" if not auto_lock else f"insufficient balance ({balance / config.PALS_PER_TMPL:.4f} {config.SYMBOL} < 100 {config.SYMBOL})"
                print(f"  ⚠️  {address}: INACTIVE ({reason})")
                results[address] = (False, 0, f"Inactive ({reason})")
        
        self.transition_completed = True
        
        print(f"{'='*80}")
        print(f"✅ TRANSITION COMPLETE: {active_count} active, {inactive_count} inactive")
        print(f"{'='*80}\n")
        
        return results
    
    def to_dict(self) -> dict:
        """Serialize economics state for persistence."""
        return {
            "deposits": self.deposits,
            "slashed_amounts": self.slashed_amounts,
            "withdrawal_requests": self.withdrawal_requests,
            "validator_status": self.validator_status,
            "auto_lock_enabled": self.auto_lock_enabled,
            "scheduled_deposits": self.scheduled_deposits,
            "transition_completed": self.transition_completed
        }
    
    def from_dict(self, data: dict):
        """Load economics state from persistence."""
        self.deposits = data.get("deposits", {})
        self.slashed_amounts = data.get("slashed_amounts", {})
        self.withdrawal_requests = data.get("withdrawal_requests", {})
        self.validator_status = data.get("validator_status", {})
        self.auto_lock_enabled = data.get("auto_lock_enabled", {})
        self.scheduled_deposits = data.get("scheduled_deposits", {})
        self.transition_completed = data.get("transition_completed", False)
