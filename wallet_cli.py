#!/usr/bin/env python3
"""
TIMPAL Wallet CLI v2
Interactive command-line interface for creating and managing TIMPAL wallets with BIP-39 seed phrases
"""

import os
import sys
import requests
import time
from pathlib import Path
from getpass import getpass

# Add app directory to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "app"))

# Load testnet config
import app.config_testnet as config_testnet
sys.modules["config"] = config_testnet

from app.seed_wallet import SeedWallet
from app.wallet import Wallet
from app.transaction import Transaction

# Default node API endpoint (local node)
DEFAULT_NODE_API = "http://localhost:9001"


def print_banner():
    print("\n" + "="*70)
    print("          TIMPAL WALLET GENERATOR v2 (BIP-39)")
    print("="*70)
    print("Create a secure wallet with industry-standard seed phrase backup")
    print("="*70 + "\n")


def create_new_wallet():
    """Interactive wallet creation with BIP-39 seed phrase"""
    print("🔐 CREATE NEW WALLET (BIP-39 Compatible)\n")
    
    # Choose word count
    print("Select mnemonic length:")
    print("  [1] 12 words (recommended)")
    print("  [2] 24 words (extra security)")
    choice = input("Choice (1-2): ").strip()
    
    words = 12 if choice == "1" else 24
    
    # Get password
    while True:
        password = getpass("\nEnter password (min 8 characters): ")
        if len(password) < 8:
            print("❌ Password must be at least 8 characters. Try again.\n")
            continue
        
        password_confirm = getpass("Confirm password: ")
        if password != password_confirm:
            print("❌ Passwords don't match. Try again.\n")
            continue
        
        break
    
    # Get PIN
    while True:
        pin = input("\nEnter 6-digit PIN (for transfer authorization): ")
        if len(pin) < 6:
            print("❌ PIN must be at least 6 digits. Try again.\n")
            continue
        if not pin.isdigit():
            print("❌ PIN must contain only numbers. Try again.\n")
            continue
        
        pin_confirm = input("Confirm PIN: ")
        if pin != pin_confirm:
            print("❌ PINs don't match. Try again.\n")
            continue
        
        break
    
    # Optional passphrase (extra word beyond the seed phrase)
    print("\n🔒 Optional: Add a passphrase (extra security word)")
    print("   ⚠️  Only use if you understand BIP-39 passphrases!")
    print("   (For most users, answer 'no' - seed phrase alone is secure)")
    use_passphrase = input("   Add passphrase? (yes/no): ").strip().lower()
    
    passphrase = ""
    if use_passphrase == "yes":
        passphrase = getpass("   Enter passphrase: ")
        passphrase_confirm = getpass("   Confirm passphrase: ")
        if passphrase != passphrase_confirm:
            print("❌ Passphrases don't match. Aborting.")
            return False
    
    # Create wallet
    wallet = SeedWallet("wallet_v2.json")
    try:
        mnemonic = wallet.create_new_wallet(password=password, pin=pin, words=words, passphrase=passphrase)
        account = wallet.get_account(0)
        
        print("\n" + "="*70)
        print("✅ WALLET CREATED SUCCESSFULLY!")
        print("="*70 + "\n")
        
        print(f"📝 SEED PHRASE ({words} words):")
        print("=" * 70)
        print(f"  {mnemonic}")
        print("=" * 70)
        
        print("\n⚠️  CRITICAL SECURITY WARNINGS:")
        print("   1. ✍️  WRITE DOWN your seed phrase on paper (in order)")
        print("   2. 🔒 NEVER share it with anyone - not even TIMPAL staff!")
        print("   3. 🏦 Store it in a SAFE place (fireproof safe recommended)")
        print("   4. ❌ If you lose it, your funds are GONE FOREVER")
        print("   5. 💰 Anyone with this phrase can steal your funds")
        print("   6. 📵 NEVER store digitally (no photos, no cloud, no email)")
        if passphrase:
            print("   7. 🔐 You ALSO need your passphrase to restore!")
        
        print("\n📍 Your TIMPAL Address:")
        print(f"   {account['address']}")
        
        print("\n🔑 Public Key:")
        print(f"   {account['public_key'][:32]}...{account['public_key'][-32:]}")
        
        print("\n💾 Wallet saved to: wallet_v2.json")
        print(f"📂 Derivation path: {account['path']}")
        print("\n" + "="*70)
        
        # Verification step
        input("\n✋ Press ENTER after you've written down your seed phrase...")
        print("\n✅ Verification: Please enter the FIRST 3 words of your seed phrase:")
        verify_words = input("> ").strip()
        expected_words = " ".join(mnemonic.split()[:3])
        
        if verify_words == expected_words:
            print("✅ Correct! Your seed phrase is backed up.")
        else:
            print(f"❌ Doesn't match! Please double-check your backup.")
            print(f"   Expected: {expected_words}")
            print(f"   You entered: {verify_words}")
        
        print("\n" + "="*70 + "\n")
        return True
        
    except Exception as e:
        print(f"\n❌ Error creating wallet: {e}")
        import traceback
        traceback.print_exc()
        return False


