#!/usr/bin/env python3
"""
TIMPAL TESTNET NODE LAUNCHER

Simple script to run a TIMPAL testnet validator node.
This connects to the testnet (separate from mainnet).

⚠️  IMPORTANT: Port 9000 is reserved for the bootstrap node only!
    All other validators MUST use a different port + --seed flag.

📡 UNDERSTANDING PORTS:
    When you run a node, it creates TWO ports:
    
    1. P2P Network Port (your --port value)
       → Example: --port 8001
       → Used for node-to-node blockchain communication
       
    2. HTTP API Port (your port + 1)
       → Example: port 8002 (automatically created)
       → Used by Block Explorer for transactions and queries
    
    The Block Explorer needs to connect to the HTTP API port!
    See TROUBLESHOOTING.md for explorer setup instructions.

USAGE EXAMPLES:

    # Start a new testnet (genesis node - creates blockchain)
    python3 run_testnet_node.py --port 9000 --genesis
    # → Creates P2P on 9000, HTTP API on 9001

    # Join an existing testnet (connect to any seed node)
    python3 run_testnet_node.py --port 8001 --seed ws://SEED_NODE_IP:9000
    # → Creates P2P on 8001, HTTP API on 8002
    
    python3 run_testnet_node.py --port 8002 --seed ws://SEED_NODE_IP:9000
    # → Creates P2P on 8002, HTTP API on 8003

⚠️  If you forget --seed when joining, you will create a private chain instead of joining the testnet!

🔧 CONNECTING BLOCK EXPLORER:
    If your node is on port 8001, tell the explorer to use port 8002:
    
    export EXPLORER_API_PORT=8002
    python3 start_explorer.py --port 8080
"""

import sys
import os
import argparse
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

# Load testnet config BEFORE importing Node
import app.config_testnet as config_testnet
sys.modules["config"] = config_testnet
config = config_testnet

from node import Node
from wallet import Wallet
from seed_wallet import SeedWallet


def resolve_data_dir(port: int, data_dir: str = None) -> str:
    """
    Resolve the data directory path for a testnet node.
    
    Priority:
    1. Explicit --data-dir argument (if provided)
    2. TIMPAL_DATA_PATH env variable + testnet_data_node_{port}
    3. Default: ~/.timpal/testnet_data_node_{port}
    
    This avoids storing chain data in project directories (like Desktop)
    which can be iCloud-synced on macOS, causing consensus divergence.
    """
    if data_dir:
        # User explicitly specified a path - use it (with ~ expansion)
        return os.path.expanduser(data_dir)
    
    # Check for TIMPAL_DATA_PATH environment variable
    data_path_env = os.environ.get("TIMPAL_DATA_PATH")
    if data_path_env:
        data_root = Path(os.path.expanduser(data_path_env))
    else:
        # Default: ~/.timpal (safe from iCloud sync)
        data_root = Path.home() / ".timpal"
    
    resolved_path = str(data_root / f"testnet_data_node_{port}")
    return resolved_path


def warn_if_icloud_path(data_dir: str) -> None:
    """
    Warn macOS users if their data directory is under Desktop or Documents,
    which may be iCloud-synced and cause consensus divergence.
    """
    import sys
    if sys.platform != "darwin":
        return
    
    data_path = Path(data_dir).resolve()
    path_parts_lower = [part.lower() for part in data_path.parts]
    
    if "desktop" in path_parts_lower or "documents" in path_parts_lower:
        print("\n" + "!" * 80)
        print("⚠️  WARNING: macOS iCloud Sync Risk Detected!")
        print("!" * 80)
        print(f"\nYour data directory is under Desktop or Documents:")
        print(f"   {data_dir}")
        print(f"\nOn macOS, these folders may be synced to iCloud, which can cause:")
        print(f"   • Chain state duplication across devices")
        print(f"   • Old genesis data being restored from iCloud")
        print(f"   • Permanent consensus forks that look like network bugs")
        print(f"\n🔧 RECOMMENDED FIX:")
        print(f"   1. Disable 'Desktop & Documents' in iCloud settings, OR")
        print(f"   2. Use the default path: ~/.timpal/testnet_data_node_{{port}}")
        print(f"   3. Or set: export TIMPAL_DATA_PATH=~/.timpal")
        print("!" * 80 + "\n")


