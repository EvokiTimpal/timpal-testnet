#!/usr/bin/env python3
"""
TIMPAL Block Explorer Launcher
Handles import paths correctly for both development and production
"""
import sys
import os
import argparse

# Add project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Now import and run the explorer
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TIMPAL Block Explorer")
    parser.add_argument("--port", type=int, default=8080, help="Port to run the explorer on (default: 8080)")
    args = parser.parse_args()
    
    # Import uvicorn and app after path is set
    import uvicorn
    from app import explorer
    
    print(f"Starting TIMPAL Block Explorer on port {args.port}...")
    print(f"Explorer URL: http://0.0.0.0:{args.port}")
    print(f"Press Ctrl+C to stop")
    
    uvicorn.run(explorer.app, host="0.0.0.0", port=args.port, log_level="info")