def restore_wallet():
    """Interactive wallet restoration from BIP-39 seed phrase"""
    print("🔄 RESTORE WALLET FROM SEED PHRASE\n")
    
    # Get seed phrase
    print("Enter your seed phrase (12 or 24 words):")
    print("(Type the words separated by spaces)")
    mnemonic = input("> ").strip()
    
    words = mnemonic.split()
    if len(words) not in [12, 24]:
        print(f"\n❌ Invalid seed phrase. Expected 12 or 24 words, got {len(words)}")
        return False
    
    # Get password
    while True:
        password = getpass("\nEnter password for wallet file (min 8 characters): ")
        if len(password) < 8:
            print("❌ Password must be at least 8 characters. Try again.\n")
            continue
        
        password_confirm = getpass("Confirm password: ")
        if password != password_confirm:
            print("❌ Passwords don't match. Try again.\n")
            continue
        
        break
    
    # Get PIN
    while True:
        pin = input("\nEnter 6-digit PIN (for transfer authorization): ")
        if len(pin) < 6:
            print("❌ PIN must be at least 6 digits. Try again.\n")
            continue
        if not pin.isdigit():
            print("❌ PIN must contain only numbers. Try again.\n")
            continue
        
        pin_confirm = input("Confirm PIN: ")
        if pin != pin_confirm:
            print("❌ PINs don't match. Try again.\n")
            continue
        
        break
    
    # Ask about passphrase
    print("\n🔒 Did you use an optional passphrase with this seed?")
    print("   (Most users don't - if unsure, answer 'no')")
    use_passphrase = input("   Use passphrase? (yes/no): ").strip().lower()
    
    passphrase = ""
    if use_passphrase == "yes":
        passphrase = getpass("   Enter passphrase: ")
    
    # Restore wallet
    wallet = SeedWallet("wallet_v2.json")
    try:
        wallet.restore_wallet(mnemonic=mnemonic, password=password, pin=pin, passphrase=passphrase)
        account = wallet.get_account(0)
        
        print("\n" + "="*70)
        print("✅ WALLET RESTORED SUCCESSFULLY!")
        print("="*70)
        
        print(f"\n📍 Your TIMPAL Address:")
        print(f"   {account['address']}")
        
        print(f"\n🔑 Public Key:")
        print(f"   {account['public_key'][:32]}...{account['public_key'][-32:]}")
        
        print(f"\n💾 Wallet saved to: wallet_v2.json")
        print(f"📂 Derivation path: {account['path']}")
        print("\n" + "="*70 + "\n")
        
        return True
        
    except ValueError as e:
        if "checksum" in str(e).lower():
            print(f"\n❌ Invalid seed phrase: Checksum verification failed")
            print("   Please check your words and try again.")
        else:
            print(f"\n❌ Error restoring wallet: {e}")
        return False
    except Exception as e:
        print(f"\n❌ Error restoring wallet: {e}")
        import traceback
        traceback.print_exc()
        return False


def view_wallet_info():
    """View existing wallet information"""
    wallet_file = "wallet_v2.json"
    
    if not os.path.exists(wallet_file):
        print(f"\n❌ No wallet found at {wallet_file}")
        print("   Create a new wallet first.\n")
        return
    
    password = getpass("\nEnter your wallet password: ")
    
    # Ask about passphrase
    use_passphrase = input("Did you use a passphrase? (yes/no): ").strip().lower()
    passphrase = ""
    if use_passphrase == "yes":
        passphrase = getpass("Enter passphrase: ")
    
    wallet = SeedWallet(wallet_file)
    try:
        wallet.load_wallet(password, passphrase=passphrase)
        account = wallet.get_account(0)
        
        print("\n" + "="*70)
        print("💼 WALLET INFORMATION")
        print("="*70)
        print(f"\n📍 Address: {account['address']}")
        print(f"🔑 Public Key: {account['public_key'][:32]}...{account['public_key'][-32:]}")
        print(f"📂 Derivation Path: {account['path']}")
        print(f"📁 Wallet File: {wallet_file}")
        print(f"🔢 Wallet Version: 2 (BIP-39 Compatible)")
        print("\n" + "="*70 + "\n")
        
    except ValueError as e:
        print(f"\n❌ Error: {e}")
    except Exception as e:
        print(f"\n❌ Error loading wallet: {e}")


