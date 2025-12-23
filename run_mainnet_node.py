#!/usr/bin/env python3
"""
TIMPAL MAINNET NODE LAUNCHER

Simple script to run a TIMPAL mainnet validator node.
This connects to the mainnet (production network).

Usage:
    python run_mainnet_node.py --port 8765
    python run_mainnet_node.py --port 8766 --seed ws://seed1.timpal.net:8765
"""

import sys
import os
import argparse
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

import config
sys.modules['config'] = config

from node import Node
from wallet import Wallet


class MainnetNode:
    """TIMPAL Mainnet Validator Node"""
    
    def __init__(self, port: int = 8765, data_dir: str = None, seed_nodes: list = None):
        self.port = port
        self.data_dir = data_dir or f"mainnet_data_node_{port}"
        self.seed_nodes = seed_nodes or []
        
        os.makedirs(self.data_dir, exist_ok=True)
        
        wallet_path = os.path.join(self.data_dir, "validator_wallet.json")
        self.wallet = Wallet(wallet_path)
        
        wallet_pin = os.environ.get("TIMPAL_WALLET_PIN")
        if not wallet_pin:
            print(f"\n⚠️  ERROR: TIMPAL_WALLET_PIN environment variable not set!")
            print(f"   Set it with: export TIMPAL_WALLET_PIN='your_secure_pin'")
            print(f"   Minimum 6 characters required for security")
            raise ValueError("TIMPAL_WALLET_PIN environment variable required")
        
        if len(wallet_pin) < 6:
            raise ValueError("TIMPAL_WALLET_PIN must be at least 6 characters")
        
        # Check if this should be the genesis validator (using specific seed phrase)
        genesis_seed = os.environ.get("GENESIS_SEED_CORRECT")
        
        if not os.path.exists(wallet_path):
            if genesis_seed:
                print(f"\n🔑 Restoring GENESIS validator wallet from seed phrase...")
                self.wallet.restore_wallet(genesis_seed, wallet_pin)
                self.wallet.save_wallet(wallet_pin)
                print(f"   Address: {self.wallet.address}")
                print(f"   Wallet saved to: {wallet_path}")
                print(f"   ✅ Genesis validator restored")
            else:
                print(f"\n🔑 Creating new validator wallet...")
                self.wallet.create_new_wallet(wallet_pin)
                print(f"   Address: {self.wallet.address}")
                print(f"   Wallet saved to: {wallet_path}")
                print(f"   ⚠️  BACKUP YOUR WALLET FILE: {wallet_path}")
        else:
            print(f"\n🔑 Loading existing validator wallet...")
            if not self.wallet.load_wallet(wallet_pin):
                raise ValueError("Failed to load wallet - wrong PIN or corrupted file")
            print(f"   Address: {self.wallet.address}")
        
        ledger_data_dir = os.path.join(self.data_dir, "ledger")
        
        # SECURITY: Device check enforces 1 node per device (Sybil prevention)
        self.node = Node(
            device_id=self.wallet.address,
            reward_address=self.wallet.address,
            p2p_port=port,
            private_key=self.wallet.private_key,
            public_key=self.wallet.public_key,
            skip_device_check=False,
            data_dir=ledger_data_dir,
            use_production_storage=True,
            testnet_mode=False
        )
        
        self.node.p2p.seed_nodes = seed_nodes or []
    
    async def export_for_explorer(self):
        """Export blockchain data for Block Explorer to read"""
        try:
            import json
            import tempfile
            
            ledger_dir = os.path.join(self.data_dir, "ledger")
            export_path = os.path.join(ledger_dir, "ledger.json")
            
            # Create state dictionary from current ledger
            state = {
                "balances": self.node.ledger.balances,
                "nonces": self.node.ledger.nonces,
                "blocks": [block.to_dict() for block in self.node.ledger.blocks],
                "total_emitted_pals": self.node.ledger.total_emitted_pals,
                "validator_set": list(self.node.ledger.validator_set),
                "validator_registry": self.node.ledger.validator_registry,
                "finality_checkpoints": self.node.ledger.fork_choice.finality_checkpoints,
                "validator_economics": self.node.ledger.validator_economics.to_dict()
            }
            
            # Write to temp file first, then atomic rename
            temp_fd, temp_path = tempfile.mkstemp(dir=ledger_dir, suffix='.tmp')
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            
            os.replace(temp_path, export_path)
            
        except Exception as e:
            print(f"⚠️  Failed to export blockchain data: {e}")
    
    async def status_loop(self):
        """Print node status periodically and export data for Block Explorer"""
        await asyncio.sleep(10)
        
        while True:
            await asyncio.sleep(30)
            
            latest_block = self.node.ledger.get_latest_block()
            current_height = latest_block.height if latest_block else 0
            
            print(f"\n📊 Node Status:")
            print(f"   Peers: {self.node.p2p.get_peer_count()}")
            print(f"   Network nodes: {len(self.node.p2p.get_known_nodes())}")
            print(f"   Current height: {current_height}")
            print(f"   Transactions in mempool: {len(self.node.mempool.pending_transactions)}")
            
            # Export blockchain data for Block Explorer
            await self.export_for_explorer()
    
    async def run_http_api(self):
        """Simple HTTP API for submitting transactions"""
        from aiohttp import web
        from app.transaction import Transaction
        import time
        import config
        
        async def send_transaction(request):
            """
            User-friendly transaction endpoint.
            Accepts: sender address, recipient address, amount (TMPL), wallet PIN
            Creates, signs, and submits the transaction.
            """
            try:
                data = await request.json()
                
                # Extract and validate inputs
                sender = data.get('sender')
                recipient = data.get('recipient')
                amount_tmpl = data.get('amount')  # Amount in TMPL tokens
                pin = data.get('pin')
                
                # Validate required fields
                if not all([sender, recipient, amount_tmpl, pin]):
                    return web.json_response({
                        'status': 'error',
                        'message': 'Missing required fields: sender, recipient, amount, pin'
                    }, status=400)
                
                # Validate addresses (must start with 'tmpl' and be 48 chars)
                if not sender.startswith('tmpl') or len(sender) != 48:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Invalid sender address format'
                    }, status=400)
                
                if not recipient.startswith('tmpl') or len(recipient) != 48:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Invalid recipient address format'
                    }, status=400)
                
                # Validate amount
                try:
                    amount_float = float(amount_tmpl)
                    if amount_float <= 0:
                        return web.json_response({
                            'status': 'error',
                            'message': 'Amount must be greater than 0'
                        }, status=400)
                    
                    # Convert TMPL to pals (1 TMPL = 100,000,000 pals)
                    amount_picotokens = int(amount_float * config.PALS_PER_TMPL)
                    
                except (ValueError, TypeError):
                    return web.json_response({
                        'status': 'error',
                        'message': 'Invalid amount format'
                    }, status=400)
                
                # Load wallet for this sender
                wallet_path = os.path.join(self.data_dir, "validator_wallet.json")
                
                # Check if wallet exists
                if not os.path.exists(wallet_path):
                    return web.json_response({
                        'status': 'error',
                        'message': 'Wallet not found for this address'
                    }, status=404)
                
                # Load and decrypt wallet
                temp_wallet = Wallet(wallet_path)
                if not temp_wallet.load_wallet(pin):
                    return web.json_response({
                        'status': 'error',
                        'message': 'Invalid PIN or wallet decryption failed'
                    }, status=401)
                
                # Verify wallet address matches sender
                if temp_wallet.address != sender:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Wallet address does not match sender address'
                    }, status=400)
                
                # Check balance
                balance = self.node.ledger.get_balance(sender)
                fee = config.FEE  # Fixed 0.0005 TMPL fee
                total_required = amount_picotokens + fee
                
                if balance < total_required:
                    balance_tmpl = balance / config.PICOTOKEN
                    required_tmpl = total_required / config.PICOTOKEN
                    return web.json_response({
                        'status': 'error',
                        'message': f'Insufficient balance. Have: {balance_tmpl:.6f} TMPL, Need: {required_tmpl:.6f} TMPL (including 0.0005 TMPL fee)'
                    }, status=400)
                
                # Get nonce - CRITICAL: nonce must be next available, not last used
                # ledger.get_nonce() returns the NEXT required nonce (confirmed transactions)
                # mempool.get_pending_nonce() returns the NEXT free nonce (after pending transactions)
                # We use max() to get the correct next nonce considering both sources
                ledger_nonce = self.node.ledger.get_nonce(sender)
                pending_nonce = self.node.mempool.get_pending_nonce(sender)
                next_nonce = max(ledger_nonce, pending_nonce)
                
                # Create transaction
                tx = Transaction(
                    sender=sender,
                    recipient=recipient,
                    amount=amount_picotokens,
                    fee=fee,
                    timestamp=time.time(),
                    nonce=next_nonce,
                    public_key=temp_wallet.public_key,
                    tx_type='transfer'
                )
                
                # Sign transaction
                tx.sign(temp_wallet.private_key)
                
                # Clear sensitive data
                del temp_wallet
                del pin
                
                # Submit to mempool
                if self.node.submit_transaction(tx):
                    return web.json_response({
                        'status': 'success',
                        'message': 'Transaction submitted successfully',
                        'tx_hash': tx.calculate_hash(),
                        'confirmation_time': '~3 seconds (1 block)'
                    })
                else:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Transaction rejected by mempool'
                    }, status=400)
                    
            except Exception as e:
                # SECURITY: Never expose internal error details to clients
                # Log the full error server-side for debugging
                import traceback
                print(f"❌ /send endpoint error: {e}")
                print(traceback.format_exc())
                
                # Return sanitized error message to client
                return web.json_response({
                    'status': 'error',
                    'message': 'Transaction processing failed. Please check your inputs and try again.'
                }, status=500)
        
        async def submit_transaction(request):
            """Accept and submit a pre-signed transaction"""
            try:
                tx_data = await request.json()
                
                # Create Transaction object from dict
                tx = Transaction(
                    sender=tx_data['sender'],
                    recipient=tx_data['recipient'],
                    amount=tx_data['amount'],
                    fee=tx_data['fee'],
                    timestamp=tx_data['timestamp'],
                    nonce=tx_data['nonce'],
                    signature=tx_data.get('signature'),
                    public_key=tx_data.get('public_key'),
                    tx_type=tx_data.get('tx_type', 'transfer'),
                    device_id=tx_data.get('device_id')
                )
                
                # Submit to node mempool
                if self.node.submit_transaction(tx):
                    return web.json_response({
                        'status': 'success',
                        'message': 'Transaction accepted',
                        'tx_hash': tx.calculate_hash()
                    })
                else:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Transaction rejected'
                    }, status=400)
                    
            except Exception as e:
                return web.json_response({
                    'status': 'error',
                    'message': str(e)
                }, status=500)
        
        async def get_blocks_range(request):
            """
            HTTP API endpoint for batch block sync (Tendermint-style).
            Returns blocks from start_height to end_height.
            Max 100 blocks per request to prevent memory issues.
            """
            try:
                start = int(request.query.get('start', 0))  # Default to 0 to include genesis
                end = int(request.query.get('end', start))
                
                # Limit to 100 blocks per request
                if end - start > 100:
                    return web.json_response({
                        'error': 'Max 100 blocks per request'
                    }, status=400)
                
                latest_block = self.node.ledger.get_latest_block()
                if not latest_block:
                    return web.json_response({
                        'blocks': [],
                        'latest_height': 0
                    })
                
                # Ensure end doesn't exceed latest height
                end = min(end, latest_block.height)
                
                blocks = []
                for height in range(start, end + 1):
                    block = self.node.ledger.get_block_by_height(height)
                    if block:
                        blocks.append(block.to_dict())
                
                return web.json_response({
                    'blocks': blocks,
                    'latest_height': latest_block.height,
                    'count': len(blocks)
                })
                
            except Exception as e:
                return web.json_response({
                    'error': str(e)
                }, status=500)
        
        async def get_health(request):
            """Health check endpoint"""
            latest_block = self.node.ledger.get_latest_block()
            return web.json_response({
                'status': 'healthy',
                'height': latest_block.height if latest_block else 0,
                'peers': self.node.p2p.get_peer_count(),
                'validator_count': self.node.ledger.get_validator_count()
            })
        
        async def get_account(request):
            """Get account balance, nonce, and pending_nonce"""
            try:
                address = request.match_info['address']
                
                # Validate address format (basic check)
                if not address or len(address) < 20:
                    return web.json_response({
                        'error': 'Invalid address format'
                    }, status=400)
                
                # Get balance and nonce from ledger
                balance = self.node.ledger.get_balance(address)
                ledger_nonce = self.node.ledger.get_nonce(address)
                
                # Get pending nonce from mempool (includes pending transactions)
                pending_nonce = max(ledger_nonce, self.node.mempool.get_pending_nonce(address))
                
                # Get count of pending transactions from this sender
                pending_count = self.node.mempool.get_sender_pending_count(address)
                
                return web.json_response({
                    'address': address,
                    'balance': balance,
                    'nonce': ledger_nonce,
                    'pending_nonce': pending_nonce,
                    'pending_count': pending_count
                })
                
            except Exception as e:
                return web.json_response({
                    'error': str(e)
                }, status=500)
        
        app = web.Application()
        app.router.add_post('/send', send_transaction)  # User-friendly endpoint
        app.router.add_post('/submit_transaction', submit_transaction)  # Pre-signed transactions
        app.router.add_get('/api/blocks/range', get_blocks_range)
        app.router.add_get('/api/health', get_health)
        app.router.add_get('/api/account/{address}', get_account)
        
        # Run on port 9001 (P2P is on 9000)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port + 1)
        await site.start()
        
        print(f"📡 HTTP API running on port {self.port + 1}")
    
    async def start(self):
        """Start the mainnet node"""
        print("\n" + "="*80)
        print("TIMPAL MAINNET NODE")
        print("="*80)
        print(f"\n⚙️  Configuration:")
        print(f"   Network: {config.CHAIN_ID}")
        print(f"   Symbol: {config.SYMBOL}")
        print(f"   Port: {self.port}")
        print(f"   Data directory: {self.data_dir}")
        print(f"   Validator address: {self.wallet.address}")
        print(f"   Seed nodes: {len(self.seed_nodes)}")
        
        if self.seed_nodes:
            for seed in self.seed_nodes:
                print(f"      - {seed}")
        else:
            print(f"      (No seed nodes - this is a bootstrap node)")
        
        print(f"\n🚀 Starting node...")
        
        # Run node as background task so status loop can start
        asyncio.create_task(self.node.start())
        
        # Start status loop task
        asyncio.create_task(self.status_loop())
        
        # Start HTTP API server
        asyncio.create_task(self.run_http_api())
        
        print(f"✅ Node is running!")
        print(f"\n💡 Other nodes can connect to: ws://YOUR_IP:{self.port}")
        print(f"\nPress Ctrl+C to stop\n")
        
        try:
            await asyncio.Future()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down node...")
            await self.node.stop()
            print("✅ Node stopped cleanly")


