#!/usr/bin/env python3
"""
TIMPAL Block Explorer Launcher

The Block Explorer provides a web interface for viewing blockchain data
and submitting transactions.

IMPORTANT: The explorer needs to connect to your node's HTTP API port!

📡 PORT CONFIGURATION:
    Your blockchain node creates TWO ports:
    - P2P Network: --port value (e.g., 8001)
    - HTTP API: --port + 1 (e.g., 8002) ← Explorer connects here!
    
    By default, the explorer looks for HTTP API on port 9001.
    
    If your node is on a different port, set EXPLORER_API_PORT:
    
    # Node on port 8001 → API on 8002
    export EXPLORER_API_PORT=8002
    python3 start_explorer.py --port 8080
    
    # Node on port 9005 → API on 9006
    export EXPLORER_API_PORT=9006
    python3 start_explorer.py --port 8080

TROUBLESHOOTING:
    If you get "Cannot connect to host" or "Failed to fetch account info":
    → See TROUBLESHOOTING.md for detailed help
"""
import sys
import os
import argparse
from pathlib import Path

# Add app directory to Python path (so 'import config' works)
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "app"))

# Load testnet config BEFORE importing explorer
import app.config_testnet as config_testnet
sys.modules["config"] = config_testnet

# Now import and run the explorer
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TIMPAL Block Explorer")
    parser.add_argument("--port", type=int, default=8080, help="Port to run the explorer on (default: 8080)")
    args = parser.parse_args()
    
    # Import uvicorn and explorer after path is set
    import uvicorn
    from app.explorer import app as explorer_app
    
    # Get the node API port from environment
    node_api_port = os.getenv("EXPLORER_API_PORT", "9001")
    
    print(f"Starting TIMPAL Block Explorer on port {args.port}...")
    print(f"Explorer URL: http://0.0.0.0:{args.port}")
    print(f"")
    print(f"📡 Configuration:")
    print(f"   Explorer Web UI: http://0.0.0.0:{args.port}")
    print(f"   Node HTTP API:   http://localhost:{node_api_port}")
    print(f"")
    if node_api_port == "9001":
        print(f"ℹ️  Using default API port 9001 (for node on port 9000)")
        print(f"   If your node is on a different port, set EXPLORER_API_PORT")
        print(f"   Example: export EXPLORER_API_PORT=8002")
    else:
        print(f"✓ Using custom API port {node_api_port} (EXPLORER_API_PORT is set)")
    print(f"")
    print(f"Press Ctrl+C to stop")
    print(f"")
    
    uvicorn.run(explorer_app, host="0.0.0.0", port=args.port, log_level="info")
