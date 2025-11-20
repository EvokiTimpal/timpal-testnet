#!/usr/bin/env python3
"""
Generate Canonical Genesis Block for TIMPAL Testnet

This script generates the canonical genesis block and calculates its hash.
This hash must be hardcoded in config_testnet.py to prevent eclipse attacks.

Run this script ONCE to establish the canonical genesis, then never modify
the genesis parameters again (timestamp, validators, etc).
"""

import sys
import json
import hashlib

# Setup module path and inject config
sys.path.insert(0, '.')

# Import testnet configuration and inject it as 'config' module
from app import config_testnet
sys.modules['config'] = config_testnet

# Now we can import block and transaction
from app.block import Block
from app.transaction import Transaction

def generate_canonical_genesis():
    """Generate the canonical genesis block for testnet"""
    
    print("="*80)
    print("GENERATING CANONICAL GENESIS BLOCK")
    print("="*80)
    print()
    
    # Genesis parameters from config
    print(f"Chain ID: {config_testnet.CHAIN_ID}")
    print(f"Genesis Timestamp: {config_testnet.GENESIS_TIMESTAMP}")
    print(f"Genesis Validators: {list(config_testnet.GENESIS_VALIDATORS.keys())}")
    print()
    
    # Create genesis validator registration transactions
    # MUST match Block.create_genesis_block() implementation exactly
    genesis_transactions = []
    for validator_address, public_key in config_testnet.GENESIS_VALIDATORS.items():
        import hashlib
        
        # Match Block.create_genesis_block() implementation
        tx = Transaction(
            sender=validator_address,
            recipient=validator_address,
            amount=0,
            fee=0,
            nonce=0,
            public_key=public_key,
            tx_type="validator_registration",
            device_id="genesis",  # MUST match Block.create_genesis_block()
            timestamp=config_testnet.GENESIS_TIMESTAMP
        )
        # Match Block.create_genesis_block() signature/hash generation
        tx.signature = hashlib.sha256(b"genesis_validator_registration").hexdigest()
        tx.tx_hash = hashlib.sha256(f"{validator_address}{public_key}{config_testnet.GENESIS_TIMESTAMP}".encode()).hexdigest()
        genesis_transactions.append(tx)
    
    # Get the first validator as proposer
    genesis_proposer = list(config_testnet.GENESIS_VALIDATORS.keys())[0]
    
    # Create genesis block
    genesis_block = Block(
        height=0,
        previous_hash="0" * 64,
        timestamp=config_testnet.GENESIS_TIMESTAMP,
        transactions=genesis_transactions,
        proposer=genesis_proposer,
        reward=0  # No reward for genesis block
    )
    
    # Calculate merkle root and block hash
    block_hash = genesis_block.calculate_hash()
    
    print("="*80)
    print("CANONICAL GENESIS BLOCK GENERATED")
    print("="*80)
    print()
    print(f"Block Height: {genesis_block.height}")
    print(f"Timestamp: {genesis_block.timestamp}")
    print(f"Proposer: {genesis_block.proposer}")
    print(f"Merkle Root: {genesis_block.merkle_root}")
    print(f"Previous Hash: {genesis_block.previous_hash}")
    print(f"Transactions: {len(genesis_block.transactions)}")
    print()
    print("="*80)
    print("CANONICAL GENESIS BLOCK HASH")
    print("="*80)
    print()
    print(f"{block_hash}")
    print()
    print("="*80)
    print("NEXT STEPS")
    print("="*80)
    print()
    print("1. Copy the hash above")
    print("2. Add to config_testnet.py:")
    print(f"   CANONICAL_GENESIS_HASH = \"{block_hash}\"")
    print()
    print("3. Update ledger.py to validate against this hash")
    print()
    print("4. Deploy fresh testnet with this canonical genesis")
    print()
    
    # Save genesis block to file for reference
    genesis_data = {
        "height": genesis_block.height,
        "timestamp": genesis_block.timestamp,
        "previous_hash": genesis_block.previous_hash,
        "merkle_root": genesis_block.merkle_root,
        "proposer": genesis_block.proposer,
        "reward": genesis_block.reward,
        "hash": block_hash,
        "transactions": [
            {
                "sender": tx.sender,
                "recipient": tx.recipient,
                "amount": tx.amount,
                "fee": tx.fee,
                "tx_type": tx.tx_type,
                "timestamp": tx.timestamp,
                "nonce": tx.nonce,
                "public_key": tx.public_key,
                "device_id": tx.device_id
            }
            for tx in genesis_block.transactions
        ]
    }
    
    with open("canonical_genesis_testnet.json", "w") as f:
        json.dump(genesis_data, f, indent=2)
    
    print(f"✅ Genesis block saved to: canonical_genesis_testnet.json")
    print()
    
    return block_hash

if __name__ == "__main__":
    generate_canonical_genesis()
