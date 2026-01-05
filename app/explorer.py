import sys
import os
import uvicorn
import asyncio
import aiohttp
import argparse
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import List, Dict, Any, Optional, Callable
from app.ledger import Ledger
from app.transaction import Transaction
from app.wallet import Wallet
from app.explorer_assets import (
    get_base_styles, get_chart_js_cdn, get_vis_js_cdn,
    get_theme_toggle_script, get_live_updates_script, get_navigation_html
)
import app.config_testnet as config
import time
import json

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="TIMPAL Block Explorer", version="1.0.0")
app.state.limiter = limiter

# Mount static files directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Use built-in rate limit handler (type-safe)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

ALLOWED_ORIGINS = [
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://0.0.0.0:5000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

_ledger_cache = None
_ledger_cache_time = 0
CACHE_TTL = 5

_stats_cache: Dict[str, Any] = {}
_stats_cache_height = -1

_validator_stats_cache: Dict[str, Any] = {}
_validator_stats_cache_height = -1


def get_cached_validator_stats(ledger: Ledger) -> Dict[str, Dict[str, Any]]:
    """
    Get cached validator statistics with height-based invalidation.
    
    STABILITY OPTIMIZATION: Instead of iterating through all blocks for each validator
    (O(v*b) complexity), we compute all validator stats in a single pass (O(b) complexity)
    and cache the results. This dramatically reduces CPU usage for the validators dashboard.
    """
    global _validator_stats_cache, _validator_stats_cache_height
    
    current_height = ledger.get_block_count() - 1
    
    if current_height != _validator_stats_cache_height or not _validator_stats_cache:
        validator_stats: Dict[str, Dict[str, Any]] = {}
        
        for block in ledger.blocks:
            proposer = block.proposer
            if proposer:
                if proposer not in validator_stats:
                    validator_stats[proposer] = {
                        'blocks_proposed': 0,
                        'total_rewards': 0,
                        'last_block_height': -1
                    }
                validator_stats[proposer]['blocks_proposed'] += 1
                validator_stats[proposer]['last_block_height'] = block.height
            
            if block.reward_allocations:
                for address, reward in block.reward_allocations.items():
                    if address not in validator_stats:
                        validator_stats[address] = {
                            'blocks_proposed': 0,
                            'total_rewards': 0,
                            'last_block_height': -1
                        }
                    validator_stats[address]['total_rewards'] += reward
        
        _validator_stats_cache = validator_stats
        _validator_stats_cache_height = current_height
    
    return _validator_stats_cache


def get_cached_stats(ledger: Ledger) -> Dict[str, Any]:
    """
    Get cached blockchain statistics with height-based invalidation.
    
    STABILITY OPTIMIZATION: Instead of scanning all blocks on every request,
    we cache computed stats and only recompute when block height changes.
    This reduces CPU usage from O(n*m) to O(1) for most requests.
    """
    global _stats_cache, _stats_cache_height
    
    current_height = ledger.get_block_count() - 1
    
    if current_height != _stats_cache_height or not _stats_cache:
        transfer_count = 0
        total_transactions = 0
        total_fees = 0
        active_addresses: set = set()
        tx_by_type: Dict[str, int] = {
            'transfer': 0,
            'validator_registration': 0,
            'validator_heartbeat': 0,
            'epoch_attestation': 0,
            'genesis_reward': 0
        }
        
        for block in ledger.blocks:
            for tx in block.transactions:
                tx_type = tx.tx_type
                if tx_type in tx_by_type:
                    tx_by_type[tx_type] += 1
                
                if tx_type == 'transfer':
                    transfer_count += 1
                
                if tx_type != 'validator_heartbeat':
                    total_transactions += 1
                
                total_fees += tx.fee
                active_addresses.add(tx.sender)
                if tx.recipient:
                    active_addresses.add(tx.recipient)
        
        _stats_cache = {
            'transfer_count': transfer_count,
            'total_transactions': total_transactions,
            'total_fees': total_fees,
            'active_addresses_count': len(active_addresses),
            'tx_by_type': tx_by_type,
            'height': current_height
        }
        _stats_cache_height = current_height
    
    return _stats_cache


def get_ledger() -> Ledger:
    """Get ledger instance with 5-second caching for performance"""
    global _ledger_cache, _ledger_cache_time
    
    # Auto-detect local node data directory or use environment variable
    import os
    import glob
    
    data_dir = os.getenv("EXPLORER_DATA_DIR")
    
    if not data_dir:
        # Auto-detect: find any testnet_data_node_* directory with blockchain data
        # Check both the new default location (~/.timpal/) and legacy location (project dir)
        from pathlib import Path
        
        node_dirs = []
        
        # Check new default location: ~/.timpal/testnet_data_node_*/ledger
        timpal_home = Path.home() / ".timpal"
        if timpal_home.exists():
            node_dirs.extend(glob.glob(str(timpal_home / "testnet_data_node_*/ledger")))
        
        # Also check legacy location: ./testnet_data_node_*/ledger (for backwards compatibility)
        node_dirs.extend(glob.glob("testnet_data_node_*/ledger"))
        
        if node_dirs:
            # Prefer the most recently modified directory (active node)
            data_dir = sorted(node_dirs, key=lambda x: os.path.getmtime(x), reverse=True)[0]
            print(f"📊 Explorer auto-detected blockchain data: {data_dir}")
        else:
            # Fallback to new default location (VPS genesis node)
            data_dir = str(Path.home() / ".timpal" / "testnet_data_node_9000" / "ledger")
            print(f"⚠️  No blockchain data found, using default: {data_dir}")
    
    current_time = time.time()
    if _ledger_cache is None or (current_time - _ledger_cache_time) > CACHE_TTL:
        _ledger_cache = Ledger(data_dir=data_dir, use_production_storage=True, read_only=True)
        _ledger_cache_time = current_time
    
    return _ledger_cache


def format_pals(pals: int) -> str:
    """Convert pals to TMPL format (1 TMPL = 100,000,000 pals)"""
    tmpl = pals / config.PALS_PER_TMPL
    return f"{tmpl:.8f} {config.SYMBOL}"


def get_transaction_stats(ledger: Ledger, hide_legacy: bool = True) -> Dict[str, int]:
    """Get transaction counts by type
    
    Args:
        ledger: Ledger instance
        hide_legacy: If True, exclude legacy validator_heartbeat from total count
    
    Returns:
        Dict with transaction counts by type
    """
    stats = {
        'total': 0,
        'transfer': 0,
        'validator_registration': 0,
        'validator_heartbeat': 0,
        'epoch_attestation': 0,
        'genesis_reward': 0
    }
    
    for block in ledger.blocks:
        for tx in block.transactions:
            tx_type = tx.tx_type
            if tx_type in stats:
                stats[tx_type] += 1
            
            # Only count in total if not hiding legacy or not a heartbeat
            if not hide_legacy or tx_type != 'validator_heartbeat':
                stats['total'] += 1
    
    return stats


def get_all_transactions(ledger: Ledger, page: int = 1, page_size: int = 50, tx_filter: Optional[str] = None, hide_legacy: bool = True) -> Dict[str, Any]:
    """Get paginated transactions with filtering (optimized to avoid full scan)
    
    Args:
        ledger: Ledger instance
        page: Page number (1-indexed)
        page_size: Number of transactions per page (max 100)
        tx_filter: Filter by transaction type (None = all types)
        hide_legacy: Hide legacy validator_heartbeat transactions
    
    Returns:
        Dict with transactions, pagination info, and stats
    """
    # Cap page size
    page_size = min(page_size, 100)
    
    # Calculate total count first (single pass)
    total_count = 0
    for block in ledger.blocks:
        for tx in block.transactions:
            # Skip legacy heartbeats if requested
            if hide_legacy and tx.tx_type == 'validator_heartbeat':
                continue
            # Apply filter if specified
            if tx_filter and tx.tx_type != tx_filter:
                continue
            total_count += 1
    
    # Calculate pagination bounds
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    page = max(1, min(page, total_pages))
    
    # Calculate how many transactions to skip and collect
    skip_count = (page - 1) * page_size
    collect_count = page_size
    
    # Collect only the transactions needed for this page (newest first)
    page_txs = []
    skipped = 0
    collected = 0
    
    for block in reversed(ledger.blocks):
        if collected >= collect_count:
            break
            
        for tx in block.transactions:
            # Skip legacy heartbeats if requested
            if hide_legacy and tx.tx_type == 'validator_heartbeat':
                continue
            
            # Apply filter if specified
            if tx_filter and tx.tx_type != tx_filter:
                continue
            
            # Skip until we reach the page offset
            if skipped < skip_count:
                skipped += 1
                continue
            
            # Collect transaction data
            if collected < collect_count:
                tx_data = tx.to_dict()
                tx_data['block_height'] = block.height
                tx_data['block_hash'] = block.block_hash
                tx_data['timestamp'] = block.timestamp
                page_txs.append(tx_data)
                collected += 1
                
                if collected >= collect_count:
                    break
    
    return {
        'transactions': page_txs,
        'page': page,
        'page_size': page_size,
        'total_count': total_count,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'has_prev': page > 1
    }


def get_address_transactions(ledger: Ledger, address: str) -> List[Dict[str, Any]]:
    """Get all transfer transactions involving an address (excludes heartbeats and registrations)"""
    transactions = []
    
    for block in ledger.blocks:
        for tx in block.transactions:
            # Only include transfer transactions (exclude heartbeats and registrations)
            if (tx.sender == address or tx.recipient == address) and tx.tx_type == "transfer":
                tx_data = tx.to_dict()
                tx_data['block_height'] = block.height
                tx_data['block_hash'] = block.block_hash
                tx_data['timestamp'] = block.timestamp
                transactions.append(tx_data)
    
    return sorted(transactions, key=lambda x: x['timestamp'], reverse=True)


@app.get("/", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def root(request: Request):
    """Explorer homepage with navigation and live updates"""
    ledger = get_ledger()
    latest_block = ledger.get_latest_block()
    total_supply = ledger.total_emitted_pals
    validator_count = ledger.get_validator_count()
    # Get transaction statistics (hide legacy heartbeats by default for cleaner stats)
    tx_stats = get_transaction_stats(ledger, hide_legacy=True)
    total_transactions = tx_stats['total']
    transfer_count = tx_stats['transfer']
    
    # Get configured API port for display
    import os
    node_api_port = os.getenv("EXPLORER_API_PORT", "9001")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
        <meta http-equiv="Pragma" content="no-cache">
        <meta http-equiv="Expires" content="0">
        <title>TIMPAL Block Explorer</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
        {get_live_updates_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        
        <div class="header">
            <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 10px;">
                <img src="/static/timpal-logo.png" alt="TIMPAL Logo" style="width: 80px; height: 80px; border-radius: 50%; box-shadow: 0 4px 8px rgba(0,0,0,0.2); background: white; border: 3px solid rgba(255,255,255,0.3);" />
                <h1 style="margin: 0;">⛓️ TIMPAL Block Explorer</h1>
            </div>
            <p><span class="live-indicator"></span> Fully decentralized blockchain with real-time updates</p>
            <div style="font-size: 0.85em; opacity: 0.9; margin-top: 10px;">
                📡 Connected to Node API: <code style="background: rgba(255,255,255,0.2); padding: 2px 8px; border-radius: 3px;">localhost:{node_api_port}</code>
                {' <span style="color: #ffd700;">⚠️ Using default port - set EXPLORER_API_PORT if your node is on a different port</span>' if node_api_port == '9001' else ''}
            </div>
        </div>
        
        {get_navigation_html("home")}
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-label">Latest Block</div>
                <div class="stat-value" id="live-block-height">#{latest_block.height if latest_block else 0}</div>
                <div class="stat-trend">⚡ Real-time updates</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Supply</div>
                <div class="stat-value" id="live-total-supply">{format_pals(total_supply)}</div>
                <div class="stat-trend">{(total_supply / config.MAX_SUPPLY_PALS * 100):.2f}% mined</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Validators</div>
                <div class="stat-value" id="live-validator-count">{validator_count}</div>
                <div class="stat-trend">🌐 Decentralized</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">{config.SYMBOL} Transfers</div>
                <div class="stat-value" id="live-tx-count">{transfer_count:,}</div>
                <div class="stat-trend">💸 Coin transactions only</div>
            </div>
        </div>
        
        <div class="card">
            <h2>🔍 Search Blockchain</h2>
            <p style="color: var(--text-secondary); margin-bottom: 15px;">Search for transactions, addresses, blocks, or block hashes</p>
            <form action="/search-redirect" method="get" style="display: flex; gap: 10px;">
                <input 
                    type="text" 
                    name="q" 
                    placeholder="Enter transaction hash, address (tmpl...), block number, or block hash..." 
                    required
                    style="flex: 1; padding: 12px 15px; border: 2px solid var(--border-color); border-radius: 5px; font-size: 1em; font-family: monospace; background: var(--bg-color); color: var(--text-color);"
                />
                <button 
                    type="submit" 
                    style="padding: 12px 30px; background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end)); color: white; border: none; border-radius: 5px; font-weight: bold; cursor: pointer; font-size: 1em;"
                >
                    Search
                </button>
            </form>
            <div style="margin-top: 10px; font-size: 0.85em; color: var(--text-secondary);">
                <strong>Examples:</strong>
                <a href="/search/100" style="margin: 0 8px;">Block #100</a> |
                <a href="/search/51966acccd7fe8a876c30ca8c8e7323f4273bcd96660ba7c07aaf55e5d30d6f2" style="margin: 0 8px;">Transaction Hash</a> |
                <a href="/search/tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b" style="margin: 0 8px;">Address</a>
            </div>
        </div>
        
        <div class="grid-2">
            <div class="card">
                <h2>🚀 Quick Actions</h2>
                <div style="display: grid; gap: 10px;">
                    <a href="/validators-dashboard" style="display: block; padding: 15px; background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end)); color: white; border-radius: 5px; text-align: center; font-weight: bold;">
                        🛡️ View Validator Dashboard
                    </a>
                    <a href="/analytics" style="display: block; padding: 15px; background: linear-gradient(135deg, #10b981, #059669); color: white; border-radius: 5px; text-align: center; font-weight: bold;">
                        📈 Analytics & Charts
                    </a>
                    <a href="/network" style="display: block; padding: 15px; background: linear-gradient(135deg, #f59e0b, #d97706); color: white; border-radius: 5px; text-align: center; font-weight: bold;">
                        🌐 Network Visualization
                    </a>
                    <a href="/api-docs" style="display: block; padding: 15px; background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; border-radius: 5px; text-align: center; font-weight: bold;">
                        📚 API Documentation
                    </a>
                </div>
            </div>
            
            <div class="card">
                <h2>💡 Features</h2>
                <ul style="line-height: 1.8;">
                    <li><strong>Real-Time Updates:</strong> Server-Sent Events (SSE) for live blockchain data</li>
                    <li><strong>Validator Dashboard:</strong> Leaderboard, stats, and performance metrics</li>
                    <li><strong>Interactive Charts:</strong> Supply curve, transaction volume, emission schedule</li>
                    <li><strong>Network Graph:</strong> Visualize validator topology with Vis.js</li>
                    <li><strong>RESTful API:</strong> JSON endpoints for external integrations</li>
                    <li><strong>Dark/Light Theme:</strong> Toggle theme with 🌓 button (top-right)</li>
                </ul>
            </div>
        </div>
        
        <div class="card">
            <h2>📡 API Endpoints</h2>
            <table class="table">
                <thead>
                    <tr>
                        <th>Endpoint</th>
                        <th>Description</th>
                        <th>Rate Limit</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><code class="monospace"><a href="/blocks">GET /blocks</a></code></td>
                        <td>List recent blocks (paginated)</td>
                        <td><span class="badge badge-info">20/min</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/blocks/1">GET /blocks/{{height}}</a></code></td>
                        <td>Get specific block by height</td>
                        <td><span class="badge badge-info">20/min</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/tx/51966acccd7fe8a876c30ca8c8e7323f4273bcd96660ba7c07aaf55e5d30d6f2">GET /tx/{{hash}}</a></code></td>
                        <td>Get transaction details</td>
                        <td><span class="badge badge-info">20/min</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/address/tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b">GET /address/{{address}}</a></code></td>
                        <td>Get address balance & history</td>
                        <td><span class="badge badge-warning">15/min</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/stats">GET /stats</a></code></td>
                        <td>Get blockchain statistics</td>
                        <td><span class="badge badge-success">30/min</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/validators">GET /validators</a></code></td>
                        <td>Get all validators (JSON)</td>
                        <td><span class="badge badge-success">30/min</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/stream" target="_blank">GET /stream</a></code></td>
                        <td>Real-time updates (SSE)</td>
                        <td><span class="badge badge-success">Unlimited</span></td>
                    </tr>
                    <tr>
                        <td><code class="monospace"><a href="/search/100">GET /search/{{query}}</a></code></td>
                        <td>Universal search</td>
                        <td><span class="badge badge-warning">10/min</span></td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>🔒 Security Features</h2>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px;">
                <div style="padding: 15px; background: var(--bg-color); border-radius: 5px;">
                    <h3 style="margin-top: 0;">🛡️ On-Chain Registration</h3>
                    <p style="color: var(--text-secondary); margin: 0;">Validators register via blockchain transactions for network-wide consensus</p>
                </div>
                <div style="padding: 15px; background: var(--bg-color); border-radius: 5px;">
                    <h3 style="margin-top: 0;">🔐 Sybil Prevention</h3>
                    <p style="color: var(--text-secondary); margin: 0;">3-layer defense with device fingerprint uniqueness</p>
                </div>
                <div style="padding: 15px; background: var(--bg-color); border-radius: 5px;">
                    <h3 style="margin-top: 0;">⚡ Real-Time Security</h3>
                    <p style="color: var(--text-secondary); margin: 0;">Rate limiting, CORS protection, and input validation</p>
                </div>
                <div style="padding: 15px; background: var(--bg-color); border-radius: 5px;">
                    <h3 style="margin-top: 0;">🌐 100% Decentralized</h3>
                    <p style="color: var(--text-secondary); margin: 0;">Permissionless - anyone can join and earn rewards</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html


@app.get("/blocks", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def get_blocks(request: Request, limit: int = 50, offset: int = 0):
    """Get list of recent blocks page"""
    from datetime import datetime
    
    if limit > 100:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 100")
    
    ledger = get_ledger()
    total_blocks = len(ledger.blocks)
    start_idx = max(0, total_blocks - offset - limit)
    end_idx = total_blocks - offset
    
    blocks = ledger.blocks[start_idx:end_idx]
    
    # Build block rows
    block_rows = ""
    for block in reversed(blocks):
        timestamp_str = datetime.fromtimestamp(block.timestamp).strftime('%Y-%m-%d %H:%M:%S')
        # Count only transfer transactions (exclude heartbeats and registrations)
        transfer_count = sum(1 for tx in block.transactions if tx.tx_type == "transfer")
        
        # Calculate total reward distributed (base + transaction fees)
        total_distributed = sum(block.reward_allocations.values()) if block.reward_allocations else block.reward
        
        block_rows += f"""
            <tr>
                <td><a href="/blocks/{block.height}" style="font-weight: bold;">#{block.height}</a></td>
                <td style="font-size: 0.85em;">{timestamp_str}</td>
                <td><a href="/address/{block.proposer}" style="font-family: monospace; font-size: 0.85em;">{block.proposer[:12]}...</a></td>
                <td>{transfer_count}</td>
                <td>{format_pals(total_distributed)}</td>
                <td><a href="/blocks/{block.height}" style="font-family: monospace; font-size: 0.85em;">{block.block_hash[:16]}...</a></td>
            </tr>
        """
    
    # Pagination
    current_page = offset // limit
    total_pages = (total_blocks + limit - 1) // limit
    
    prev_link = ""
    next_link = ""
    if offset > 0:
        prev_offset = max(0, offset - limit)
        prev_link = f'<a href="/blocks?limit={limit}&offset={prev_offset}" style="padding: 10px 20px; background: var(--gradient-start); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">← Previous</a>'
    
    if offset + limit < total_blocks:
        next_offset = offset + limit
        next_link = f'<a href="/blocks?limit={limit}&offset={next_offset}" style="padding: 10px 20px; background: var(--gradient-start); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">Next →</a>'
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Recent Blocks - TIMPAL Explorer</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        {get_navigation_html("blocks")}
        
        <div class="card">
            <h1>📦 Recent Blocks</h1>
            <p style="color: var(--text-secondary);">
                Showing blocks {start_idx + 1} to {end_idx} of {total_blocks} total blocks
            </p>
        </div>
        
        <div class="card">
            <table class="table">
                <thead>
                    <tr>
                        <th>Height</th>
                        <th>Timestamp</th>
                        <th>Proposer</th>
                        <th>Txs</th>
                        <th>Reward</th>
                        <th>Block Hash</th>
                    </tr>
                </thead>
                <tbody>
                    {block_rows}
                </tbody>
            </table>
        </div>
        
        <div class="card" style="text-align: center;">
            <div style="display: flex; gap: 10px; justify-content: center; align-items: center;">
                {prev_link}
                <span style="color: var(--text-secondary);">Page {current_page + 1} of {total_pages}</span>
                {next_link}
            </div>
        </div>
        
        <div class="card" style="text-align: center;">
            <a href="/" style="padding: 10px 20px; background: var(--text-secondary); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">🏠 Home</a>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html)


@app.get("/blocks/{height}", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def get_block_by_height(request: Request, height: int):
    """Get specific block details page"""
    from datetime import datetime
    ledger = get_ledger()
    block = ledger.get_block_by_height(height)
    
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    
    # Filter to show only TMPL transfer transactions (exclude heartbeats and registrations)
    transactions = [tx.to_dict() for tx in block.transactions if tx.tx_type == "transfer"]
    timestamp_str = datetime.fromtimestamp(block.timestamp).strftime('%Y-%m-%d %H:%M:%S UTC')
    confirmations = len(ledger.blocks) - block.height
    
    # Calculate total fees in this block (only from transfer transactions)
    total_fees = sum(tx.get('fee', 0) for tx in transactions)
    
    # Calculate reward economics
    base_reward = block.reward  # New coins minted
    total_distributed = sum(block.reward_allocations.values()) if block.reward_allocations else 0
    num_validators = len(block.reward_allocations) if block.reward_allocations else 0
    avg_reward_per_validator = total_distributed // num_validators if num_validators > 0 else 0
    
    # Build transaction section
    if transactions:
        tx_rows = ""
        for tx in transactions:
            recipient_display = tx.get('recipient', 'N/A')
            recipient_short = recipient_display[:12] + "..." if recipient_display and recipient_display != 'N/A' else 'N/A'
            tx_rows += f"""
                    <tr>
                        <td><a href="/tx/{tx['tx_hash']}" style="font-family: monospace; font-size: 0.85em;">{tx['tx_hash'][:16]}...</a></td>
                        <td><a href="/address/{tx['sender']}" style="font-family: monospace; font-size: 0.85em;">{tx['sender'][:12]}...</a></td>
                        <td><a href="/address/{recipient_display}" style="font-family: monospace; font-size: 0.85em;">{recipient_short}</a></td>
                        <td>{format_pals(tx['amount'])}</td>
                        <td>{format_pals(tx['fee'])}</td>
                    </tr>
            """
        tx_section = f"""
            <table class="table">
                <thead>
                    <tr>
                        <th>Transaction Hash</th>
                        <th>From</th>
                        <th>To</th>
                        <th>Amount</th>
                        <th>Fee</th>
                    </tr>
                </thead>
                <tbody>
                    {tx_rows}
                </tbody>
            </table>
        """
    else:
        tx_section = '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No transactions in this block</p>'
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Block #{height} - TIMPAL Explorer</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        {get_navigation_html("blocks")}
        
        <div class="card">
            <h1>📦 Block #{height}</h1>
            <p style="color: var(--text-secondary); word-break: break-all; font-family: monospace; font-size: 0.9em;">
                {block.block_hash}
            </p>
        </div>
        
        <div class="card">
            <h2>📋 Block Details</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>Block Height</strong></td>
                        <td>#{block.height}</td>
                    </tr>
                    <tr>
                        <td><strong>Status</strong></td>
                        <td><span class="badge badge-success">✅ Confirmed</span> ({confirmations} confirmations)</td>
                    </tr>
                    <tr>
                        <td><strong>Timestamp</strong></td>
                        <td>{timestamp_str}</td>
                    </tr>
                    <tr>
                        <td><strong>Proposer</strong></td>
                        <td><a href="/address/{block.proposer}" style="font-family: monospace; font-size: 0.85em;">{block.proposer}</a></td>
                    </tr>
                    <tr>
                        <td><strong>Transactions</strong></td>
                        <td>{len(transactions)} transaction{'s' if len(transactions) != 1 else ''}</td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>💰 Reward Economics</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>Base Block Reward</strong></td>
                        <td><span style="font-size: 1.1em; font-weight: bold; color: var(--gradient-start);">{format_pals(base_reward)}</span></td>
                    </tr>
                    <tr>
                        <td><strong>Transaction Fees Collected</strong></td>
                        <td><span style="font-size: 1.1em; font-weight: bold; color: #2ecc71;">{format_pals(total_fees)}</span></td>
                    </tr>
                    <tr style="border-top: 2px solid var(--text-secondary);">
                        <td><strong>Total Distributed to Validators</strong></td>
                        <td><span style="font-size: 1.2em; font-weight: bold; color: #e74c3c;">{format_pals(total_distributed)}</span></td>
                    </tr>
                    <tr>
                        <td><strong>Number of Validators Rewarded</strong></td>
                        <td>{num_validators}</td>
                    </tr>
                    <tr>
                        <td><strong>Average Reward per Validator</strong></td>
                        <td>{format_pals(avg_reward_per_validator)}</td>
                    </tr>
                    <tr>
                        <td><strong>New {config.SYMBOL} Minted</strong></td>
                        <td><span style="font-weight: bold;">{format_pals(base_reward)}</span> <span style="color: var(--text-secondary); font-size: 0.9em;">(from emission)</span></td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>🔐 Block Hashes</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>Block Hash</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{block.block_hash}</td>
                    </tr>
                    <tr>
                        <td><strong>Previous Hash</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{block.previous_hash}</td>
                    </tr>
                    <tr>
                        <td><strong>Merkle Root</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{block.merkle_root}</td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>💸 {config.SYMBOL} Transfers ({len(transactions)})</h2>
            {tx_section}
        </div>
        
        <div class="card" style="text-align: center;">
            <div style="display: flex; gap: 10px; justify-content: center;">
                {f'<a href="/blocks/{block.height - 1}" style="padding: 10px 20px; background: var(--gradient-start); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">← Previous Block</a>' if block.height > 0 else ''}
                <a href="/" style="padding: 10px 20px; background: var(--text-secondary); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">🏠 Home</a>
                {f'<a href="/blocks/{block.height + 1}" style="padding: 10px 20px; background: var(--gradient-start); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">Next Block →</a>' if block.height < len(ledger.blocks) - 1 else ''}
            </div>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html)


@app.get("/transactions", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def get_transactions(request: Request, page: int = 1, tx_filter: Optional[str] = None, hide_legacy: str = "true"):
    """Get paginated list of all transactions with filtering (defaults to transfers only)"""
    from datetime import datetime
    
    ledger = get_ledger()
    
    # Parse hide_legacy parameter
    hide_legacy_bool = hide_legacy.lower() == "true"
    
    # DEFAULT TO TRANSFERS ONLY - what users care about
    if tx_filter is None:
        tx_filter = "transfer"
    
    # Get transaction statistics matching the hide_legacy filter
    tx_stats = get_transaction_stats(ledger, hide_legacy=hide_legacy_bool)
    
    # Get paginated transactions
    result = get_all_transactions(
        ledger, 
        page=page, 
        page_size=50, 
        tx_filter=tx_filter,
        hide_legacy=hide_legacy_bool
    )
    
    # Build transaction rows with type-specific formatting
    tx_rows = ""
    for tx in result['transactions']:
        timestamp_str = datetime.fromtimestamp(tx['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        
        # Type badge with color coding
        type_badges = {
            'transfer': '<span class="badge badge-success">Transfer</span>',
            'validator_registration': '<span class="badge badge-info">Registration</span>',
            'epoch_attestation': '<span class="badge badge-warning">Epoch Attestation</span>',
            'validator_heartbeat': '<span class="badge badge-secondary">Heartbeat (Legacy)</span>',
            'genesis_reward': '<span class="badge badge-info">Genesis Reward</span>'
        }
        type_badge = type_badges.get(tx['tx_type'], f'<span class="badge badge-secondary">{tx["tx_type"]}</span>')
        
        # Format sender and recipient based on transaction type
        sender = tx.get('sender', 'N/A')
        recipient = tx.get('recipient', 'N/A')
        
        sender_display = f'<a href="/address/{sender}" style="font-family: monospace; font-size: 0.85em;">{sender[:12]}...</a>' if sender != 'N/A' else sender
        recipient_display = f'<a href="/address/{recipient}" style="font-family: monospace; font-size: 0.85em;">{recipient[:12]}...</a>' if recipient != 'N/A' else recipient
        
        # Format amount and fee based on type
        if tx['tx_type'] == 'epoch_attestation':
            # For epoch attestations, show epoch number instead of amount
            amount_display = f'Epoch {tx.get("epoch_number", "N/A")}'
            fee_display = format_pals(tx.get('fee', 0))
        else:
            amount_display = format_pals(tx.get('amount', 0))
            fee_display = format_pals(tx.get('fee', 0))
        
        tx_rows += f"""
            <tr>
                <td><a href="/tx/{tx['tx_hash']}" style="font-family: monospace; font-size: 0.85em;">{tx['tx_hash'][:16]}...</a></td>
                <td>{type_badge}</td>
                <td>{sender_display}</td>
                <td>{recipient_display}</td>
                <td>{amount_display}</td>
                <td>{fee_display}</td>
                <td><a href="/blocks/{tx['block_height']}">#{tx['block_height']}</a></td>
                <td style="font-size: 0.85em;">{timestamp_str}</td>
            </tr>
        """
    
    # SIMPLIFIED: Only show transfer filter - users only care about money transfers
    filter_buttons = ""
    # Removed all internal blockchain operation filters
    
    # Pagination links
    prev_link = ""
    next_link = ""
    if result['has_prev']:
        prev_url = f'/transactions?page={page - 1}&hide_legacy={hide_legacy}' + (f'&tx_filter={tx_filter}' if tx_filter else '')
        prev_link = f'<a href="{prev_url}" style="padding: 10px 20px; background: var(--gradient-start); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">← Previous</a>'
    
    if result['has_next']:
        next_url = f'/transactions?page={page + 1}&hide_legacy={hide_legacy}' + (f'&tx_filter={tx_filter}' if tx_filter else '')
        next_link = f'<a href="{next_url}" style="padding: 10px 20px; background: var(--gradient-start); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">Next →</a>'
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Transactions - TIMPAL Explorer</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        {get_navigation_html("transactions")}
        
        <div class="card">
            <h1>💸 Money Transfers</h1>
            <p style="color: var(--text-secondary);">
                {f"Showing {len(result['transactions'])} of {result['total_count']:,} transfers (Page {page} of {result['total_pages']})" if result['total_count'] > 0 else f'No transfers found yet - be the first to send {config.SYMBOL}!'}
            </p>
        </div>
        
        <div class="card">
            <h2>📊 Transfer Statistics</h2>
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-label">Total Transfers</div>
                    <div class="stat-value">{tx_stats['transfer']:,}</div>
                    <div class="stat-trend">Money transfers completed</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Confirmation Time</div>
                    <div class="stat-value">3s</div>
                    <div class="stat-trend">1 block finality</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Transaction Fee</div>
                    <div class="stat-value">0.0005</div>
                    <div class="stat-trend">{config.SYMBOL} per transfer</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <table class="table">
                <thead>
                    <tr>
                        <th>Transaction Hash</th>
                        <th>Type</th>
                        <th>From</th>
                        <th>To</th>
                        <th>Amount / Data</th>
                        <th>Fee</th>
                        <th>Block</th>
                        <th>Timestamp</th>
                    </tr>
                </thead>
                <tbody>
                    {tx_rows if tx_rows else f'<tr><td colspan="8" style="text-align: center; padding: 40px; color: var(--text-secondary);">{"No money transfers yet - blockchain is waiting for users to send " + config.SYMBOL if tx_filter == "transfer" else "No transactions found"}</td></tr>'}
                </tbody>
            </table>
        </div>
        
        <div class="card" style="text-align: center;">
            <div style="display: flex; gap: 10px; justify-content: center; align-items: center;">
                {prev_link}
                <span style="color: var(--text-secondary);">Page {page} of {result['total_pages']}</span>
                {next_link}
            </div>
        </div>
        
        <div class="card" style="text-align: center;">
            <a href="/" style="padding: 10px 20px; background: var(--text-secondary); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">🏠 Home</a>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html)


@app.get("/tx/{tx_hash}")
@limiter.limit("20/minute")
async def get_transaction(request: Request, tx_hash: str):
    """Get transaction details page"""
    ledger = get_ledger()
    
    tx_found = None
    block_found = None
    
    for block in ledger.blocks:
        for tx in block.transactions:
            if tx.tx_hash == tx_hash:
                tx_found = tx
                block_found = block
                break
        if tx_found:
            break
    
    if not tx_found or not block_found:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Format transaction data
    from datetime import datetime
    tx_data = tx_found.to_dict()
    confirmations = len(ledger.blocks) - block_found.height
    timestamp_str = datetime.fromtimestamp(block_found.timestamp).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    # Determine transaction type display
    tx_type_display = {
        'transfer': '💸 Transfer',
        'validator_registration': '🎫 Validator Registration',
        'validator_deposit': '💰 Validator Deposit',
        'validator_withdrawal': '🏦 Validator Withdrawal'
    }.get(tx_data.get('tx_type', 'transfer'), '📝 Transaction')
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Transaction {tx_hash[:16]}... - TIMPAL Explorer</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        {get_navigation_html("tx")}
        
        <div class="card">
            <h1>{tx_type_display}</h1>
            <p style="color: var(--text-secondary); word-break: break-all; font-family: monospace; font-size: 0.9em;">
                {tx_hash}
            </p>
        </div>
        
        <div class="card">
            <h2>📋 Transaction Details</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>Status</strong></td>
                        <td><span class="badge badge-success">✅ Confirmed</span> ({confirmations} confirmations)</td>
                    </tr>
                    <tr>
                        <td><strong>Block Height</strong></td>
                        <td><a href="/blocks/{block_found.height}">#{block_found.height}</a></td>
                    </tr>
                    <tr>
                        <td><strong>Block Hash</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.85em;">{block_found.block_hash}</td>
                    </tr>
                    <tr>
                        <td><strong>Timestamp</strong></td>
                        <td>{timestamp_str}</td>
                    </tr>
                    <tr>
                        <td><strong>Type</strong></td>
                        <td>{tx_data.get('tx_type', 'transfer')}</td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>💰 Transfer Information</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>From</strong></td>
                        <td><a href="/address/{tx_data['sender']}" style="font-family: monospace; font-size: 0.85em;">{tx_data['sender']}</a></td>
                    </tr>
                    <tr>
                        <td><strong>To</strong></td>
                        <td><a href="/address/{tx_data['recipient']}" style="font-family: monospace; font-size: 0.85em;">{tx_data['recipient'] if tx_data['recipient'] else '(Contract Execution)'}</a></td>
                    </tr>
                    <tr>
                        <td><strong>Amount</strong></td>
                        <td><span style="font-size: 1.2em; font-weight: bold;">{format_pals(tx_data['amount'])}</span></td>
                    </tr>
                    <tr>
                        <td><strong>Transaction Fee</strong></td>
                        <td>{format_pals(tx_data['fee'])}</td>
                    </tr>
                    <tr>
                        <td><strong>Nonce</strong></td>
                        <td>{tx_data['nonce']}</td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>🔐 Cryptographic Data</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>Transaction Hash</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{tx_hash}</td>
                    </tr>
                    <tr>
                        <td><strong>Public Key</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{tx_data.get('public_key', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td><strong>Signature</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{tx_data.get('signature', 'N/A')}</td>
                    </tr>
                    {f'''<tr>
                        <td><strong>Device ID</strong></td>
                        <td style="word-break: break-all; font-family: monospace; font-size: 0.8em; background: var(--bg-color); padding: 10px; border-radius: 5px;">{tx_data.get('device_id', 'N/A')}</td>
                    </tr>''' if tx_data.get('device_id') else ''}
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>📊 Raw Transaction Data (JSON)</h2>
            <pre style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto; font-size: 0.85em; line-height: 1.5;">
{json.dumps({
    'tx_hash': tx_hash,
    'sender': tx_data['sender'],
    'recipient': tx_data['recipient'],
    'amount': tx_data['amount'],
    'fee': tx_data['fee'],
    'timestamp': block_found.timestamp,
    'nonce': tx_data['nonce'],
    'signature': tx_data.get('signature'),
    'public_key': tx_data.get('public_key'),
    'tx_type': tx_data.get('tx_type'),
    'device_id': tx_data.get('device_id'),
    'block_height': block_found.height,
    'block_hash': block_found.block_hash,
    'confirmations': confirmations
}, indent=2)}
            </pre>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/send", response_class=HTMLResponse)
@limiter.limit("15/minute")
async def send_transfer_page(request: Request):
    """Send money transfer page"""
    ledger = get_ledger()
    
    # Default wallet path for backend
    default_wallet_path = "testnet_data_node_9000/validator_wallet.json"
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Send Transfer | TIMPAL Block Explorer</title>
        {get_base_styles()}
        <style>
            .form-group {{
                margin-bottom: 20px;
            }}
            label {{
                display: block;
                margin-bottom: 5px;
                font-weight: 600;
            }}
            input, textarea {{
                width: 100%;
                padding: 12px;
                background: var(--bg-color);
                border: 1px solid var(--border-color);
                border-radius: 6px;
                color: var(--text-color);
                font-size: 1em;
                box-sizing: border-box;
            }}
            input:focus, textarea:focus {{
                outline: none;
                border-color: #00d4ff;
            }}
            .btn-submit {{
                background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%);
                color: white;
                padding: 15px 30px;
                border: none;
                border-radius: 8px;
                font-size: 1.1em;
                font-weight: 600;
                cursor: pointer;
                width: 100%;
                transition: all 0.3s;
            }}
            .btn-submit:hover {{
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0, 212, 255, 0.4);
            }}
            .info-box {{
                background: rgba(0, 212, 255, 0.1);
                border-left: 4px solid #00d4ff;
                padding: 15px;
                margin: 20px 0;
                border-radius: 6px;
            }}
            .success {{
                background: rgba(0, 255, 136, 0.1);
                border-left-color: #00ff88;
            }}
            .error {{
                background: rgba(255, 68, 68, 0.1);
                border-left-color: #ff4444;
            }}
        </style>
    </head>
    <body>
        {get_navigation_html()}
        
        <div class="container">
            <div class="header">
                <h1>💸 Send Timpal ({config.SYMBOL})</h1>
                <p style="font-size: 1.1em; color: white;">Submit a {config.SYMBOL} transfer to the blockchain network</p>
            </div>
            
            <div class="info-box">
                <strong>⚡ Fast Confirmation:</strong> Your transaction will be confirmed in the next block (~3 seconds)<br>
                <strong>💰 Fixed Fee:</strong> 0.0005 {config.SYMBOL} per transaction<br>
                <strong>🔒 Secure:</strong> Transaction is signed with your wallet's private key
            </div>
            
            <div class="card">
                <h2>📝 Transfer Details</h2>
                <form id="transferForm" method="POST" action="/send">
                    <div class="form-group">
                        <label for="sender_address">Sender Address:</label>
                        <input type="text" id="sender_address" name="sender_address" 
                               placeholder="tmpl..." required>
                        <small style="color: #888;">Paste your wallet address (starts with 'tmpl')</small>
                    </div>
                    
                    <div class="form-group">
                        <label for="password">Wallet Password:</label>
                        <input type="password" id="password" name="password" 
                               placeholder="Enter your wallet password" required>
                        <small style="color: #888;">🔐 Password is used to decrypt your wallet file</small>
                    </div>
                    
                    <div class="form-group">
                        <label for="pin">Wallet PIN:</label>
                        <input type="password" id="pin" name="pin" 
                               placeholder="Enter your 6+ digit PIN" required>
                        <small style="color: #888;">🔒 PIN is used to authorize this specific transfer</small>
                    </div>
                    
                    <div class="form-group">
                        <label for="recipient">Recipient Address:</label>
                        <input type="text" id="recipient" name="recipient" 
                               placeholder="tmpl..." required>
                        <small style="color: #888;">Must start with 'tmpl'</small>
                    </div>
                    
                    <div class="form-group">
                        <label for="amount">Amount ({config.SYMBOL}):</label>
                        <input type="text" id="amount" name="amount" 
                               placeholder="0.00000000" required>
                        <small style="color: #888;">Minimum: 0.00000001 {config.SYMBOL} (+ 0.0005 {config.SYMBOL} fee)</small>
                    </div>
                    
                    <button type="submit" class="btn-submit">📤 Send {config.SYMBOL}</button>
                </form>
                
                <div id="result" style="margin-top: 20px;"></div>
            </div>
        </div>
        
        {get_theme_toggle_script()}
        
        <script>
            document.getElementById('transferForm').addEventListener('submit', async (e) => {{
                e.preventDefault();
                
                const resultDiv = document.getElementById('result');
                resultDiv.innerHTML = '<div class="info-box">⏳ Submitting transaction...</div>';
                
                const formData = new FormData(e.target);
                
                try {{
                    const response = await fetch('/send', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }},
                        body: new URLSearchParams(formData)
                    }});
                    
                    const result = await response.json();
                    
                    if (response.ok) {{
                        resultDiv.innerHTML = `
                            <div class="info-box success">
                                <h3>✅ Transfer Submitted Successfully!</h3>
                                <p><strong>Transaction Hash:</strong> <a href="/tx/${{result.tx_hash}}" style="color: #00ff88; font-family: monospace;">${{result.tx_hash}}</a></p>
                                <p><strong>Status:</strong> ${{result.message}}</p>
                                <p><strong>Expected Confirmation:</strong> Within 3 seconds (next block)</p>
                                <p style="margin-top: 15px;"><a href="/tx/${{result.tx_hash}}" style="color: #00ff88;">📊 View Transaction Status →</a></p>
                            </div>
                        `;
                        // Clear form
                        e.target.reset();
                    }} else {{
                        resultDiv.innerHTML = `
                            <div class="info-box error">
                                <h3>❌ Transfer Failed</h3>
                                <p>${{result.error || 'Unknown error occurred'}}</p>
                            </div>
                        `;
                    }}
                }} catch (error) {{
                    resultDiv.innerHTML = `
                        <div class="info-box error">
                            <h3>❌ Network Error</h3>
                            <p>${{error.message}}</p>
                        </div>
                    `;
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.post("/send")
@limiter.limit("5/minute")
async def submit_transfer(request: Request, sender_address: str = Form(...), password: str = Form(...), pin: str = Form(...), recipient: str = Form(...), amount: float = Form(...)):
    """Handle transfer submission"""
    try:
        # Strip whitespace from addresses
        sender_address = sender_address.strip()
        recipient = recipient.strip()
        
        # Validate inputs
        if not sender_address.startswith("tmpl"):
            return JSONResponse({"error": "Invalid sender address - must start with 'tmpl'"}, status_code=400)
        
        if not recipient.startswith("tmpl"):
            return JSONResponse({"error": "Invalid recipient address - must start with 'tmpl'"}, status_code=400)
        
        # Validate amount
        try:
            amount = float(amount)
            if amount <= 0:
                return JSONResponse({"error": "Amount must be greater than 0"}, status_code=400)
        except ValueError:
            return JSONResponse({"error": "Invalid amount format"}, status_code=400)
        
        # Auto-discover wallet files (v3 wallets.json, v2 wallet_v2.json, legacy wallet.json)
        import glob
        import json
        from app.seed_wallet import SeedWallet
        from app.metawallet import MultiWallet

        wallet_files = (
            glob.glob("wallets.json")
            + glob.glob("wallet*.json")
            + glob.glob("**/wallets.json", recursive=True)
            + glob.glob("**/wallet*.json", recursive=True)
        )

        wallet_private_key = None
        wallet_public_key = None

        for wf in wallet_files:
            try:
                with open(wf, "r") as f:
                    meta = json.load(f)
                version = meta.get("version", 1)

                if version == 3:
                    mw = MultiWallet(wf)
                    mw.load(password)
                    found = mw.find_account(sender_address)
                    if not found:
                        continue
                    vault_id, acct = found
                    vault = mw.get_vault(vault_id)
                    if not vault.validate_pin(pin):
                        return JSONResponse({"error": "Incorrect PIN"}, status_code=400)
                    _addr, wallet_public_key, wallet_private_key = mw.export_account_private_key(
                        password, vault_id=vault_id, index=acct.index
                    )
                    break

                if version == 2:
                    temp_wallet = SeedWallet(wf)
                    temp_wallet.load_wallet(password)
                    # v2 only stores one derived account (0) in practice
                    account = temp_wallet.accounts.get(0)
                    if account and account["address"] == sender_address:
                        if not temp_wallet.validate_pin(pin):
                            return JSONResponse({"error": "Incorrect PIN"}, status_code=400)
                        wallet_private_key = account["private_key"]
                        wallet_public_key = account["public_key"]
                        break

            except Exception:
                continue

        if not wallet_private_key or not wallet_public_key:
            return JSONResponse({"error": "Wallet not found for this address, incorrect password, or unsupported wallet"}, status_code=400)
        
        # Get current nonce from node
        import os
        node_api_port = os.getenv("EXPLORER_API_PORT", "9001")
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://localhost:{node_api_port}/api/account/{sender_address}") as resp:
                if resp.status == 200:
                    account_data = await resp.json()
                    nonce = account_data['pending_nonce']
                    balance = account_data['balance']
                else:
                    return JSONResponse({"error": "Failed to fetch account info from node"}, status_code=500)
        
        # Convert TMPL to pals (use round() to avoid floating-point precision errors)
        amount_pals = round(amount * config.PALS_PER_TMPL)
        fee_pals = 50000  # 0.0005 TMPL fixed fee
        
        # Check balance
        if balance < amount_pals + fee_pals:
            return JSONResponse({"error": f"Insufficient balance. Need {(amount_pals + fee_pals) / config.PALS_PER_TMPL:.8f} {config.SYMBOL}, have {balance / config.PALS_PER_TMPL:.8f} {config.SYMBOL}"}, status_code=400)
        
        # Create and sign transaction
        tx = Transaction(
            sender=sender_address,
            recipient=recipient,
            amount=amount_pals,
            fee=fee_pals,
            timestamp=time.time(),
            nonce=nonce,
            tx_type="transfer"
        )
        tx.public_key = wallet_public_key
        tx.sign(wallet_private_key)
        
        # Submit to node's HTTP API
        import os
        node_api_port = os.getenv("EXPLORER_API_PORT", "9001")
        async with aiohttp.ClientSession() as session:
            async with session.post(f"http://localhost:{node_api_port}/submit_transaction", json=tx.to_dict()) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return JSONResponse({
                        "status": "success",
                        "message": "Transaction broadcast to network!",
                        "tx_hash": result['tx_hash']
                    })
                else:
                    error_text = await resp.text()
                    return JSONResponse({"error": f"Node rejected transaction: {error_text}"}, status_code=400)
                    
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/address/{address}", response_class=HTMLResponse)
@limiter.limit("15/minute")
async def get_address(request: Request, address: str):
    """Get address balance and transaction history page"""
    if not address.startswith("tmpl"):
        raise HTTPException(status_code=400, detail="Invalid TIMPAL address format")
    
    ledger = get_ledger()
    balance = ledger.get_balance(address)
    transactions = get_address_transactions(ledger, address)
    
    sent_count = sum(1 for tx in transactions if tx['sender'] == address)
    received_count = sum(1 for tx in transactions if tx['recipient'] == address)
    
    total_sent = sum(tx['amount'] + tx['fee'] for tx in transactions if tx['sender'] == address)
    total_received = sum(tx['amount'] for tx in transactions if tx['recipient'] == address)
    
    is_validator = address in config.GENESIS_VALIDATORS
    validator_info = ledger.get_validator_info(address) if is_validator else None
    
    # Build transaction rows
    tx_rows = ""
    for tx in transactions[:50]:  # Show latest 50 transactions
        tx_type_icon = "📤" if tx['sender'] == address else "📥"
        tx_type_label = "Sent" if tx['sender'] == address else "Received"
        tx_rows += f"""
            <tr>
                <td>{tx_type_icon} {tx_type_label}</td>
                <td><a href="/tx/{tx['tx_hash']}" style="font-family: monospace; font-size: 0.85em;">{tx['tx_hash'][:16]}...</a></td>
                <td><a href="/address/{tx['sender']}" style="font-family: monospace; font-size: 0.85em;">{tx['sender'][:12]}...</a></td>
                <td><a href="/address/{tx['recipient']}" style="font-family: monospace; font-size: 0.85em;">{tx['recipient'][:12]}...</a></td>
                <td>{format_pals(tx['amount'])}</td>
                <td><a href="/blocks/{tx.get('block_height', '?')}">#{tx.get('block_height', '?')}</a></td>
            </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Address {address[:16]}... - TIMPAL Explorer</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        {get_navigation_html("address")}
        
        <div class="card">
            <h1>{'🛡️ Validator Address' if is_validator else '💼 Wallet Address'}</h1>
            <p style="color: var(--text-secondary); word-break: break-all; font-family: monospace; font-size: 0.9em;">
                {address}
            </p>
            {f'<span class="badge badge-success" style="font-size: 0.9em;">✅ Active Validator</span>' if is_validator else ''}
        </div>
        
        <div class="card">
            <h2>💰 Balance & Statistics</h2>
            <table class="table">
                <tbody>
                    <tr>
                        <td><strong>Current Balance</strong></td>
                        <td><span style="font-size: 1.3em; font-weight: bold; color: var(--gradient-start);">{format_pals(balance)}</span></td>
                    </tr>
                    <tr>
                        <td><strong>Total Transactions</strong></td>
                        <td>{len(transactions)}</td>
                    </tr>
                    <tr>
                        <td><strong>Transactions Sent</strong></td>
                        <td>{sent_count}</td>
                    </tr>
                    <tr>
                        <td><strong>Transactions Received</strong></td>
                        <td>{received_count}</td>
                    </tr>
                    <tr>
                        <td><strong>Total Sent</strong></td>
                        <td>{format_pals(total_sent)}</td>
                    </tr>
                    <tr>
                        <td><strong>Total Received</strong></td>
                        <td>{format_pals(total_received)}</td>
                    </tr>
                    {f'''<tr>
                        <td><strong>Validator Status</strong></td>
                        <td><span class="badge badge-success">Active Validator</span></td>
                    </tr>''' if is_validator else ''}
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>📜 Transaction History ({len(transactions)})</h2>
            {f'''
            <table class="table">
                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Transaction Hash</th>
                        <th>From</th>
                        <th>To</th>
                        <th>Amount</th>
                        <th>Block</th>
                    </tr>
                </thead>
                <tbody>
                    {tx_rows}
                </tbody>
            </table>
            ''' if transactions else '<p style="text-align: center; color: var(--text-secondary); padding: 20px;">No transactions found for this address</p>'}
            {f'<p style="text-align: center; color: var(--text-secondary); margin-top: 10px; font-size: 0.9em;">Showing latest 50 transactions</p>' if len(transactions) > 50 else ''}
        </div>
        
        <div class="card" style="text-align: center;">
            <a href="/" style="padding: 10px 20px; background: var(--text-secondary); color: white; border-radius: 5px; text-decoration: none; font-weight: bold;">🏠 Home</a>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html)


@app.get("/stats")
@limiter.limit("30/minute")
async def get_stats(request: Request):
    """Get blockchain statistics (optimized with caching)"""
    ledger = get_ledger()
    latest_block = ledger.get_latest_block()
    
    cached = get_cached_stats(ledger)
    
    phase1_blocks = config.PHASE1_BLOCKS
    current_phase = 1 if latest_block and latest_block.height < phase1_blocks else 2
    
    return {
        "chain_height": latest_block.height if latest_block else 0,
        "total_blocks": len(ledger.blocks),
        "total_transactions": cached['transfer_count'],
        "total_supply": ledger.total_emitted_pals,
        "total_supply_tmpl": format_pals(ledger.total_emitted_pals),
        "max_supply": config.MAX_SUPPLY_PALS,
        "max_supply_tmpl": format_pals(config.MAX_SUPPLY_PALS),
        "percentage_mined": (ledger.total_emitted_pals / config.MAX_SUPPLY_PALS) * 100,
        "active_addresses": cached['active_addresses_count'],
        "total_fees_collected": cached['total_fees'],
        "total_fees_tmpl": format_pals(cached['total_fees']),
        "current_phase": current_phase,
        "block_time": config.BLOCK_TIME,
        "validator_count": ledger.get_validator_count()
    }


@app.get("/validators")
@limiter.limit("30/minute")
async def get_validators(request: Request):
    """Get all registered validators (dynamic validator set) - optimized"""
    ledger = get_ledger()
    validators = []
    
    current_height = ledger.get_block_count() - 1
    LIVENESS_WINDOW = 30  # ~90 seconds at 3s/block - proof of activity window
    
    cached_validator_stats = get_cached_validator_stats(ledger)
    
    for address, validator_data in ledger.validator_registry.items():
        info = ledger.get_validator_info(address)
        
        if info:
            balance = ledger.get_balance(address)
            
            stats = cached_validator_stats.get(address, {
                'blocks_proposed': 0,
                'total_rewards': 0,
                'last_block_height': -1
            })
            
            block_count = stats['blocks_proposed']
            last_block_height = stats['last_block_height']
            
            if current_height < 10:
                display_status = "active"
            elif last_block_height >= 0 and (current_height - last_block_height) <= LIVENESS_WINDOW:
                display_status = "active"
            else:
                display_status = "offline"
            
            validators.append({
                "address": address,
                "public_key": info.get('public_key', 'N/A'),
                "balance": balance,
                "balance_tmpl": format_pals(balance),
                "blocks_proposed": block_count,
                "status": display_status,
                "registered_at": info.get('registered_at', 0),
                "device_id_preview": info.get('device_id', 'N/A')[:32] + '...' if info.get('device_id') and len(info.get('device_id', '')) > 32 else info.get('device_id', 'N/A')
            })
    
    # FILTER: Only show ACTIVE/ONLINE validators (user request)
    # Offline validators should not appear in the list or network graph
    active_validators = [v for v in validators if v['status'] == 'active']
    
    # Sort by blocks proposed (most active first)
    active_validators.sort(key=lambda v: v.get('blocks_proposed', 0), reverse=True)
    
    return {
        "validators": active_validators,
        "total_count": len(active_validators),
        "active_count": len(active_validators),
        "inactive_count": 0  # Not showing offline validators
    }


# ============================================================================
# API ENDPOINT ALIASES (with /api/ prefix for standardization)
# ============================================================================

@app.get("/api/stats")
@limiter.limit("30/minute")
async def api_get_stats(request: Request):
    """API endpoint: Get blockchain statistics (JSON)"""
    return await get_stats(request)


@app.get("/api/validators")
@limiter.limit("30/minute")
async def api_get_validators(request: Request):
    """API endpoint: Get all validators (JSON)"""
    return await get_validators(request)


@app.get("/api/blockchain/info")
@limiter.limit("100/minute")
async def api_get_blockchain_info(request: Request):
    """
    API endpoint: Get blockchain info for sync (JSON)
    
    Returns:
        JSON with current blockchain height and block count
    """
    ledger = get_ledger()
    latest_block = ledger.get_latest_block()
    
    return {
        "height": latest_block.height if latest_block else 0,
        "blocks": len(ledger.blocks)
    }


@app.get("/api/blocks/range")
@limiter.limit("100/minute")
async def api_get_blocks_range(request: Request, start: int, end: int):
    """
    API endpoint: Get sequential range of blocks for sync (JSON)
    
    CRITICAL: Returns blocks in SEQUENTIAL ORDER by height (start to end)
    This is required for blockchain sync to work correctly.
    
    Args:
        start: First block height (inclusive)
        end: Last block height (inclusive)
    
    Returns:
        JSON with blocks array in sequential order
    """
    ledger = get_ledger()
    
    # Validate parameters
    if start < 0 or end < start:
        raise HTTPException(status_code=400, detail="Invalid range: start must be >= 0 and end >= start")
    
    # Enforce max 100 blocks per request (prevent memory issues)
    if end - start + 1 > 100:
        raise HTTPException(status_code=400, detail="Range too large: max 100 blocks per request")
    
    # Get blocks in SEQUENTIAL ORDER (critical for sync)
    blocks = []
    for height in range(start, end + 1):
        if height < len(ledger.blocks):
            block = ledger.blocks[height]
            blocks.append(block.to_dict())
        else:
            # Block doesn't exist yet - return what we have so far
            break
    
    return {
        "blocks": blocks,
        "count": len(blocks),
        "start": start,
        "end": end
    }


@app.get("/search-redirect")
async def search_redirect(q: str):
    """Redirect search form to search endpoint"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/search/{q.strip()}", status_code=303)


@app.get("/search/{query}")
@limiter.limit("10/minute")
async def search(request: Request, query: str):
    """Search for block, transaction, or address"""
    from fastapi.responses import RedirectResponse
    ledger = get_ledger()
    query = query.strip()
    
    # Search for block number
    if query.isdigit():
        block = ledger.get_block_by_height(int(query))
        if block:
            return RedirectResponse(url=f"/blocks/{block.height}", status_code=303)
    
    # Search for address
    if query.startswith("tmpl"):
        balance = ledger.get_balance(query)
        if balance > 0 or query in config.GENESIS_VALIDATORS:
            return RedirectResponse(url=f"/address/{query}", status_code=303)
    
    # Search for block hash or transaction hash
    for block in ledger.blocks:
        if block.block_hash == query:
            return RedirectResponse(url=f"/blocks/{block.height}", status_code=303)
        
        for tx in block.transactions:
            if tx.tx_hash == query:
                return RedirectResponse(url=f"/tx/{query}", status_code=303)
    
    raise HTTPException(status_code=404, detail="No results found")


@app.get("/stream")
async def stream(request: Request):
    """Server-Sent Events (SSE) endpoint for real-time blockchain updates (optimized)"""
    async def event_generator():
        last_height = 0
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while True:
            if await request.is_disconnected():
                break
            
            try:
                ledger = get_ledger()
                latest_block = ledger.get_latest_block()
                current_height = latest_block.height if latest_block else 0
                
                if current_height != last_height:
                    last_height = current_height
                    consecutive_errors = 0
                    
                    cached = get_cached_stats(ledger)
                    
                    data = {
                        "latest_block": current_height,
                        "total_supply_tmpl": format_pals(ledger.total_emitted_pals),
                        "validator_count": ledger.get_validator_count(),
                        "total_transactions": cached['transfer_count'],
                        "timestamp": time.time()
                    }
                    
                    yield f"data: {json.dumps(data)}\n\n"
                
                await asyncio.sleep(2)
                
            except Exception as e:
                consecutive_errors += 1
                print(f"SSE Error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                if consecutive_errors >= max_consecutive_errors:
                    print("SSE: Too many consecutive errors, closing connection")
                    break
                await asyncio.sleep(1)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/validators-dashboard", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def validators_dashboard(request: Request):
    """Enhanced validator dashboard with detailed stats and leaderboard (optimized)"""
    ledger = get_ledger()
    validators = []
    
    current_height = ledger.get_block_count() - 1
    LIVENESS_WINDOW = 30  # ~90 seconds at 3s/block - proof of activity window
    
    cached_validator_stats = get_cached_validator_stats(ledger)
    
    for address, validator_data in ledger.validator_registry.items():
        info = ledger.get_validator_info(address)
        
        if info:
            balance = ledger.get_balance(address)
            
            stats = cached_validator_stats.get(address, {
                'blocks_proposed': 0,
                'total_rewards': 0,
                'last_block_height': -1
            })
            
            block_count = stats['blocks_proposed']
            total_rewards = stats['total_rewards']
            last_block_height = stats['last_block_height']
            
            if current_height < 10:
                display_status = "active"
            elif last_block_height >= 0 and (current_height - last_block_height) <= LIVENESS_WINDOW:
                display_status = "active"
            else:
                display_status = "offline"
            
            validators.append({
                "address": address,
                "public_key": info.get('public_key', 'N/A')[:64] + '...',
                "balance": balance,
                "balance_tmpl": format_pals(balance),
                "blocks_proposed": block_count,
                "total_rewards": total_rewards,
                "total_rewards_tmpl": format_pals(total_rewards),
                "status": display_status,
                "registered_at": info.get('registered_at', 0),
                "device_id_preview": info.get('device_id', 'N/A')[:32] + '...' if info.get('device_id') and len(info.get('device_id', '')) > 32 else info.get('device_id', 'N/A')
            })
    
    validators.sort(key=lambda v: v['blocks_proposed'], reverse=True)
    
    # Calculate stats
    total_validators = len(validators)
    active_validators = sum(1 for v in validators if v['status'] == 'active')
    total_blocks_by_validators = sum(v['blocks_proposed'] for v in validators)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TIMPAL Validator Dashboard</title>
        {get_base_styles()}
        {get_chart_js_cdn()}
        {get_theme_toggle_script()}
        {get_live_updates_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        
        <div class="header">
            <h1>🛡️ TIMPAL Validator Dashboard</h1>
            <p><span class="live-indicator"></span> Real-time validator registry and performance metrics</p>
        </div>
        
        {get_navigation_html("validators")}
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-label">Total Validators</div>
                <div class="stat-value" id="live-validator-count">{total_validators}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Validators</div>
                <div class="stat-value">{active_validators}</div>
                <div class="stat-trend">✓ 100% Decentralized</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Blocks Proposed</div>
                <div class="stat-value">{total_blocks_by_validators}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Sybil Protection</div>
                <div class="stat-value">Device Fingerprint</div>
                <div class="stat-trend">✓ 3-Layer Defense</div>
            </div>
        </div>
        
        <div class="card">
            <h2>🏆 Validator Leaderboard</h2>
            <p style="color: var(--text-secondary);">Validators ranked by blocks proposed (most active first)</p>
            <table class="table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Address</th>
                        <th>Status</th>
                        <th>Blocks Proposed</th>
                        <th>Total Rewards</th>
                        <th>Balance</th>
                        <th>Device ID</th>
                    </tr>
                </thead>
                <tbody>
    """
    
    for idx, v in enumerate(validators, 1):
        status_badge = 'badge-success' if v['status'] == 'active' else 'badge-secondary'
        rank_emoji = '🥇' if idx == 1 else ('🥈' if idx == 2 else ('🥉' if idx == 3 else f'{idx}.'))
        
        html += f"""
                    <tr>
                        <td>{rank_emoji}</td>
                        <td><span class="monospace">{v['address'][:20]}...</span></td>
                        <td><span class="badge {status_badge}">{v['status'].upper()}</span></td>
                        <td><strong>{v['blocks_proposed']}</strong></td>
                        <td>{v['total_rewards_tmpl']}</td>
                        <td>{v['balance_tmpl']}</td>
                        <td><span class="monospace">{v['device_id_preview']}</span></td>
                    </tr>
        """
    
    html += """
                </tbody>
            </table>
        </div>
        
        <div class="grid-2">
            <div class="card">
                <h2>📊 Block Proposal Distribution</h2>
                <div class="chart-container">
                    <canvas id="proposalChart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <h2>💰 Reward Distribution</h2>
                <div class="chart-container">
                    <canvas id="rewardChart"></canvas>
                </div>
            </div>
        </div>
        
        <script>
            // Block Proposal Distribution Chart
            const proposalCtx = document.getElementById('proposalChart').getContext('2d');
            new Chart(proposalCtx, {
                type: 'bar',
                data: {
                    labels: """ + json.dumps([f"#{i+1}" for i in range(min(10, len(validators)))]) + """,
                    datasets: [{
                        label: 'Blocks Proposed',
                        data: """ + json.dumps([v['blocks_proposed'] for v in validators[:10]]) + """,
                        backgroundColor: 'rgba(102, 126, 234, 0.6)',
                        borderColor: 'rgba(102, 126, 234, 1)',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        title: {
                            display: true,
                            text: 'Top 10 Validators by Blocks Proposed'
                        }
                    },
                    scales: {
                        y: { beginAtZero: true }
                    }
                }
            });
            
            // Reward Distribution Chart
            const rewardCtx = document.getElementById('rewardChart').getContext('2d');
            new Chart(rewardCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Distributed', 'Remaining'],
                    datasets: [{
                        data: [""" + str(ledger.total_emitted_pals / config.PALS_PER_TMPL) + """, """ + str((config.MAX_SUPPLY_PALS - ledger.total_emitted_pals) / config.PALS_PER_TMPL) + """],
                        backgroundColor: [
                            'rgba(16, 185, 129, 0.6)',
                            'rgba(107, 114, 128, 0.3)'
                        ],
                        borderColor: [
                            'rgba(16, 185, 129, 1)',
                            'rgba(107, 114, 128, 1)'
                        ],
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom' },
                        title: {
                            display: true,
                            text: 'Total Supply Distribution (""" + config.SYMBOL + """)'
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    let label = context.label || '';
                                    if (label) {
                                        label += ': ';
                                    }
                                    label += context.parsed.toLocaleString() + ' """ + config.SYMBOL + """';
                                    return label;
                                }
                            }
                        }
                    }
                }
            });
        </script>
    </body>
    </html>
    """
    
    return html


@app.get("/analytics", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def analytics(request: Request):
    """Analytics page with interactive charts and blockchain metrics"""
    ledger = get_ledger()
    latest_block = ledger.get_latest_block()
    
    # Calculate supply curve data (last 100 blocks or all if less)
    block_heights = []
    supply_at_height = []
    tx_counts = []
    
    total_supply_so_far = 0
    for block in ledger.blocks[-100:]:
        block_heights.append(block.height)
        total_supply_so_far += block.reward
        supply_at_height.append(total_supply_so_far / config.PALS_PER_TMPL)
        # Count only transfer transactions (exclude heartbeats and registrations)
        transfer_count = sum(1 for tx in block.transactions if tx.tx_type == "transfer")
        tx_counts.append(transfer_count)
    
    # Validator growth over time (count only registered validators)
    validator_counts = []
    for block in ledger.blocks:
        # Count unique REGISTERED validators up to this block height
        # Only count validators that are in the validator_registry and were registered by this block
        validators_at_height = set()
        for address, validator_data in ledger.validator_registry.items():
            info = ledger.get_validator_info(address)
            if info and info.get('registered_at', 0) <= block.timestamp:
                validators_at_height.add(address)
        validator_counts.append(len(validators_at_height))
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TIMPAL Analytics</title>
        {get_base_styles()}
        {get_chart_js_cdn()}
        {get_theme_toggle_script()}
        {get_live_updates_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        
        <div class="header">
            <h1>📈 TIMPAL Analytics</h1>
            <p><span class="live-indicator"></span> Real-time blockchain metrics and visualization</p>
        </div>
        
        {get_navigation_html("analytics")}
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-label">Latest Block</div>
                <div class="stat-value" id="live-block-height">#{latest_block.height if latest_block else 0}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Supply</div>
                <div class="stat-value" id="live-total-supply">{format_pals(ledger.total_emitted_pals)}</div>
                <div class="stat-trend">{(ledger.total_emitted_pals / config.MAX_SUPPLY_PALS * 100):.2f}% mined</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Validators</div>
                <div class="stat-value" id="live-validator-count">{ledger.get_validator_count()}</div>
                <div class="stat-trend">🌐 Decentralized</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">{config.SYMBOL} Transfers</div>
                <div class="stat-value" id="live-tx-count">{sum(1 for b in ledger.blocks for tx in b.transactions if tx.tx_type == "transfer")}</div>
            </div>
        </div>
        
        <div class="grid-2">
            <div class="card">
                <h2>📊 Supply Curve</h2>
                <div class="chart-container">
                    <canvas id="supplyChart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <h2>🚀 Validator Growth</h2>
                <div class="chart-container">
                    <canvas id="validatorGrowthChart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="grid-2">
            <div class="card">
                <h2>📦 {config.SYMBOL} Transfer Volume</h2>
                <div class="chart-container">
                    <canvas id="txVolumeChart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <h2>⏱️ Emission Schedule</h2>
                <div class="chart-container">
                    <canvas id="emissionChart"></canvas>
                </div>
            </div>
        </div>
        
        <script>
            // Supply Curve Chart
            const supplyCtx = document.getElementById('supplyChart').getContext('2d');
            new Chart(supplyCtx, {{
                type: 'line',
                data: {{
                    labels: {json.dumps(block_heights)},
                    datasets: [{{
                        label: 'Total Supply ({config.SYMBOL})',
                        data: {json.dumps(supply_at_height)},
                        borderColor: 'rgba(102, 126, 234, 1)',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        fill: true,
                        tension: 0.4
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ display: true, position: 'top' }},
                        title: {{
                            display: true,
                            text: '{config.SYMBOL} Supply Growth Over Time'
                        }}
                    }},
                    scales: {{
                        x: {{ title: {{ display: true, text: 'Block Height' }} }},
                        y: {{ 
                            title: {{ display: true, text: 'Supply ({config.SYMBOL})' }},
                            beginAtZero: true
                        }}
                    }}
                }}
            }});
            
            // Validator Growth Chart
            const validatorGrowthCtx = document.getElementById('validatorGrowthChart').getContext('2d');
            new Chart(validatorGrowthCtx, {{
                type: 'line',
                data: {{
                    labels: {json.dumps(list(range(len(validator_counts))))},
                    datasets: [{{
                        label: 'Unique Validators',
                        data: {json.dumps(validator_counts)},
                        borderColor: 'rgba(16, 185, 129, 1)',
                        backgroundColor: 'rgba(16, 185, 129, 0.1)',
                        fill: true,
                        tension: 0.4,
                        stepped: true
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ display: true, position: 'top' }},
                        title: {{
                            display: true,
                            text: 'Validator Count Over Time'
                        }}
                    }},
                    scales: {{
                        x: {{ title: {{ display: true, text: 'Block Height' }} }},
                        y: {{ 
                            title: {{ display: true, text: 'Validators' }},
                            beginAtZero: true
                        }}
                    }}
                }}
            }});
            
            // Transaction Volume Chart
            const txVolumeCtx = document.getElementById('txVolumeChart').getContext('2d');
            new Chart(txVolumeCtx, {{
                type: 'bar',
                data: {{
                    labels: {json.dumps(block_heights)},
                    datasets: [{{
                        label: 'Transactions per Block',
                        data: {json.dumps(tx_counts)},
                        backgroundColor: 'rgba(245, 158, 11, 0.6)',
                        borderColor: 'rgba(245, 158, 11, 1)',
                        borderWidth: 1
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ display: false }},
                        title: {{
                            display: true,
                            text: 'Transaction Activity (Last 100 Blocks)'
                        }}
                    }},
                    scales: {{
                        x: {{ title: {{ display: true, text: 'Block Height' }} }},
                        y: {{ 
                            title: {{ display: true, text: 'Transactions' }},
                            beginAtZero: true
                        }}
                    }}
                }}
            }});
            
            // Emission Schedule Chart
            const emissionCtx = document.getElementById('emissionChart').getContext('2d');
            const phase1Blocks = {config.PHASE1_BLOCKS};
            const currentBlock = {latest_block.height if latest_block else 0};
            const blocksRemaining = Math.max(0, phase1Blocks - currentBlock);
            const yearsRemaining = (blocksRemaining * {config.BLOCK_TIME}) / (365.25 * 24 * 3600);
            
            new Chart(emissionCtx, {{
                type: 'doughnut',
                data: {{
                    labels: ['Phase 1 (Block Rewards)', 'Phase 2 (Fees Only)'],
                    datasets: [{{
                        data: [currentBlock, Math.max(0, phase1Blocks - currentBlock)],
                        backgroundColor: [
                            'rgba(139, 92, 246, 0.6)',
                            'rgba(229, 231, 235, 0.4)'
                        ],
                        borderColor: [
                            'rgba(139, 92, 246, 1)',
                            'rgba(229, 231, 235, 1)'
                        ],
                        borderWidth: 2
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ position: 'bottom' }},
                        title: {{
                            display: true,
                            text: `Emission Phase Progress (~${{yearsRemaining.toFixed(1)}} years to Phase 2)`
                        }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    return html


@app.get("/network", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def network_visualization(request: Request):
    """Network visualization page with interactive validator graph"""
    ledger = get_ledger()
    
    # Build network graph data
    validators = []
    edges = []
    
    # Get current height for liveness detection (same logic as leaderboard)
    current_height = ledger.get_block_count() - 1
    LIVENESS_WINDOW = 30  # ~90 seconds at 3s/block - proof of activity window
    
    for idx, (address, validator_data) in enumerate(ledger.validator_registry.items()):
        info = ledger.get_validator_info(address)
        if info:
            block_count = sum(1 for block in ledger.blocks if block.proposer == address)
            
            # LIVENESS DETECTION: Check if validator proposed a block recently
            # Find the most recent block proposed by this validator
            last_block_height = -1
            for block in reversed(ledger.blocks):
                if block.proposer == address:
                    last_block_height = block.height
                    break
            
            # Determine real-time online/offline status (same as leaderboard)
            if current_height < 10:
                # Bootstrap period - all registered validators shown as active
                display_status = "active"
            elif last_block_height >= 0 and (current_height - last_block_height) <= LIVENESS_WINDOW:
                # Proposed a block recently - ONLINE
                display_status = "active"
            else:
                # No recent blocks - OFFLINE
                display_status = "offline"
            
            # FILTER: Only show ACTIVE validators in network graph
            if display_status == "active":
                validators.append({
                    "id": address,
                    "label": f"{address[:10]}...",
                    "title": f"Address: {address}\\nBlocks: {block_count}\\nStatus: {display_status}",
                    "value": block_count + 10,  # Node size based on blocks proposed
                    "color": "#10b981"  # All shown validators are active (green)
                })
    
    # Create edges between validators (representing block proposals)
    # Connect each validator to the next proposer
    # Only include edges between ACTIVE validators
    active_addresses = {v["id"] for v in validators}
    for i in range(len(ledger.blocks) - 1):
        from_addr = ledger.blocks[i].proposer
        to_addr = ledger.blocks[i + 1].proposer
        if from_addr != to_addr and from_addr in active_addresses and to_addr in active_addresses:
            edges.append({"from": from_addr, "to": to_addr})
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TIMPAL Network Visualization</title>
        {get_base_styles()}
        {get_vis_js_cdn()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        
        <div class="header">
            <h1>🌐 TIMPAL Network Visualization</h1>
            <p>Interactive validator network graph - real-time topology</p>
        </div>
        
        {get_navigation_html("network")}
        
        <div class="card">
            <h2>🔗 Validator Network Graph</h2>
            <p style="color: var(--text-secondary);">
                Node size represents blocks proposed. Green = active, Gray = inactive. 
                Edges show block proposal sequence.
            </p>
            <div class="network-container" id="networkContainer"></div>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-label">Total Validators</div>
                <div class="stat-value">{len(validators)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Validators</div>
                <div class="stat-value">{sum(1 for v in validators if v['color'] == '#10b981')}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Network Edges</div>
                <div class="stat-value">{len(edges)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Topology</div>
                <div class="stat-value">Decentralized</div>
            </div>
        </div>
        
        <script>
            // Network data
            const nodes = new vis.DataSet({json.dumps(validators)});
            const edges = new vis.DataSet({json.dumps(edges)});
            
            // Container
            const container = document.getElementById('networkContainer');
            
            // Network options
            const options = {{
                nodes: {{
                    shape: 'dot',
                    font: {{
                        size: 14,
                        color: '#333'
                    }},
                    borderWidth: 2,
                    shadow: true
                }},
                edges: {{
                    width: 1,
                    color: {{ color: '#cccccc', opacity: 0.5 }},
                    smooth: {{ type: 'continuous' }},
                    arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }}
                }},
                physics: {{
                    enabled: true,
                    barnesHut: {{
                        gravitationalConstant: -8000,
                        springConstant: 0.001,
                        springLength: 200
                    }},
                    stabilization: {{
                        iterations: 150
                    }}
                }},
                interaction: {{
                    hover: true,
                    tooltipDelay: 100,
                    zoomView: true,
                    dragView: true
                }}
            }};
            
            // Create network
            const network = new vis.Network(container, {{ nodes: nodes, edges: edges }}, options);
            
            // Event handlers
            network.on('click', function(params) {{
                if (params.nodes.length > 0) {{
                    const nodeId = params.nodes[0];
                    window.location.href = '/address/' + nodeId;
                }}
            }});
            
            network.on('stabilizationIterationsDone', function() {{
                network.setOptions({{ physics: false }});
            }});
        </script>
    </body>
    </html>
    """
    
    return html


@app.get("/api-docs", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def api_documentation(request: Request):
    """API documentation page with examples"""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TIMPAL API Documentation</title>
        {get_base_styles()}
        {get_theme_toggle_script()}
    </head>
    <body>
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        
        <div class="header">
            <h1>📚 TIMPAL API Documentation</h1>
            <p>RESTful JSON API for blockchain data access</p>
        </div>
        
        {get_navigation_html("api")}
        
        <div class="card">
            <h2>🚀 Getting Started</h2>
            <p>All API endpoints return JSON data. No authentication required for read-only operations.</p>
            <p><strong>Base URL:</strong> <code class="monospace">http://localhost:8080</code></p>
            <p><strong>Rate Limiting:</strong> Varies by endpoint (10-30 requests/minute per IP)</p>
        </div>
        
        <div class="card">
            <h2>📊 Statistics Endpoints</h2>
            
            <h3>GET /stats</h3>
            <p>Get comprehensive blockchain statistics</p>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
{{
  "chain_height": 123,
  "total_blocks": 123,
  "total_transactions": 456,
  "total_supply": 78345000000,
  "total_supply_tmpl": "783.45000000 TMPL",
  "max_supply": 25000000000000000,
  "validator_count": 1,
  "current_phase": 1,
  "block_time": 2
}}
            </pre>
            
            <h3>GET /stream</h3>
            <p>Server-Sent Events (SSE) stream for real-time blockchain updates</p>
            <p><strong>Try it:</strong> <a href="/stream" target="_blank"><code>/stream</code></a> (opens in new tab)</p>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px;">
// JavaScript Example
const eventSource = new EventSource('/stream');
eventSource.onmessage = (event) => {{
  const data = JSON.parse(event.data);
  console.log('Latest block:', data.latest_block);
}};
            </pre>
        </div>
        
        <div class="card">
            <h2>🔍 Block Endpoints</h2>
            
            <h3>GET /blocks?limit=20&offset=0</h3>
            <p>Get list of recent blocks (max 100 per request)</p>
            <p><strong>Parameters:</strong></p>
            <ul>
                <li><code>limit</code> - Number of blocks to return (default: 20, max: 100)</li>
                <li><code>offset</code> - Number of blocks to skip from latest (default: 0)</li>
            </ul>
            
            <h3>GET /blocks/{{height}}</h3>
            <p>Get specific block by height</p>
            <p><strong>Example:</strong> <a href="/blocks/1"><code>/blocks/1</code></a></p>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
{{
  "height": 1,
  "block_hash": "abc123...",
  "timestamp": 1234567890,
  "proposer": "tmpl...",
  "reward": 63450000,
  "transactions": [...],
  "merkle_root": "...",
  "proposer_signature": "..."
}}
            </pre>
        </div>
        
        <div class="card">
            <h2>💳 Transaction Endpoints</h2>
            
            <h3>GET /tx/{{hash}}</h3>
            <p>Get transaction details by hash</p>
            <p><strong>Example:</strong> <a href="/tx/51966acccd7fe8a876c30ca8c8e7323f4273bcd96660ba7c07aaf55e5d30d6f2"><code>/tx/51966acccd7fe8a876c30ca8c8e7323f4273bcd96660ba7c07aaf55e5d30d6f2</code></a></p>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
{{
  "tx_hash": "...",
  "sender": "tmpl...",
  "recipient": "tmpl...",
  "amount": 100000000,
  "fee": 5000,
  "nonce": 0,
  "signature": "...",
  "block_height": 5,
  "confirmations": 10
}}
            </pre>
        </div>
        
        <div class="card">
            <h2>👛 Address Endpoints</h2>
            
            <h3>GET /address/{{address}}</h3>
            <p>Get address balance and transaction history</p>
            <p><strong>Example:</strong> <a href="/address/tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b"><code>/address/tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b</code></a></p>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
{{
  "address": "tmpl...",
  "balance": 500000000,
  "balance_tmpl": "5.00000000 TMPL",
  "transaction_count": 25,
  "sent_count": 10,
  "received_count": 15,
  "is_validator": true,
  "transactions": [...]
}}
            </pre>
        </div>
        
        <div class="card">
            <h2>🛡️ Validator Endpoints</h2>
            
            <h3>GET /validators</h3>
            <p>Get all registered validators with stats</p>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
{{
  "validators": [
    {{
      "address": "tmpl...",
      "public_key": "...",
      "balance": 123000000,
      "blocks_proposed": 45,
      "status": "active",
      "device_id_preview": "84fb5494281e2378..."
    }}
  ],
  "total_count": 1,
  "active_count": 1
}}
            </pre>
        </div>
        
        <div class="card">
            <h2>🔎 Search Endpoint</h2>
            
            <h3>GET /search/{{query}}</h3>
            <p>Universal search for blocks, transactions, or addresses</p>
            <p><strong>Supports:</strong></p>
            <ul>
                <li>Block heights (numbers)</li>
                <li>Block hashes</li>
                <li>Transaction hashes</li>
                <li>Addresses (starting with "tmpl")</li>
            </ul>
            <p><strong>Examples:</strong></p>
            <ul>
                <li><a href="/search/100"><code>/search/100</code></a> - Search for block 100</li>
                <li><a href="/search/tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b"><code>/search/tmpl6065...</code></a> - Search for address</li>
                <li><a href="/search/51966acccd7fe8a876c30ca8c8e7323f4273bcd96660ba7c07aaf55e5d30d6f2"><code>/search/51966acc...</code></a> - Search for transaction</li>
            </ul>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px;">
// Returns redirect information
{{ "type": "block", "height": 5, "redirect": "/blocks/5" }}
            </pre>
        </div>
        
        <div class="card">
            <h2>📋 Rate Limits</h2>
            <table class="table">
                <thead>
                    <tr>
                        <th>Endpoint</th>
                        <th>Rate Limit</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><code>/</code> (homepage)</td>
                        <td>30 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/stats</code></td>
                        <td>30 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/blocks</code></td>
                        <td>20 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/blocks/{{height}}</code></td>
                        <td>20 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/tx/{{hash}}</code></td>
                        <td>20 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/address/{{address}}</code></td>
                        <td>15 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/search/{{query}}</code></td>
                        <td>10 requests/minute</td>
                    </tr>
                    <tr>
                        <td><code>/stream</code> (SSE)</td>
                        <td>No limit (single connection)</td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>💡 Example Usage</h2>
            
            <h3>Python</h3>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
import requests

# Get blockchain stats
stats = requests.get('http://localhost:8080/stats').json()
print(f"Chain height: {{stats['chain_height']}}")

# Get latest blocks
blocks = requests.get('http://localhost:8080/blocks?limit=10').json()
for block in blocks['blocks']:
    print(f"Block #{{block['height']}} - {{block['transaction_count']}} txs")
            </pre>
            
            <h3>JavaScript (Fetch)</h3>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
// Get blockchain stats
fetch('http://localhost:8080/stats')
  .then(response => response.json())
  .then(data => {{
    console.log('Chain height:', data.chain_height);
    console.log('Validators:', data.validator_count);
  }});

// Real-time updates with SSE
const eventSource = new EventSource('/stream');
eventSource.onmessage = (event) => {{
  const data = JSON.parse(event.data);
  console.log('New block:', data.latest_block);
}};
            </pre>
            
            <h3>cURL</h3>
            <pre class="monospace" style="background: var(--bg-color); padding: 15px; border-radius: 5px; overflow-x: auto;">
# Get stats
curl http://localhost:8080/stats

# Get specific block
curl http://localhost:8080/blocks/1

# Search
curl http://localhost:8080/search/tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b
            </pre>
        </div>
    </body>
    </html>
    """
    
    return html


# =============================================================================
# DEBUG ENDPOINTS - Observability for liveness, proposers, and rewards
# =============================================================================

@app.get("/debug/liveness")
@limiter.limit("60/minute")
async def debug_liveness(request: Request):
    """
    DEBUG ENDPOINT: Get validator liveness state for debugging.
    
    Returns:
        JSON with all validators and their liveness state including:
        - offline_since_height: Block height when validator was marked offline (null if online)
        - blocks_offline: Number of blocks the validator has been offline
        - is_offline_for_rewards: Whether validator is excluded from rewards (10-block cutoff)
        - status: Validator status (active, genesis, pending, etc.)
        - last_proposed_height: Last block height proposed by this validator
        - last_reward_height: Last block height where validator received rewards
    """
    ledger = get_ledger()
    current_height = ledger.get_block_count() - 1
    
    # Get all validators from registry
    validators_liveness = {}
    
    for addr, data in ledger.validator_registry.items():
        if addr == "genesis":
            continue
        
        if not isinstance(data, dict):
            continue
        
        # Get offline_since_height from validator registry
        offline_since_height = data.get('offline_since_height')
        
        # Calculate blocks_offline
        if offline_since_height is not None:
            blocks_offline = current_height - offline_since_height
        else:
            blocks_offline = 0
        
        # Check if excluded from rewards (10-block cutoff)
        is_offline_for_rewards = ledger.is_validator_offline_for_rewards(addr, current_height)
        
        # Find last proposed block
        last_proposed_height = -1
        last_reward_height = -1
        for block in reversed(ledger.blocks):
            if last_proposed_height == -1 and block.proposer == addr:
                last_proposed_height = block.height
            if last_reward_height == -1 and block.reward_allocations and addr in block.reward_allocations:
                last_reward_height = block.height
            if last_proposed_height != -1 and last_reward_height != -1:
                break
        
        validators_liveness[addr] = {
            "status": data.get('status', 'unknown'),
            "offline_since_height": offline_since_height,
            "blocks_offline": blocks_offline,
            "is_offline_for_rewards": is_offline_for_rewards,
            "last_proposed_height": last_proposed_height,
            "last_reward_height": last_reward_height,
            "activation_height": data.get('activation_height'),
            "public_key": data.get('public_key', '')[:20] + '...' if data.get('public_key') else None
        }
    
    return {
        "current_height": current_height,
        "timestamp": time.time(),
        "offline_reward_cutoff_blocks": 10,
        "validators": validators_liveness,
        "summary": {
            "total_validators": len(validators_liveness),
            "online_validators": sum(1 for v in validators_liveness.values() if v['offline_since_height'] is None),
            "offline_validators": sum(1 for v in validators_liveness.values() if v['offline_since_height'] is not None),
            "excluded_from_rewards": sum(1 for v in validators_liveness.values() if v['is_offline_for_rewards'])
        }
    }


@app.get("/debug/proposers")
@limiter.limit("60/minute")
async def debug_proposers(request: Request):
    """
    DEBUG ENDPOINT: Get proposer rotation state for debugging.
    
    Returns:
        JSON with current proposer selection state including:
        - current_slot: Current time-sliced slot number
        - active_rank: Current active rank within the slot (0, 1, or 2)
        - ranked_proposers: Ordered list of proposers for current slot
        - is_single_validator_mode: Whether single-validator fast path is active
        - recent_proposers: Last N block proposers
        - fallback_events: Recent fallback proposer activations
    """
    ledger = get_ledger()
    current_height = ledger.get_block_count() - 1
    latest_block = ledger.get_latest_block()
    
    # Get genesis timestamp for slot calculations
    genesis_block = ledger.get_block_by_height(0)
    genesis_timestamp = genesis_block.timestamp if genesis_block else time.time()
    
    # Calculate current slot and rank
    current_time = time.time()
    try:
        from time_slots import current_slot_and_rank, WINDOW_SECONDS
        current_slot, active_rank = current_slot_and_rank(genesis_timestamp, current_time)
    except ImportError:
        current_slot = int((current_time - genesis_timestamp) / config.BLOCK_TIME)
        active_rank = 0
        WINDOW_SECONDS = 1.0
    
    # Get ranked proposers for current slot
    ranked_proposers = ledger.get_ranked_proposers_for_slot(current_slot, num_ranks=3)
    
    # Determine if single-validator mode would be active
    is_single_validator_mode = len(ranked_proposers) == 1
    
    # Get recent proposers (last 20 blocks)
    recent_proposers = []
    for block in ledger.blocks[-20:]:
        recent_proposers.append({
            "height": block.height,
            "slot": getattr(block, 'slot', None),
            "proposer": block.proposer,
            "timestamp": block.timestamp
        })
    
    # Detect fallback events (when proposer rank > 0)
    fallback_events = []
    for i, block in enumerate(ledger.blocks[-50:]):
        if i == 0:
            continue
        prev_block = ledger.blocks[-(50-i+1)] if len(ledger.blocks) > 50-i+1 else None
        if prev_block and block.slot and prev_block.slot:
            # If same slot but different proposer, it's a fallback
            if block.slot == prev_block.slot:
                fallback_events.append({
                    "height": block.height,
                    "slot": block.slot,
                    "proposer": block.proposer,
                    "reason": "same_slot_different_proposer"
                })
    
    # Get validator set for proposer selection
    validator_set = ledger.get_validator_set()
    
    return {
        "current_height": current_height,
        "timestamp": current_time,
        "genesis_timestamp": genesis_timestamp,
        "block_time": config.BLOCK_TIME,
        "window_seconds": WINDOW_SECONDS if 'WINDOW_SECONDS' in dir() else 1.0,
        "slot_info": {
            "current_slot": current_slot,
            "active_rank": active_rank,
            "latest_block_slot": getattr(latest_block, 'slot', None) if latest_block else None
        },
        "proposer_selection": {
            "ranked_proposers": ranked_proposers,
            "is_single_validator_mode": is_single_validator_mode,
            "validator_set_size": len(validator_set)
        },
        "recent_proposers": recent_proposers,
        "fallback_events": fallback_events[-10:],  # Last 10 fallback events
        "timing": {
            "time_since_genesis": current_time - genesis_timestamp,
            "expected_blocks": int((current_time - genesis_timestamp) / config.BLOCK_TIME),
            "actual_blocks": current_height + 1,
            "blocks_behind": max(0, int((current_time - genesis_timestamp) / config.BLOCK_TIME) - (current_height + 1))
        }
    }


@app.get("/debug/rewards")
@limiter.limit("60/minute")
async def debug_rewards(request: Request):
    """
    DEBUG ENDPOINT: Get reward distribution state for debugging.
    
    Returns:
        JSON with reward eligibility and distribution state including:
        - rewardable_validators: List of validators eligible for rewards
        - excluded_validators: List of validators excluded from rewards (and why)
        - recent_rewards: Last N blocks' reward distributions
        - reward_sources: How validators qualified for rewards (proposer, attestation, etc.)
    """
    ledger = get_ledger()
    current_height = ledger.get_block_count() - 1
    
    # Get online validators (deterministic)
    rewardable_validators = ledger.get_online_validators_deterministic(current_height)
    
    # Get all validators and determine exclusion reasons
    excluded_validators = []
    for addr, data in ledger.validator_registry.items():
        if addr == "genesis":
            continue
        if not isinstance(data, dict):
            continue
        
        if addr not in rewardable_validators:
            # Determine exclusion reason
            reasons = []
            
            status = data.get('status')
            if status not in ('active', 'genesis'):
                reasons.append(f"status={status}")
            
            offline_since = data.get('offline_since_height')
            if offline_since is not None:
                blocks_offline = current_height - offline_since
                if blocks_offline >= 10:
                    reasons.append(f"offline_for_{blocks_offline}_blocks")
                else:
                    reasons.append(f"offline_but_within_grace_{blocks_offline}_blocks")
            
            if not ledger.validator_economics.is_validator_active(addr, current_height):
                reasons.append("economics_inactive")
            
            excluded_validators.append({
                "address": addr,
                "reasons": reasons if reasons else ["unknown"],
                "offline_since_height": offline_since,
                "status": status
            })
    
    # Get recent reward distributions (last 20 blocks)
    recent_rewards = []
    for block in ledger.blocks[-20:]:
        if block.reward_allocations:
            total_reward = sum(block.reward_allocations.values())
            recent_rewards.append({
                "height": block.height,
                "proposer": block.proposer,
                "validators_rewarded": len(block.reward_allocations),
                "total_reward_pals": total_reward,
                "total_reward_tmpl": total_reward / 100_000_000,
                "per_validator_pals": total_reward // len(block.reward_allocations) if block.reward_allocations else 0,
                "recipients": list(block.reward_allocations.keys())
            })
    
    # Calculate reward sources for each rewardable validator
    reward_sources = {}
    proposer_lookback = max(30, len(rewardable_validators) * 2)
    recent_proposers = set()
    for block in ledger.blocks[-proposer_lookback:]:
        if block.proposer:
            recent_proposers.add(block.proposer)
    
    validators_with_attestations = ledger.get_validators_with_recent_attestations(lookback_blocks=100)
    
    for addr in rewardable_validators:
        sources = []
        if addr in recent_proposers:
            sources.append("recent_proposer")
        if addr in validators_with_attestations:
            sources.append("attestation")
        if not sources:
            sources.append("p2p_online")
        reward_sources[addr] = sources
    
    return {
        "current_height": current_height,
        "timestamp": time.time(),
        "offline_reward_cutoff_blocks": 10,
        "rewardable_validators": {
            "count": len(rewardable_validators),
            "addresses": rewardable_validators,
            "sources": reward_sources
        },
        "excluded_validators": {
            "count": len(excluded_validators),
            "validators": excluded_validators
        },
        "recent_rewards": recent_rewards,
        "summary": {
            "total_registered": len([a for a in ledger.validator_registry if a != "genesis"]),
            "currently_rewardable": len(rewardable_validators),
            "currently_excluded": len(excluded_validators)
        }
    }


@app.get("/api/health")
async def api_health():
    """
    Health check endpoint for sync and monitoring.
    
    Returns:
        JSON with node health status and current height
    """
    ledger = get_ledger()
    latest_block = ledger.get_latest_block()
    
    return {
        "status": "healthy",
        "height": latest_block.height if latest_block else 0,
        "blocks": len(ledger.blocks),
        "timestamp": time.time()
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run blockchain verification on startup using lifespan handler"""
    import glob
    
    print("🔍 Running blockchain integrity check...")
    
    data_dir = os.getenv("EXPLORER_DATA_DIR")
    if not data_dir:
        from pathlib import Path
        
        node_dirs = []
        
        # Check new default location: ~/.timpal/testnet_data_node_*/ledger
        timpal_home = Path.home() / ".timpal"
        if timpal_home.exists():
            node_dirs.extend(glob.glob(str(timpal_home / "testnet_data_node_*/ledger")))
        
        # Also check legacy location: ./testnet_data_node_*/ledger (for backwards compatibility)
        node_dirs.extend(glob.glob("testnet_data_node_*/ledger"))
        
        if node_dirs:
            data_dir = sorted(node_dirs, key=lambda x: os.path.getmtime(x), reverse=True)[0]
        else:
            data_dir = str(Path.home() / ".timpal" / "testnet_data_node_9000" / "ledger")
    
    try:
        ledger = Ledger(data_dir=data_dir, use_production_storage=False)
        
        if ledger.verify_chain():
            print("✅ Blockchain integrity verified - all blocks valid")
        else:
            print("❌ WARNING: Blockchain integrity check failed!")
        
        emitted_tmpl = ledger.total_emitted_pals / config.PALS_PER_TMPL
        print(f"📊 Loaded {len(ledger.blocks)} blocks, {emitted_tmpl:,.8f} {config.SYMBOL} emitted")
    except Exception as e:
        print(f"⚠️  Could not verify blockchain: {e}")
    
    print(f"🔒 Security: Rate limiting enabled, CORS restricted to localhost")
    print(f"⚡ Performance: Ledger caching enabled ({CACHE_TTL}s TTL), stats caching enabled")
    yield

# Update app to use lifespan
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TIMPAL Block Explorer")
    parser.add_argument("--port", type=int, default=5000, help="Port to run the explorer on (default: 5000)")
    args = parser.parse_args()
    
    print(f"Starting TIMPAL Block Explorer on port {args.port}...")
    print(f"Explorer URL: http://0.0.0.0:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
