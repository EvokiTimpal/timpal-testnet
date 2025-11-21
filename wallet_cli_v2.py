#!/usr/bin/env python3
"""
TIMPAL Wallet CLI v2
Interactive command-line interface for creating and managing TIMPAL wallets with BIP-39 seed phrases
"""

import os
import sys
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
    
    # Optional passphrase (25th word)
    print("\n🔒 Optional: Add a passphrase (BIP-39 25th word)")
    print("   This adds extra security but MUST be remembered!")
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
    print("\n🔒 Did you use a passphrase (25th word) with this seed?")
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


def check_wallet_type():
    """Check what type of wallet exists"""
    has_v2 = os.path.exists("wallet_v2.json")
    has_v1 = os.path.exists("wallet.json")
    
    if has_v2:
        print("📁 Found wallet_v2.json (BIP-39 compatible)")
        return "v2"
    elif has_v1:
        print("📁 Found wallet.json (legacy format)")
        print("⚠️  Consider migrating to v2 using: python migrate_wallet.py wallet.json")
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
        print("  [2] Create new wallet (overwrites existing)")
        print("  [3] Restore wallet from seed phrase (overwrites existing)")
        if wallet_type == "v1":
            print("  [4] Migrate to v2 format")
            print("  [5] Exit")
            max_choice = 5
        else:
            print("  [4] Exit")
            max_choice = 4
        
        choice = input(f"\nEnter your choice (1-{max_choice}): ").strip()
        
        if choice == "1":
            view_wallet_info()
        elif choice == "2":
            confirm = input("\n⚠️  This will overwrite your existing wallet. Continue? (yes/no): ").strip().lower()
            if confirm == "yes":
                create_new_wallet()
            else:
                print("\n✅ Cancelled. Your existing wallet is safe.")
        elif choice == "3":
            confirm = input("\n⚠️  This will overwrite your existing wallet. Continue? (yes/no): ").strip().lower()
            if confirm == "yes":
                restore_wallet()
            else:
                print("\n✅ Cancelled. Your existing wallet is safe.")
        elif choice == "4" and wallet_type == "v1":
            print("\n📦 To migrate your wallet, run:")
            print("   python migrate_wallet.py wallet.json")
            print("\n   This will safely upgrade to BIP-39 format with backup.")
        elif (choice == "4" and wallet_type == "v2") or (choice == "5" and wallet_type == "v1"):
            print("\n👋 Goodbye!")
            sys.exit(0)
        else:
            print(f"\n❌ Invalid choice. Please enter a number between 1 and {max_choice}.")
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
