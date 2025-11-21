#!/usr/bin/env python3
"""
Generate new TIMPAL genesis block with v2 wallet (BIP-39)

This script:
1. Creates a new v2 wallet for genesis validator (or uses existing)
2. Generates a fresh genesis block
3. Outputs the new canonical genesis hash for config_testnet.py
"""

import sys
import os
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent / "app"))

import app.config_testnet as config_testnet
sys.modules["config"] = config_testnet

from app.seed_wallet import SeedWallet
from app.ledger import Ledger
from app.block import Block
import json

def main():
    print("\n" + "="*70)
    print("  TIMPAL TESTNET GENESIS GENERATOR (v2 Wallet)")
    print("="*70 + "\n")
    
    # Check if genesis wallet already exists
    if os.path.exists("genesis_wallet_v2.json"):
        print("✅ Found existing genesis_wallet_v2.json")
        print("   This will preserve the genesis validator address")
        use_existing = input("   Use existing wallet? (yes/no): ").strip().lower()
        
        if use_existing != "yes":
            print("\n❌ Aborted. Delete genesis_wallet_v2.json first if you want a fresh wallet.")
            return
        
        # Load existing wallet
        password = input("\nEnter wallet password: ")
        wallet = SeedWallet("genesis_wallet_v2.json")
        try:
            wallet.load_wallet(password)
            account = wallet.get_account(0)
            print(f"\n✅ Loaded existing genesis wallet")
            print(f"   Address: {account['address']}")
        except Exception as e:
            print(f"\n❌ Failed to load wallet: {e}")
            return
    else:
        print("📝 Creating new genesis wallet with BIP-39 seed phrase...")
        print("   This will be the TESTNET genesis validator wallet\n")
        
        # Get password and PIN
        password = input("Enter password (min 8 chars): ")
        if len(password) < 8:
            print("❌ Password must be at least 8 characters")
            return
        
        pin = input("Enter PIN (6+ digits): ")
        if len(pin) < 6 or not pin.isdigit():
            print("❌ PIN must be at least 6 digits (numeric only)")
            return
        
        # Create wallet
        wallet = SeedWallet("genesis_wallet_v2.json")
        mnemonic = wallet.create_new_wallet(password=password, pin=pin, words=12)
        account = wallet.get_account(0)
        
        print("\n" + "="*70)
        print("✅ GENESIS WALLET CREATED!")
        print("="*70)
        print(f"\n📝 SEED PHRASE (12 words) - WRITE THIS DOWN:")
        print("="*70)
        print(f"  {mnemonic}")
        print("="*70)
        print("\n⚠️  CRITICAL: Write down this seed phrase!")
        print("   You'll need it to restore the genesis validator wallet")
        print(f"\n📍 Genesis Validator Address: {account['address']}")
        print(f"🔑 Public Key: {account['public_key'][:32]}...{account['public_key'][-32:]}")
        
        input("\n✋ Press ENTER after writing down the seed phrase...")
    
    # Generate genesis block
    print("\n" + "="*70)
    print("GENERATING GENESIS BLOCK...")
    print("="*70 + "\n")
    
    # Get keys from wallet
    account = wallet.get_account(0)
    genesis_address = account["address"]
    genesis_pubkey = account["public_key"]
    
    # Create temporary ledger
    temp_ledger = Ledger(data_dir="temp_genesis_ledger")
    
    # Create genesis block
    genesis_block = Block(
        height=0,
        previous_hash="0" * 64,
        timestamp=1732147200,  # Fixed timestamp for testnet
        transactions=[],
        validator=genesis_address,
        proposer_pubkey=genesis_pubkey
    )
    
    # Sign genesis block
    genesis_block.sign(account["private_key"])
    
    # Calculate canonical hash
    canonical_hash = genesis_block.calculate_hash()
    
    print(f"✅ Genesis block created!")
    print(f"\n📊 Genesis Block Details:")
    print(f"   Height: {genesis_block.height}")
    print(f"   Timestamp: {genesis_block.timestamp}")
    print(f"   Validator: {genesis_block.validator}")
    print(f"   Hash: {canonical_hash}")
    
    print(f"\n" + "="*70)
    print(f"UPDATE app/config_testnet.py:")
    print(f"="*70)
    print(f"\n# Genesis validator (v2 wallet)")
    print(f"GENESIS_VALIDATORS = {{")
    print(f"    '{genesis_address}': '{genesis_pubkey}'")
    print(f"}}")
    print(f"\n# Canonical genesis hash (v2 wallet)")
    print(f"CANONICAL_GENESIS_HASH = '{canonical_hash}'")
    print(f"\n" + "="*70)
    
    # Save genesis block for reference
    genesis_data = {
        "height": genesis_block.height,
        "hash": canonical_hash,
        "timestamp": genesis_block.timestamp,
        "validator": genesis_block.validator,
        "public_key": genesis_pubkey,
        "signature": genesis_block.signature
    }
    
    with open("genesis_block_v2.json", "w") as f:
        json.dump(genesis_data, f, indent=2)
    
    print(f"\n✅ Genesis block saved to: genesis_block_v2.json")
    print(f"✅ Genesis wallet saved to: genesis_wallet_v2.json")
    print(f"\n🎯 Next steps:")
    print(f"   1. Update app/config_testnet.py with the values above")
    print(f"   2. Copy genesis_wallet_v2.json to your VPS bootstrap node")
    print(f"   3. Set TIMPAL_WALLET_PIN environment variable on VPS")
    print(f"   4. Start testnet: python run_testnet_node.py --port 9000")
    print("\n" + "="*70 + "\n")
    
    # Cleanup temp ledger
    import shutil
    if os.path.exists("temp_genesis_ledger"):
        shutil.rmtree("temp_genesis_ledger")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