def check_balance(address: str = None, node_api: str = DEFAULT_NODE_API):
    """Check wallet balance from the network"""
    wallet_file = "wallet_v2.json"
    
    # If no address provided, load from wallet
    if address is None:
        if not os.path.exists(wallet_file):
            print(f"\n❌ No wallet found. Create or restore a wallet first.\n")
            return
        
        password = getpass("\nEnter your wallet password: ")
        
        use_passphrase = input("Did you use a passphrase? (yes/no): ").strip().lower()
        passphrase = ""
        if use_passphrase == "yes":
            passphrase = getpass("Enter passphrase: ")
        
        wallet = SeedWallet(wallet_file)
        try:
            wallet.load_wallet(password, passphrase=passphrase)
            account = wallet.get_account(0)
            address = account['address']
        except Exception as e:
            print(f"\n❌ Error loading wallet: {e}")
            return
    
    print(f"\n🔍 Checking balance for: {address}")
    print(f"   Node: {node_api}")
    
    try:
        resp = requests.get(f"{node_api}/api/account/{address}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            balance_tmpl = data.get('balance_tmpl', '0 TMPL')
            balance_pals = data.get('balance', 0)
            tx_count = data.get('transaction_count', 0)
            
            print("\n" + "="*70)
            print("💰 WALLET BALANCE")
            print("="*70)
            print(f"\n📍 Address: {address}")
            print(f"💵 Balance: {balance_tmpl}")
            print(f"📊 Transactions: {tx_count}")
            print("\n" + "="*70 + "\n")
        elif resp.status_code == 404:
            print("\n" + "="*70)
            print("💰 WALLET BALANCE")
            print("="*70)
            print(f"\n📍 Address: {address}")
            print(f"💵 Balance: 0.00000000 TMPL")
            print(f"📊 Transactions: 0")
            print("\n   (Address not yet on blockchain)")
            print("\n" + "="*70 + "\n")
        else:
            print(f"\n❌ Error: Node returned status {resp.status_code}")
    except requests.exceptions.ConnectionError:
        print(f"\n❌ Cannot connect to node at {node_api}")
        print("   Make sure the node is running and accessible.")
    except Exception as e:
        print(f"\n❌ Error checking balance: {e}")


def send_tmpl(node_api: str = DEFAULT_NODE_API):
    """Send TMPL to another address"""
    wallet_file = "wallet_v2.json"
    
    if not os.path.exists(wallet_file):
        print(f"\n❌ No wallet found. Create or restore a wallet first.\n")
        return
    
    # Load wallet
    password = getpass("\nEnter your wallet password: ")
    
    use_passphrase = input("Did you use a passphrase? (yes/no): ").strip().lower()
    passphrase = ""
    if use_passphrase == "yes":
        passphrase = getpass("Enter passphrase: ")
    
    wallet = SeedWallet(wallet_file)
    try:
        wallet.load_wallet(password, passphrase=passphrase)
        account = wallet.get_account(0)
        sender_address = account['address']
        private_key = account['private_key']
        public_key = account['public_key']
    except Exception as e:
        print(f"\n❌ Error loading wallet: {e}")
        return
    
    # Check current balance
    print(f"\n📍 Your address: {sender_address}")
    try:
        resp = requests.get(f"{node_api}/api/account/{sender_address}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            balance_pals = data.get('balance', 0)
            nonce = data.get('nonce', 0)
            print(f"💵 Current balance: {data.get('balance_tmpl', '0 TMPL')}")
        else:
            balance_pals = 0
            nonce = 0
            print(f"💵 Current balance: 0.00000000 TMPL")
    except Exception as e:
        print(f"\n❌ Cannot connect to node: {e}")
        return
    
    if balance_pals == 0:
        print("\n❌ You have no TMPL to send.")
        return
    
    # Get recipient
    print("\n📤 SEND TMPL")
    print("-" * 70)
    recipient = input("Recipient address (tmpl...): ").strip()
    
    if not recipient.startswith("tmpl") or len(recipient) != 48:
        print(f"\n❌ Invalid address format. Must be 'tmpl' + 44 hex characters.")
        return
    
    if recipient == sender_address:
        print(f"\n❌ Cannot send to yourself.")
        return
    
    # Get amount
    amount_str = input("Amount to send (in TMPL): ").strip()
    try:
        amount_tmpl = float(amount_str)
        if amount_tmpl <= 0:
            print("\n❌ Amount must be positive.")
            return
    except ValueError:
        print("\n❌ Invalid amount.")
        return
    
    # Convert to pals (1 TMPL = 100,000,000 pals)
    PALS_PER_TMPL = 100_000_000
    amount_pals = int(amount_tmpl * PALS_PER_TMPL)
    
    # Fee (0.0005 TMPL = 50,000 pals) - ALWAYS
    fee_pals = 50_000
    fee_tmpl = fee_pals / PALS_PER_TMPL
    
    total_pals = amount_pals + fee_pals
    
    if total_pals > balance_pals:
        print(f"\n❌ Insufficient balance.")
        print(f"   Need: {total_pals / PALS_PER_TMPL:.8f} TMPL (amount + fee)")
        print(f"   Have: {balance_pals / PALS_PER_TMPL:.8f} TMPL")
        return
    
    # Confirm
    print("\n" + "="*70)
    print("📝 TRANSACTION SUMMARY")
    print("="*70)
    print(f"   From:   {sender_address}")
    print(f"   To:     {recipient}")
    print(f"   Amount: {amount_tmpl:.8f} TMPL")
    print(f"   Fee:    {fee_tmpl:.8f} TMPL")
    print(f"   Total:  {total_pals / PALS_PER_TMPL:.8f} TMPL")
    print("="*70)
    
    confirm = input("\nConfirm transaction? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("\n✅ Transaction cancelled.")
        return
    
    # Enter PIN for authorization
    pin = input("Enter your 6-digit PIN to authorize: ").strip()
    
    # Verify PIN
    if not wallet.validate_pin(pin):
        print("\n❌ Invalid PIN. Transaction cancelled.")
        return
    
    # Create and sign transaction
    print("\n⏳ Creating and signing transaction...")
    
    tx = Transaction(
        sender=sender_address,
        recipient=recipient,
        amount=amount_pals,
        fee=fee_pals,
        timestamp=time.time(),
        nonce=nonce + 1,
        public_key=public_key
    )
    tx.sign(private_key)
    
    # Broadcast
    print("📡 Broadcasting to network...")
    try:
        resp = requests.post(
            f"{node_api}/submit_transaction",
            json=tx.to_dict(),
            timeout=15
        )
        
        if resp.status_code == 200:
            result = resp.json()
            print("\n" + "="*70)
            print("✅ TRANSACTION BROADCAST SUCCESSFUL!")
            print("="*70)
            print(f"\n   TX Hash: {tx.tx_hash}")
            print(f"   Status:  Pending confirmation")
            print("\n   Your transaction will be included in the next block.")
            print("\n" + "="*70 + "\n")
        else:
            error = resp.json().get('error', 'Unknown error')
            print(f"\n❌ Transaction failed: {error}")
    except Exception as e:
        print(f"\n❌ Error broadcasting: {e}")


def check_wallet_type():
    """Check what type of wallet exists"""
    has_v2 = os.path.exists("wallet_v2.json")
    has_v1 = os.path.exists("wallet.json")
    
    if has_v2:
        print("📁 Found wallet_v2.json (BIP-39 compatible)")
        return "v2"
    elif has_v1:
        print("📁 Found wallet.json (old format)")
        return "v1"
    else:
        return None


def main():
    """Main CLI interface"""
    print_banner()
    
    wallet_type = check_wallet_type()
    
    # Show appropriate menu
    if wallet_type is None:
        print("\nWhat would you like to do?")
        print("  [1] Create new wallet (BIP-39)")
        print("  [2] Restore wallet from seed phrase")
        print("  [3] Exit")
        choice = input("\nEnter your choice (1-3): ").strip()
        
        if choice == "1":
            create_new_wallet()
        elif choice == "2":
            restore_wallet()
        elif choice == "3":
            print("\n👋 Goodbye!")
            sys.exit(0)
        else:
            print("\n❌ Invalid choice. Please enter 1, 2, or 3.")
            sys.exit(1)
    
    else:
        print("\nWhat would you like to do?")
        print("  [1] View wallet info")
        print("  [2] Check balance")
        print("  [3] Send TMPL")
        print("  [4] Create new wallet (overwrites existing)")
        print("  [5] Restore wallet from seed phrase (overwrites existing)")
        print("  [6] Exit")
        
        choice = input("\nEnter your choice (1-6): ").strip()
        
        if choice == "1":
            view_wallet_info()
        elif choice == "2":
            check_balance()
        elif choice == "3":
            send_tmpl()
        elif choice == "4":
            confirm = input("\n⚠️  This will overwrite your existing wallet. Continue? (yes/no): ").strip().lower()
            if confirm == "yes":
                create_new_wallet()
            else:
                print("\n✅ Cancelled. Your existing wallet is safe.")
        elif choice == "5":
            confirm = input("\n⚠️  This will overwrite your existing wallet. Continue? (yes/no): ").strip().lower()
            if confirm == "yes":
                restore_wallet()
            else:
                print("\n✅ Cancelled. Your existing wallet is safe.")
        elif choice == "6":
            print("\n👋 Goodbye!")
            sys.exit(0)
        else:
            print("\n❌ Invalid choice. Please enter 1-6.")
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user. Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
