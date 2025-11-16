#!/usr/bin/env python3
"""
TIMPAL Wallet CLI
Interactive command-line interface for creating and managing TIMPAL wallets
"""

import os
import sys
from app.wallet import Wallet


def print_banner():
    print("\n" + "="*60)
    print("          TIMPAL WALLET GENERATOR")
    print("="*60)
    print("Create a secure wallet for the TIMPAL blockchain")
    print("="*60 + "\n")


def create_new_wallet():
    """Interactive wallet creation"""
    print("🔐 CREATE NEW WALLET\n")
    
    # Get PIN
    while True:
        pin = input("Enter a 6-digit PIN (for wallet encryption): ").strip()
        if len(pin) < 6:
            print("❌ PIN must be at least 6 digits. Try again.\n")
            continue
        if not pin.isdigit():
            print("❌ PIN must contain only numbers. Try again.\n")
            continue
        
        pin_confirm = input("Confirm PIN: ").strip()
        if pin != pin_confirm:
            print("❌ PINs don't match. Try again.\n")
            continue
        
        break
    
    # Create wallet
    wallet = Wallet()
    try:
        mnemonic = wallet.create_new_wallet(pin)
        
        print("\n" + "="*60)
        print("✅ WALLET CREATED SUCCESSFULLY!")
        print("="*60 + "\n")
        
        print("📝 RECOVERY PHRASE (12 words):")
        print("-" * 60)
        print(f"  {mnemonic}")
        print("-" * 60)
        print("\n⚠️  CRITICAL SECURITY WARNINGS:")
        print("   1. WRITE DOWN your recovery phrase on paper")
        print("   2. NEVER share it with anyone")
        print("   3. Store it in a SAFE place")
        print("   4. If you lose it, your funds are GONE FOREVER")
        print("   5. Anyone with this phrase can steal your funds")
        print("\n📍 Your TIMPAL Address:")
        print(f"   {wallet.get_address()}")
        print("\n💾 Wallet saved to: wallet.json")
        print("\n" + "="*60 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error creating wallet: {e}")
        return False


def restore_wallet():
    """Interactive wallet restoration"""
    print("🔄 RESTORE WALLET FROM RECOVERY PHRASE\n")
    
    # Get recovery phrase
    print("Enter your 12-word recovery phrase:")
    print("(Type the 12 words separated by spaces)")
    mnemonic = input("> ").strip()
    
    words = mnemonic.split()
    if len(words) != 12:
        print(f"\n❌ Invalid recovery phrase. Expected 12 words, got {len(words)}")
        return False
    
    # Get PIN
    while True:
        pin = input("\nEnter a 6-digit PIN (for wallet encryption): ").strip()
        if len(pin) < 6:
            print("❌ PIN must be at least 6 digits. Try again.\n")
            continue
        if not pin.isdigit():
            print("❌ PIN must contain only numbers. Try again.\n")
            continue
        
        pin_confirm = input("Confirm PIN: ").strip()
        if pin != pin_confirm:
            print("❌ PINs don't match. Try again.\n")
            continue
        
        break
    
    # Restore wallet
    wallet = Wallet()
    try:
        wallet.restore_wallet(mnemonic, pin)
        
        print("\n" + "="*60)
        print("✅ WALLET RESTORED SUCCESSFULLY!")
        print("="*60)
        print(f"\n📍 Your TIMPAL Address:")
        print(f"   {wallet.get_address()}")
        print("\n💾 Wallet saved to: wallet.json")
        print("\n" + "="*60 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error restoring wallet: {e}")
        return False


def view_wallet_info():
    """View existing wallet information"""
    if not os.path.exists("wallet.json"):
        print("\n❌ No wallet found. Create a new wallet first.\n")
        return
    
    pin = input("\nEnter your wallet PIN: ").strip()
    
    wallet = Wallet()
    try:
        wallet.load_wallet(pin)
        
        print("\n" + "="*60)
        print("💼 WALLET INFORMATION")
        print("="*60)
        print(f"\n📍 Address: {wallet.get_address()}")
        print(f"🔑 Public Key: {wallet.get_public_key()[:20]}...{wallet.get_public_key()[-20:]}")
        print("\n" + "="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error loading wallet: {e}")


def main():
    """Main CLI interface"""
    print_banner()
    
    # Check if wallet already exists
    wallet_exists = os.path.exists("wallet.json")
    
    # Show appropriate menu based on wallet existence
    if not wallet_exists:
        print("What would you like to do?")
        print("  [1] Create new wallet")
        print("  [2] Restore wallet from recovery phrase")
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
        print("⚠️  A wallet already exists in this directory.")
        print("\nWhat would you like to do?")
        print("  [1] View wallet info")
        print("  [2] Create new wallet (overwrites existing)")
        print("  [3] Restore wallet from recovery phrase (overwrites existing)")
        print("  [4] Exit")
        choice = input("\nEnter your choice (1-4): ").strip()
        
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
        elif choice == "4":
            print("\n👋 Goodbye!")
            sys.exit(0)
        else:
            print("\n❌ Invalid choice. Please enter a number between 1 and 4.")
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user. Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
