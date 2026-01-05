#!/usr/bin/env python3
"""
TIMPAL Validator Management CLI

This tool allows validators to:
1. Set auto-lock preferences (before block 5,000,000)
2. Schedule deposits in advance (blocks 4,750,000-5,000,000)
3. Request withdrawal (exit validation)
4. Process withdrawal (get coins back after waiting period)
5. Check validator status and deposit information
"""

import sys
import os

from app.ledger import Ledger
from app.wallet import Wallet
from app.seed_wallet import SeedWallet
from app.wallet_loader import detect_wallet_version, load_wallet_unified
from app import config_testnet as config

def print_header(title):
    """Print formatted header"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def load_wallet():
    """Load wallet and return address"""
    # Check for v3 multi-vault wallet first, then v2, then v1
    wallet_file = None
    if os.path.exists("wallets.json"):
        wallet_file = "wallets.json"
    elif os.path.exists("wallet_v2.json"):
        wallet_file = "wallet_v2.json"
    elif os.path.exists("wallet.json"):
        wallet_file = "wallet.json"
    else:
        print(f"❌ Wallet not found. Please create a wallet first.")
        print(f"   Run: python3 wallet_cli_v2.py")
        return None

    pin = input(f"Enter your wallet PIN/password: ")
    try:
        # Unified loader handles v1/v2/v3; for address we don't need private key output.
        address, _pub, _priv = load_wallet_unified(wallet_file, password=pin)
        return address
    except Exception as e:
        print(f"❌ Failed to load wallet: {e}")
        return None

def show_validator_status(ledger, address):
    """Display comprehensive validator status"""
    print_header("VALIDATOR STATUS")
    
    # Basic info
    is_registered = ledger.is_validator_registered(address)
    print(f"Address: {address}")
    print(f"Registered: {'✅ Yes' if is_registered else '❌ No'}")
    
    if not is_registered:
        print("\n💡 You need to register as a validator first.")
        return
    
    # Balance
    balance = ledger.get_balance(address) / config.PALS_PER_TMPL
    print(f"Balance: {balance:.4f} TMPL")
    
    # Deposit info
    deposit_amount = ledger.validator_economics.get_validator_deposit(address) / config.PALS_PER_TMPL
    status = ledger.validator_economics.get_validator_status(address)
    auto_lock = ledger.validator_economics.get_auto_lock_status(address)
    
    print(f"\nDeposit Amount: {deposit_amount:.4f} TMPL")
    print(f"Status: {status}")
    print(f"Auto-lock Enabled: {auto_lock}")
    
    # Scheduled deposit
    if address in ledger.validator_economics.scheduled_deposits:
        scheduled_height = ledger.validator_economics.scheduled_deposits[address]
        print(f"✅ Deposit scheduled for block: {scheduled_height:,}")
    
    # Withdrawal status
    if address in ledger.validator_economics.withdrawal_requests:
        request_height = ledger.validator_economics.withdrawal_requests[address]
        withdrawal_height = request_height + ledger.validator_economics.WITHDRAWAL_PERIOD_BLOCKS
        current_height = len(ledger.blocks)
        
        print(f"\n📤 Withdrawal Status:")
        print(f"   Requested at block: {request_height:,}")
        print(f"   Can withdraw at block: {withdrawal_height:,}")
        
        if current_height >= withdrawal_height:
            print(f"   ✅ Ready to withdraw!")
        else:
            blocks_remaining = withdrawal_height - current_height
            time_remaining = blocks_remaining * config.BLOCK_TIME / 60
            print(f"   ⏳ {blocks_remaining} blocks remaining (~{time_remaining:.1f} minutes)")
    
    # Current blockchain height
    current_height = len(ledger.blocks)
    print(f"\nCurrent block height: {current_height:,}")
    
    # Transition status
    if current_height < config.DEPOSIT_GRACE_PERIOD_BLOCKS:
        blocks_to_transition = config.DEPOSIT_GRACE_PERIOD_BLOCKS - current_height
        print(f"Deposit requirement in: {blocks_to_transition:,} blocks")
        
        if current_height >= (config.DEPOSIT_GRACE_PERIOD_BLOCKS - 250000):
            print(f"⚠️  ADVANCE WINDOW OPEN - You can schedule your deposit now!")

def set_auto_lock_preference(ledger, address, enable):
    """Enable or disable auto-lock for deposit transition"""
    current_height = len(ledger.blocks)
    
    # Check if before transition
    if current_height >= config.DEPOSIT_GRACE_PERIOD_BLOCKS:
        print(f"❌ Too late! Transition already occurred at block {config.DEPOSIT_GRACE_PERIOD_BLOCKS:,}")
        return
    
    # Set preference
    ledger.validator_economics.set_auto_lock(address, enable)
    ledger.save_state()
    
    if enable:
        print(f"✅ Auto-lock ENABLED")
        print(f"   At block 5,000,000, your deposit will be automatically locked if you have ≥100 TMPL")
    else:
        print(f"🔧 Auto-lock DISABLED")
        print(f"   You will need to manually deposit 100 TMPL at/after block 5,000,000")
        print(f"   ⚠️  You will become INACTIVE until you deposit manually!")

def schedule_deposit_advance(ledger, address):
    """Schedule deposit to be locked at block 5,000,000"""
    current_height = len(ledger.blocks)
    
    # Check if in advance window
    advance_window_start = config.DEPOSIT_GRACE_PERIOD_BLOCKS - 250000
    if current_height < advance_window_start:
        print(f"❌ Too early! Advance window opens at block {advance_window_start:,}")
        print(f"   Current block: {current_height:,}")
        print(f"   Wait {advance_window_start - current_height:,} more blocks")
        return
    
    if current_height >= config.DEPOSIT_GRACE_PERIOD_BLOCKS:
        print(f"❌ Too late! Transition already occurred at block {config.DEPOSIT_GRACE_PERIOD_BLOCKS:,}")
        return
    
    # Check balance
    balance = ledger.get_balance(address) / config.PALS_PER_TMPL
    if balance < 100:
        print(f"❌ Insufficient balance: {balance:.4f} TMPL (need 100 TMPL)")
        return
    
    # Schedule deposit
    success = ledger.validator_economics.schedule_deposit(address, config.DEPOSIT_GRACE_PERIOD_BLOCKS)
    if success:
        ledger.save_state()
        print(f"✅ Deposit scheduled for block 5,000,000")
        print(f"   100 TMPL will be locked automatically at the transition")
        print(f"   Your balance: {balance:.4f} TMPL → {balance - 100:.4f} TMPL after transition")
    else:
        print(f"❌ Failed to schedule deposit (already scheduled?)")

def request_withdrawal_exit(ledger, address):
    """Request to exit validation and withdraw deposit"""
    current_height = len(ledger.blocks)
    
    # Check if has deposit
    deposit = ledger.validator_economics.get_validator_deposit(address)
    if deposit == 0:
        print(f"❌ No deposit found. Nothing to withdraw.")
        return
    
    deposit_tmpl = deposit / config.PALS_PER_TMPL
    
    print(f"📤 Requesting withdrawal...")
    print(f"   Deposit amount: {deposit_tmpl:.4f} TMPL")
    print(f"   Waiting period: {ledger.validator_economics.WITHDRAWAL_PERIOD_BLOCKS} blocks (~5 minutes)")
    
    confirm = input(f"\nConfirm withdrawal request? (yes/no): ")
    if confirm.lower() != 'yes':
        print(f"❌ Withdrawal cancelled")
        return
    
    # Request withdrawal
    success, message = ledger.validator_economics.request_withdrawal(address, current_height)
    if success:
        ledger.save_state()
        print(f"\n✅ Withdrawal requested successfully!")
        print(f"   {message}")
        print(f"\n⚠️  You are now INACTIVE and will not receive block rewards")
        print(f"   Come back after the waiting period to process your withdrawal")
    else:
        print(f"\n❌ Withdrawal request failed: {message}")

def process_withdrawal_complete(ledger, address):
    """Complete withdrawal and get coins back"""
    current_height = len(ledger.blocks)
    
    # Check if can withdraw
    can_withdraw, message = ledger.validator_economics.can_withdraw(address, current_height)
    
    if not can_withdraw:
        print(f"❌ Cannot withdraw yet: {message}")
        return
    
    # Get deposit amount before withdrawal
    deposit = ledger.validator_economics.get_validator_deposit(address) / config.PALS_PER_TMPL
    
    print(f"💰 Processing withdrawal...")
    print(f"   Deposit amount: {deposit:.4f} TMPL")
    
    confirm = input(f"\nConfirm final withdrawal? (yes/no): ")
    if confirm.lower() != 'yes':
        print(f"❌ Withdrawal cancelled")
        return
    
    # Process withdrawal
    success, amount, result_message = ledger.validator_economics.process_withdrawal(address, current_height)
    if success:
        # Return deposit to balance
        current_balance = ledger.get_balance(address)
        ledger.balances[address] = current_balance + amount
        ledger.save_state()
        
        amount_tmpl = amount / config.PALS_PER_TMPL
        new_balance = ledger.get_balance(address) / config.PALS_PER_TMPL
        
        print(f"\n✅ Withdrawal complete!")
        print(f"   Returned: {amount_tmpl:.4f} TMPL")
        print(f"   New balance: {new_balance:.4f} TMPL")
        print(f"\n🎉 You have successfully exited validation!")
    else:
        print(f"\n❌ Withdrawal failed: {result_message}")

def main_menu():
    """Display main menu and handle user input"""
    print_header("TIMPAL VALIDATOR MANAGEMENT")
    
    # Load ledger
    ledger = Ledger(use_production_storage=True)
    
    # Load wallet
    address = load_wallet()
    if not address:
        return 1
    
    while True:
        print("\n" + "─"*70)
        print("MENU:")
        print("  1. Check validator status")
        print("  2. Enable auto-lock (deposit will lock automatically at block 5M)")
        print("  3. Disable auto-lock (manual control - you'll become inactive)")
        print("  4. Schedule deposit in advance (blocks 4.75M-5M only)")
        print("  5. Request withdrawal (exit validation)")
        print("  6. Process withdrawal (get coins back)")
        print("  0. Exit")
        print("─"*70)
        
        choice = input("\nSelect option: ").strip()
        
        if choice == '0':
            print("👋 Goodbye!")
            break
        elif choice == '1':
            show_validator_status(ledger, address)
        elif choice == '2':
            set_auto_lock_preference(ledger, address, True)
        elif choice == '3':
            set_auto_lock_preference(ledger, address, False)
        elif choice == '4':
            schedule_deposit_advance(ledger, address)
        elif choice == '5':
            request_withdrawal_exit(ledger, address)
        elif choice == '6':
            process_withdrawal_complete(ledger, address)
        else:
            print("❌ Invalid option")
    
    return 0

if __name__ == "__main__":
    sys.exit(main_menu())