def main():
    # ==========================================
    # 🔒 MAINNET SAFETY: NO RESET FLAG ALLOWED
    # ==========================================
    # CRITICAL: This is the mainnet launcher - NEVER add a --reset flag here!
    # Testnet has --reset for development, but mainnet data is PERMANENT.
    # Any destructive operations on mainnet would be catastrophic.
    # ==========================================
    
    parser = argparse.ArgumentParser(
        description="Run a TIMPAL mainnet validator node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Start first node (bootstrap):
    python run_mainnet_node.py --port 9000
  
  Start second node (connect to first):
    python run_mainnet_node.py --port 3000 --seed ws://localhost:9000
  
  Start third node (connect to first):
    python run_mainnet_node.py --port 3001 --seed ws://localhost:9000
"""
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=9000,
        help="Port for P2P network (default: 9000)"
    )
    
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Data directory for blockchain and wallet (default: mainnet_data_node_PORT)"
    )
    
    parser.add_argument(
        "--seed",
        type=str,
        action="append",
        dest="seeds",
        help="Seed node address (can be specified multiple times). Example: ws://192.168.1.10:9000"
    )
    
    args = parser.parse_args()
    
    seed_nodes = args.seeds or []
    
    node = MainnetNode(
        port=args.port,
        data_dir=args.data_dir,
        seed_nodes=seed_nodes
    )
    
    try:
        asyncio.run(node.start())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


if __name__ == "__main__":
    main()
