# 🍎 TIMPAL Testnet - macOS Quick Start Guide

Run the TIMPAL testnet on your Mac in 2 minutes - **no Docker required!**

---

## ✅ Prerequisites

- **macOS** (Ventura, Sonoma, or later)
- **Python 3.8+** (check with `python3 --version`)
- **Git** (check with `git --version`)

---

## 🚀 Quick Start (Native macOS)

### 1. Clone the repository

```bash
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
```

### 2. Create Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note**: TIMPAL now uses **pure-Python JSON storage** - works identically on all platforms!

### 4. Launch the testnet node

```bash
export TIMPAL_WALLET_PIN="your_secure_pin"
python3 run_testnet_node.py --port 9000
```

**That's it!** You should see:

```
✅ Using storage backend: storage_basic (Pure-Python JSON)
✅ Creating new validator wallet...
✅ Wallet created successfully
✅ Node started on port 9000
✅ HTTP API available on port 9001
```

---

## 🌐 Join the Network

### Connect to Other Nodes

```bash
# If you know another node's address:
python3 run_testnet_node.py --port 9000 --seed ws://other-node-ip:9000
```

### Run Multiple Local Nodes

**Terminal 1 - Genesis Node:**
```bash
export TIMPAL_WALLET_PIN="pin123"
python3 run_testnet_node.py --port 9000
```

**Terminal 2 - Validator Node 2:**
```bash
export TIMPAL_WALLET_PIN="pin456"
python3 run_testnet_node.py --port 3000 --seed ws://localhost:9000
```

**Terminal 3 - Validator Node 3:**
```bash
export TIMPAL_WALLET_PIN="pin789"
python3 run_testnet_node.py --port 3001 --seed ws://localhost:9000
```

---

## 📊 Check Your Wallet

```bash
python3 app/wallet_cli.py
```

---

## 🛑 Stop Your Node

Press `Ctrl+C` in the terminal where the node is running.

---

## 🔧 Troubleshooting

### Port Already in Use

```bash
# Use a different port
python3 run_testnet_node.py --port 9001
```

### Python Version Too Old

```bash
# Check Python version
python3 --version

# Should be 3.8 or higher
# If not, install a newer Python from python.org
```

### Missing Dependencies

```bash
# Reinstall all dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Reset Blockchain Data

```bash
# Remove blockchain data directory
rm -rf testnet_data_node_9000/

# Then restart the node
python3 run_testnet_node.py --port 9000
```

---

## 📁 Data Storage

Your blockchain data is stored in:
```
testnet_data_node_<PORT>/
└── ledger/
    ├── metadata.json      # Chain metadata
    ├── blocks_*.json      # Block data
    └── state.json         # Account balances
```

All files are **human-readable JSON** - you can inspect them with any text editor!

---

## ✅ Cross-Platform Compatibility

TIMPAL uses **pure-Python JSON storage** that works identically on:
- ✅ macOS (Intel & Apple Silicon)
- ✅ Windows
- ✅ Linux
- ✅ Docker
- ✅ Replit

No platform-specific dependencies or compilation required!

---

## 📞 Support

- **Quick Start:** [JOIN_TESTNET.md](JOIN_TESTNET.md)
- **Docker Guide:** [DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md)
- **GitHub Issues:** https://github.com/EvokiTimpal/timpal-testnet/issues

---

*TIMPAL Genesis - Decentralized. Immutable. Fair.*