class TestnetNode:
    """TIMPAL Testnet Validator Node"""
    
    def __init__(self, port: int = 8765, data_dir: str = None, seed_nodes: list = None, is_genesis_node: bool = False, skip_device_check: bool = False):
        self.port = port
        # Use resolve_data_dir for consistent path resolution
        # Default: ~/.timpal/testnet_data_node_{port} (safe from iCloud sync)
        self.data_dir = resolve_data_dir(port, data_dir)
        self.seed_nodes = seed_nodes or []
        self.is_genesis_node = is_genesis_node
        self.skip_device_check = skip_device_check
        
        # DIAGNOSTIC: Print startup info for deployment debugging
        print("=" * 60)
        print("TIMPAL TESTNET NODE - STARTUP DIAGNOSTICS")
        print("=" * 60)
        print(f"Working directory: {os.getcwd()}")
        print(f"Data directory: {self.data_dir}")
        print(f"Files in current dir: {os.listdir('.')[:20]}...")  # First 20 files
        print(f"wallets.json exists: {os.path.exists('wallets.json')}")
        print(f"wallet_v2.json exists: {os.path.exists('wallet_v2.json')}")
        print(f"wallet.json exists: {os.path.exists('wallet.json')}")
        print(f"TIMPAL_WALLET_PASSWORD set: {'Yes' if os.environ.get('TIMPAL_WALLET_PASSWORD') else 'NO!'}")
        print(f"TIMPAL_WALLET_PIN set: {'Yes' if os.environ.get('TIMPAL_WALLET_PIN') else 'NO!'}")
        print("=" * 60)
        
        # Warn macOS users if data directory is under iCloud-synced paths
        warn_if_icloud_path(self.data_dir)
        
        os.makedirs(self.data_dir, exist_ok=True)
        
        # -----------------------------------------------
        # TESTNET: Load wallet (v2 or v1) and set genesis validator
        # -----------------------------------------------
        
        # Check for v3 multi-vault wallet first, then v2, then v1
        if os.path.exists("wallets.json"):
            wallet_path = "wallets.json"
            wallet_version = 3
            print(f"[TESTNET] Loading v3 wallet (multi-vault HD)")
        elif os.path.exists("wallet_v2.json"):
            wallet_path = "wallet_v2.json"
            wallet_version = 2
            print(f"[TESTNET] Loading v2 wallet (BIP-39)")
        elif os.path.exists("wallet.json"):
            wallet_path = "wallet.json"
            wallet_version = 1
            print(f"[TESTNET] Loading v1 wallet (legacy)")
        else:
            print("❌ FATAL: No wallet file found!")
            print(f"   Looked for: wallets.json, wallet_v2.json, wallet.json")
            print(f"   Current directory: {os.getcwd()}")
            print(f"   Directory contents: {os.listdir('.')}")
            raise ValueError("No wallet found (wallet_v2.json or wallet.json) — cannot start testnet node")
        
        # Load wallet based on version
        if wallet_version in (2, 3):
            # v2 wallet: Use TIMPAL_WALLET_PASSWORD for decryption
            wallet_password = os.environ.get("TIMPAL_WALLET_PASSWORD")
            if not wallet_password:
                raise ValueError("TIMPAL_WALLET_PASSWORD environment variable not set — cannot decrypt wallet")

            if wallet_version == 3:
                from app.metawallet import MultiWallet
                mw = MultiWallet(wallet_path)
                mw.load(wallet_password)
                vault_id = os.getenv("TIMPAL_WALLET_ID") or mw.default_vault_id
                acct_index = int(os.getenv("TIMPAL_WALLET_ACCOUNT", "0"))
                addr, public_key, private_key = mw.export_account_private_key(wallet_password, vault_id=vault_id, index=acct_index)
                reward_address = addr
            else:
                temp_wallet = SeedWallet(wallet_path)
                temp_wallet.load_wallet(password=wallet_password)
                account = temp_wallet.get_account(0)
                private_key = account["private_key"]
                public_key = account["public_key"]
                reward_address = account["address"]
        else:
            # v1 wallet: Use TIMPAL_WALLET_PIN for decryption (legacy behavior)
            wallet_pin = os.environ.get("TIMPAL_WALLET_PIN")
            if not wallet_pin:
                raise ValueError("TIMPAL_WALLET_PIN environment variable not set — cannot decrypt v1 wallet")
            
            temp_wallet = Wallet(wallet_path)
            if not temp_wallet.load_wallet(wallet_pin):
                raise ValueError(f"Failed to decrypt {wallet_path} — wrong PIN or corrupted file")
            private_key = temp_wallet.private_key
            public_key = temp_wallet.public_key
            reward_address = temp_wallet.address
        
        print(f"[TESTNET] Loaded validator key from {wallet_path}")
        print(f"[TESTNET] Address: {reward_address}")
        
        # SECURITY: DO NOT overwrite config.GENESIS_VALIDATORS
        # All nodes MUST use the same static GENESIS_VALIDATORS from config
        # to ensure canonical genesis block hash validation works
        if not seed_nodes:
            print(f"[TESTNET] Bootstrap mode: Using genesis validators from config")
            print(f"  Genesis validators: {list(config.GENESIS_VALIDATORS.keys())}")
        else:
            print(f"[TESTNET] Network mode: Will sync from {len(seed_nodes)} seed node(s)")
        
        ledger_data_dir = os.path.join(self.data_dir, "ledger")
        
        # -----------------------------------------------
        # CRITICAL: Only pre-initialize genesis validator for BOOTSTRAP nodes
        # Non-bootstrap nodes (with --seed) should sync validator registry from network
        # -----------------------------------------------
        if not seed_nodes:
            # BOOTSTRAP MODE: This is the genesis node, pre-initialize validator
            from ledger import Ledger
            
            print(f"🔧 [BOOTSTRAP] Pre-initializing ledger with genesis validator...")
            temp_ledger = Ledger(data_dir=ledger_data_dir, use_production_storage=True)
            
            # Add genesis validator to registry
            temp_ledger.validator_registry[reward_address] = {
                "public_key": public_key,
                "stake": 0,
                "device_id": "genesis",
                "registered_at": 0,
                "status": "active"
            }
            
            # Save ledger state to disk
            temp_ledger.save_state()
            print(f"✅ [BOOTSTRAP] Genesis validator written to ledger at {ledger_data_dir}")
            
            # Clean up temp ledger reference
            del temp_ledger
        else:
            # NETWORK MODE: This node will sync from seed nodes
            print(f"🌐 [NETWORK] Skipping genesis pre-initialization (will sync from seeds)")
        
        # Create node with loaded validator keys
        # SECURITY: Device check enforces 1 node per device (Sybil prevention)
        # skip_device_check can be used for testing multiple nodes on same machine
        self.node = Node(
            skip_device_check=self.skip_device_check,
            reward_address=reward_address,
            private_key=private_key,
            public_key=public_key,
            p2p_port=port,
            data_dir=ledger_data_dir,
            use_production_storage=True,
            testnet_mode=True,
            is_genesis_node=is_genesis_node  # Only genesis node creates block 0 locally
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
        import config_testnet
        
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
                    amount_picotokens = int(amount_float * config_testnet.PALS_PER_TMPL)
                    
                except (ValueError, TypeError):
                    return web.json_response({
                        'status': 'error',
                        'message': 'Invalid amount format'
                    }, status=400)
                
                # Load wallet for this sender (support both v2 and v1)
                if os.path.exists("wallet_v2.json"):
                    wallet_path = "wallet_v2.json"
                    wallet_version = 2
                elif os.path.exists("wallet.json"):
                    wallet_path = "wallet.json"
                    wallet_version = 1
                else:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Wallet not found for this address'
                    }, status=404)
                
                # Load and decrypt wallet based on version
                try:
                    if wallet_version == 2:
                        temp_wallet = SeedWallet(wallet_path)
                        temp_wallet.load_wallet(password=pin)
                        account = temp_wallet.get_account(0)
                        wallet_address = account["address"]
                        wallet_public_key = account["public_key"]
                        wallet_private_key = account["private_key"]
                    else:
                        temp_wallet = Wallet(wallet_path)
                        if not temp_wallet.load_wallet(pin):
                            return web.json_response({
                                'status': 'error',
                                'message': 'Invalid PIN or wallet decryption failed'
                            }, status=401)
                        wallet_address = temp_wallet.address
                        wallet_public_key = temp_wallet.public_key
                        wallet_private_key = temp_wallet.private_key
                except Exception as e:
                    return web.json_response({
                        'status': 'error',
                        'message': f'Invalid PIN or wallet decryption failed: {str(e)}'
                    }, status=401)
                
                # Verify wallet address matches sender
                if wallet_address != sender:
                    return web.json_response({
                        'status': 'error',
                        'message': 'Wallet address does not match sender address'
                    }, status=400)
                
                # Check balance
                balance = self.node.ledger.get_balance(sender)
                fee = config_testnet.FEE  # Fixed 0.0005 fee
                total_required = amount_picotokens + fee
                
                if balance < total_required:
                    balance_tmpl = balance / config_testnet.PICOTOKEN
                    required_tmpl = total_required / config_testnet.PICOTOKEN
                    return web.json_response({
                        'status': 'error',
                        'message': f'Insufficient balance. Have: {balance_tmpl:.6f} {config_testnet.SYMBOL}, Need: {required_tmpl:.6f} {config_testnet.SYMBOL} (including 0.0005 {config_testnet.SYMBOL} fee)'
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
                    public_key=wallet_public_key,
                    tx_type='transfer'
                )
                
                # Sign transaction
                tx.sign(wallet_private_key)
                
                # Clear sensitive data
                del wallet_private_key
                del wallet_public_key
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
        
        async def get_blockchain_info(request):
            """Get blockchain info for peer sync validation"""
            try:
                latest_block = self.node.ledger.get_latest_block()
                
                return web.json_response({
                    'height': latest_block.height if latest_block else 0,
                    'blocks': len(self.node.ledger.blocks)
                })
                
            except Exception as e:
                return web.json_response({
                    'error': str(e)
                }, status=500)
        
        app = web.Application()
        app.router.add_post('/send', send_transaction)  # User-friendly endpoint
        app.router.add_post('/submit_transaction', submit_transaction)  # Pre-signed transactions
        app.router.add_get('/api/blocks/range', get_blocks_range)
        app.router.add_get('/api/blockchain/info', get_blockchain_info)
        app.router.add_get('/api/health', get_health)
        app.router.add_get('/api/account/{address}', get_account)
        
        # Run on port 9001 (P2P is on 9000)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port + 1)
        await site.start()
        
        print(f"📡 HTTP API running on port {self.port + 1}")
    
    async def start(self):
        """Start the testnet node"""
        print("\n" + "="*80)
        print("TIMPAL TESTNET NODE")
        print("="*80)
        
        # CRITICAL: Check for genesis mismatch BEFORE doing anything else
        # If local genesis doesn't match canonical, purge and restart fresh
        if self.seed_nodes and hasattr(config, 'CANONICAL_GENESIS_HASH') and config.CANONICAL_GENESIS_HASH:
            genesis_block = self.node.ledger.get_block_by_height(0)
            if genesis_block:
                local_genesis_hash = genesis_block.block_hash
                canonical_hash = config.CANONICAL_GENESIS_HASH
                
                if local_genesis_hash != canonical_hash:
                    print("\n" + "!"*80)
                    print("🚨 GENESIS MISMATCH DETECTED - AUTO-RECOVERY")
                    print("!"*80)
                    print(f"   Local genesis:     {local_genesis_hash[:16]}...")
                    print(f"   Canonical genesis: {canonical_hash[:16]}...")
                    print(f"\n   This node was created with wrong genesis.")
                    print(f"   Purging local chain and resyncing from network...")
                    
                    # Purge the ledger subdirectory (where actual blockchain data lives)
                    import shutil
                    data_path = Path(self.data_dir)
                    ledger_path = data_path / "ledger"
                    
                    if ledger_path.exists():
                        shutil.rmtree(ledger_path)
                        print(f"   ✅ Ledger directory purged: {ledger_path}")
                    
                    # Also clean any top-level db files just in case
                    for f in data_path.glob("*.db"):
                        f.unlink()
                    for f in data_path.glob("*.json"):
                        if "wallet" not in f.name.lower():  # Preserve wallet
                            f.unlink()
                    
                    print(f"   🔄 Reinitializing ledger...")
                    
                    # Reinitialize the ledger in correct subdirectory
                    ledger_dir = os.path.join(self.data_dir, "ledger")
                    self.node.ledger = Ledger(ledger_dir, use_production_storage=True)
                    
                    print(f"   ✅ Fresh ledger ready - will sync from network")
                    print("!"*80 + "\n")
        
        print(f"\n⚙️  Configuration:")
        print(f"   Network: {config_testnet.CHAIN_ID}")
        print(f"   Symbol: {config_testnet.SYMBOL}")
        print(f"   Port: {self.port}")
        print(f"   Data directory: {self.data_dir}")
        print(f"   Validator address: {self.node.reward_address}")
        print(f"   Seed nodes: {len(self.seed_nodes)}")
        
        if self.seed_nodes:
            for seed in self.seed_nodes:
                print(f"      - {seed}")
        else:
            print(f"      (No seed nodes - this is a bootstrap node)")
        
        print(f"\n🚀 Starting node...")
        
        # Start HTTP API server first (needed for other nodes to sync from us)
        asyncio.create_task(self.run_http_api())
        
        # CRITICAL: If we have seed nodes, do initial HTTP sync BEFORE anything else
        # This prevents creating a separate chain when we should join the existing network
        if self.seed_nodes:
            print(f"\n🔄 INITIAL SYNC: Syncing from network before joining...")
            
            # Build HTTP URLs from seed nodes
            http_urls = []
            for seed in self.seed_nodes:
                if seed.startswith('wss://'):
                    # Secure WebSocket -> HTTPS
                    # wss://host:port -> https://host:port+1
                    host_port = seed.replace('wss://', '').replace('/', '')
                    if ':' in host_port:
                        host, port_str = host_port.rsplit(':', 1)
                        try:
                            http_port = int(port_str) + 1
                            http_urls.append(f"https://{host}:{http_port}")
                        except ValueError:
                            pass
                elif seed.startswith('ws://'):
                    # Plain WebSocket -> HTTP
                    # ws://host:port -> http://host:port+1
                    host_port = seed.replace('ws://', '').replace('/', '')
                    if ':' in host_port:
                        host, port_str = host_port.rsplit(':', 1)
                        try:
                            http_port = int(port_str) + 1
                            http_urls.append(f"http://{host}:{http_port}")
                        except ValueError:
                            pass
            
            # Also use HTTP_SEEDS from config
            if hasattr(config, 'HTTP_SEEDS') and config.HTTP_SEEDS:
                for url in config.HTTP_SEEDS:
                    if url not in http_urls:
                        http_urls.append(url)
            
            if http_urls:
                # Do initial HTTP batch sync
                sync_success = await self.node.http_batch_sync(http_urls)
                if sync_success:
                    current_height = self.node.ledger.get_block_count() - 1
                    print(f"✅ INITIAL SYNC COMPLETE: Synced to height {current_height}")
                    # Do NOT set self.node.synced = True here - let mine_blocks verify against peers
                    # and enforce cooling period before allowing block production
                    print(f"   Will verify sync status and start cooling period in mining loop")
                else:
                    print(f"⚠️  Initial sync incomplete, will continue syncing via P2P")
            else:
                print(f"⚠️  No HTTP endpoints available for initial sync")
        else:
            print(f"🔥 BOOTSTRAP MODE: No seed nodes, creating new chain")
        
        # Run node as background task so status loop can start
        asyncio.create_task(self.node.start())
        
        # Start status loop task
        asyncio.create_task(self.status_loop())
        
        # Start block production loop (TESTNET ONLY)
        # This now runs AFTER initial sync is complete
        asyncio.create_task(self.node.mine_blocks())
        
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
    parser = argparse.ArgumentParser(
        description="Run a TIMPAL testnet validator node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Genesis node (starts new testnet - creates genesis block):
    python run_testnet_node.py --port 9000 --genesis
  
  Validator node (joins existing testnet - syncs genesis from network):
    python run_testnet_node.py --port 8001 --seed ws://SEED_NODE_IP:9000
    python run_testnet_node.py --port 8002 --seed ws://SEED_NODE_IP:9000
  
IMPORTANT:
   - Only use --genesis for the FIRST node starting a new testnet
   - All other nodes MUST use --seed (syncs block 0 from network)
   - Using --genesis when joining will create a fork!
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
        help="Data directory for blockchain (default: ~/.timpal/testnet_data_node_PORT). "
             "Can also set TIMPAL_DATA_PATH env var for custom base directory."
    )
    
    parser.add_argument(
        "--seed",
        type=str,
        action="append",
        dest="seeds",
        help="Seed node address (can be specified multiple times). Example: ws://192.168.1.10:9000"
    )
    
    parser.add_argument(
        "--reset",
        action="store_true",
        help="🚨 TESTNET ONLY: Delete all blockchain data and start fresh from block 0. Requires confirmation."
    )
    
    parser.add_argument(
        "--genesis",
        action="store_true",
        help="🔥 GENESIS NODE ONLY: This node creates the genesis block locally. Only use for the VPS bootstrap node!"
    )
    
    parser.add_argument(
        "--skip-device-check",
        action="store_true",
        help="🧪 TESTING ONLY: Skip device fingerprint check to allow multiple nodes on same machine for testing"
    )
    
    args = parser.parse_args()
    
    # ==========================================
    # TESTNET RESET: Delete blockchain data and start fresh
    # ==========================================
    if args.reset:
        import shutil
        
        # Use the same resolve_data_dir function to ensure we delete the correct directory
        data_dir = resolve_data_dir(args.port, args.data_dir)
        
        # CRITICAL SAFETY: Only allow deleting testnet directories
        # This prevents accidental deletion of mainnet or system directories
        abs_data_dir = os.path.abspath(data_dir)
        dir_name = os.path.basename(abs_data_dir)
        
        # Check 1: Directory name MUST start with "testnet_data_node_"
        if not dir_name.startswith("testnet_data_node_"):
            print("\n" + "="*80)
            print("🔒 MAINNET PROTECTION: RESET BLOCKED")
            print("="*80)
            print(f"\n❌ ERROR: Cannot reset directory: {abs_data_dir}")
            print(f"\nReset is ONLY allowed for testnet directories matching:")
            print(f"   testnet_data_node_<PORT>")
            print(f"\nYour directory: {dir_name}")
            print(f"This does NOT match the required pattern.")
            print(f"\n🔒 This safety check protects mainnet data from accidental deletion.")
            print(f"   Mainnet directories: mainnet_data_node_*")
            print(f"   Testnet directories: testnet_data_node_*")
            sys.exit(1)
        
        # Check 2: Resolve symlinks and prevent path traversal attacks
        try:
            real_path = os.path.realpath(abs_data_dir)
            real_dir_name = os.path.basename(real_path)
            
            # After resolving symlinks, name must still match testnet pattern
            if not real_dir_name.startswith("testnet_data_node_"):
                print("\n" + "="*80)
                print("🔒 SYMLINK ATTACK DETECTED: RESET BLOCKED")
                print("="*80)
                print(f"\n❌ ERROR: Symlink resolves to non-testnet directory")
                print(f"   Provided path: {abs_data_dir}")
                print(f"   Resolves to: {real_path}")
                print(f"\nSymlinks pointing to mainnet or system directories are not allowed.")
                sys.exit(1)
        except Exception as e:
            print(f"\n❌ ERROR: Failed to validate directory path: {e}")
            sys.exit(1)
        
        # Validation passed - show warning and get confirmation
        print("\n" + "="*80)
        print("🚨 TESTNET RESET WARNING")
        print("="*80)
        print(f"\nYou are about to DELETE all blockchain data in:")
        print(f"   {abs_data_dir}")
        print(f"\nThis will:")
        print(f"   ✗ Delete all blocks ({os.path.exists(data_dir) and 'EXISTS' or 'does not exist yet'})")
        print(f"   ✗ Delete all transactions")
        print(f"   ✗ Delete all account balances")
        print(f"   ✗ Delete finality checkpoints")
        print(f"   ✓ Keep wallet.json (validator keys are safe)")
        print(f"\n⚠️  This operation CANNOT be undone!")
        print(f"⚠️  This is TESTNET ONLY - mainnet data is protected")
        print(f"\nType 'DELETE' (all caps) to confirm, or anything else to cancel: ", end="")
        
        confirmation = input().strip()
        
        if confirmation != "DELETE":
            print("❌ Reset cancelled - no data was deleted")
            sys.exit(0)
        
        # User confirmed - proceed with reset
        if os.path.exists(data_dir):
            try:
                shutil.rmtree(data_dir)
                print(f"\n✅ Deleted: {data_dir}")
                print(f"✅ Testnet reset complete - starting fresh from block 0\n")
            except Exception as e:
                print(f"\n❌ ERROR: Failed to delete {data_dir}: {e}")
                sys.exit(1)
        else:
            print(f"\n⚠️  Directory {data_dir} does not exist (already clean)")
            print(f"✅ Starting fresh from block 0\n")
    
    # VALIDATION: Port 9000 with seeds is unusual but allowed (might be connecting to another seed)
    if args.port == 9000 and args.seeds and not args.genesis:
        print("\n⚠️  Note: Port 9000 is typically used for genesis/seed nodes.")
        print("   You're connecting to a seed node, which is fine for joining.")
    
    # WARNING: If no seeds provided and not genesis, user might be creating a private chain accidentally
    if not args.seeds and args.port != 9000 and not args.genesis:
        print("\n⚠️  WARNING: No --seed flag detected!")
        print("   You are starting a standalone node that will create a new chain.")
        print("   If you meant to join an existing testnet, add:")
        print("   --seed ws://SEED_NODE_IP:9000")
        print("\n   Continue anyway? (y/N): ", end="")
        response = input().strip().lower()
        if response != 'y':
            print("   Aborted. Restart with --seed flag to join the testnet.")
            sys.exit(0)
    
    # CRITICAL FIX: Normalize seed URLs to prevent "nodename nor servname provided" errors
    # This error occurs when URLs have trailing whitespace, newlines, or invisible characters
    # from copy/paste. We strip whitespace and filter empty entries.
    raw_seeds = args.seeds or []
    seed_nodes = [s.strip() for s in raw_seeds if s and s.strip()]
    
    # Log normalized seeds for debugging (using repr to show invisible characters)
    if raw_seeds:
        print(f"🌐 CLI seeds (raw): {raw_seeds!r}")
        print(f"🌐 CLI seeds (normalized): {seed_nodes!r}")
    
    # CRITICAL: is_genesis_node controls whether node creates genesis locally
    # Only VPS bootstrap node should use --genesis flag
    if args.genesis:
        print("\n🔥 GENESIS NODE MODE: This node will create the genesis block locally")
        if seed_nodes:
            print("⚠️  WARNING: Genesis nodes should NOT have seed nodes!")
            print("   Genesis node IS the seed for other nodes to connect to.")
    else:
        print("\n🌐 NETWORK NODE MODE: This node will sync genesis from network")
    
    node = TestnetNode(
        port=args.port,
        data_dir=args.data_dir,
        seed_nodes=seed_nodes,
        is_genesis_node=args.genesis,
        skip_device_check=args.skip_device_check
    )
    
    try:
        asyncio.run(node.start())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


if __name__ == "__main__":
    main()
