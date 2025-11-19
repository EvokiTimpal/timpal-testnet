#!/usr/bin/env python3
"""
TIMPAL Block Explorer Launcher
Handles import paths correctly for both development and production
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
    from explorer import app as explorer_app
    
    print(f"Starting TIMPAL Block Explorer on port {args.port}...")
    print(f"Explorer URL: http://0.0.0.0:{args.port}")
    print(f"Press Ctrl+C to stop")
    
    uvicorn.run(explorer_app, host="0.0.0.0", port=args.port, log_level="info")
