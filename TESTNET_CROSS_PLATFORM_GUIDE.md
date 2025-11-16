# TIMPAL Testnet - Universal Cross-Platform Setup Guide

## 🎯 Overview

TIMPAL uses **pure-Python JSON storage** that works identically on **all platforms** without any system dependencies or compilation.

### Key Features
- ✅ **Universal Compatibility**: Works on macOS, Windows, Linux, Docker, and Replit
- ✅ **Zero Dependencies**: No LevelDB, no LMDB, no C++ compilation required
- ✅ **Identical Behavior**: Same code runs everywhere without platform-specific configuration
- ✅ **Simple Setup**: Just install Python packages and run

---

## 📦 Installation

### 1. Download and Extract
```bash
# Download the TIMPAL testnet package
# Extract the ZIP file
cd timpal-testnet
```

### 2. Install Dependencies

**All platforms (macOS, Windows, Linux):**
```bash
pip install -r requirements.txt
```

That's it! No platform-specific dependencies needed.

---

## 🚀 Running the Testnet

### Quick Start (3 Nodes)

#### Terminal 1 - Node 1 (Bootstrap):
```bash
export TIMPAL_WALLET_PIN="your_secure_pin_123"
python run_testnet_node.py --port 9000
```

#### Terminal 2 - Node 2:
```bash
export TIMPAL_WALLET_PIN="your_secure_pin_456"
python run_testnet_node.py --port 3000 --seed ws://localhost:9000
```

#### Terminal 3 - Node 3:
```bash
export TIMPAL_WALLET_PIN="your_secure_pin_789"
python run_testnet_node.py --port 3001 --seed ws://localhost:9000
```

---

## 🔧 How the Pure-Python Storage Works

### Simple JSON Files

All blockchain data is stored as JSON files in the `ledger/` directory:

```
testnet_data_node_9000/
└── ledger/
    ├── metadata.json      # Chain metadata (height, hash, etc.)
    ├── blocks_*.json      # Block data (chunked for performance)
    └── state.json         # Account balances and nonces
```

### Atomic File Operations

TIMPAL uses atomic writes for crash safety:

```python
# 1. Write to temporary file
# 2. Use os.replace() to atomically swap files
# 3. If crash occurs, either old or new file exists (never corrupted)
```

### Cross-Platform Guarantees

- ✅ Same JSON format on all platforms
- ✅ No binary files or platform-specific formats
- ✅ Human-readable (you can inspect the blockchain with any text editor)
- ✅ Portable (copy `ledger/` folder to any platform)

---

## 📁 Directory Structure

```
timpal-testnet/
├── app/
│   ├── storage_basic.py          # Pure-Python JSON storage
│   ├── ledger.py                 # Blockchain ledger (uses storage_basic)
│   ├── node.py                   # P2P node implementation
│   ├── wallet_cli.py             # Wallet CLI tool
│   └── config_testnet.py         # Testnet configuration
├── run_testnet_node.py           # Testnet launcher
├── requirements.txt              # Python dependencies (pure-Python only)
├── Dockerfile                    # Docker deployment
└── docs/
    ├── JOIN_TESTNET.md          # Quick start guide
    ├── DOCKER_QUICKSTART.md     # Docker deployment
    └── TESTNET_CROSS_PLATFORM_GUIDE.md  # This file
```

---

## 🔍 Platform-Specific Notes

### All Platforms (macOS, Windows, Linux)

**Storage Backend**: Pure-Python JSON files
- **Install**: `pip install -r requirements.txt`
- **Advantages**:
  - Zero compilation or system dependencies
  - Works on any Python 3.8+ installation
  - Identical behavior everywhere
  - Easy debugging (human-readable JSON)
- **Performance**: Optimized with chunked files and atomic writes
- **Data Location**: `testnet_data_*/ledger/`

**No platform-specific configuration needed!**

---

## 🎓 For Developers

### Storage Architecture

TIMPAL's `storage_basic.py` provides:

1. **Metadata Storage**: Chain height, latest hash
2. **Block Storage**: Chunked JSON files (1000 blocks per file)
3. **State Storage**: Account balances and transaction nonces
4. **Atomic Operations**: Crash-safe writes using temp files

### API

```python
from storage_basic import BlockchainStorage

storage = BlockchainStorage("ledger/")

# Save a new block
storage.save_new_block(block_dict, state_dict)

# Load blockchain state
metadata, blocks, state = storage.load_state()

# Get height
height = storage.get_height()
```

### Adding Custom Storage Backends

To use a different storage backend (e.g., SQLite, PostgreSQL):

1. Implement the same API as `storage_basic.py`
2. Update `app/ledger.py` to import your storage module
3. Keep the same method signatures for compatibility

---

## ✅ Cross-Platform Verification

### Test the Setup

```bash
# Verify Python version
python --version  # Should be 3.8 or higher

# Install dependencies
pip install -r requirements.txt

# Check wallet creation
python app/wallet_cli.py

# Start a test node
export TIMPAL_WALLET_PIN="test123"
python run_testnet_node.py --port 9000
```

### Expected Output

```
✅ Using storage backend: storage_basic (Pure-Python JSON)
✅ Loaded state: height=0
✅ Genesis block created
✅ Validator registered: tmpl...
✅ Node started on port 9000
```

---

## 🐛 Troubleshooting

### "No module named 'ecdsa'"
```bash
pip install -r requirements.txt
```

### "Permission denied" when creating files
```bash
# Make sure you have write permissions in the current directory
chmod u+w .
```

### Port already in use
```bash
# Use a different port
python run_testnet_node.py --port 9001
```

### Blockchain data location
```bash
# Data is stored in:
testnet_data_node_<PORT>/ledger/

# To reset blockchain:
rm -rf testnet_data_node_9000/
```

---

## 📊 Performance Characteristics

| Feature | Pure-Python JSON Storage |
|---------|-------------------------|
| Setup Time | Instant (no compilation) |
| Read Performance | Fast (chunked files) |
| Write Performance | Fast (atomic operations) |
| Memory Usage | Low |
| Disk Usage | Efficient (compressed JSON) |
| Crash Recovery | Yes (atomic writes) |
| Cross-Platform | ✅ 100% compatible |
| Human Readable | ✅ Yes (JSON format) |

---

## ✅ Summary

TIMPAL testnet now supports **100% universal operation**:

- ✅ **All platforms**: macOS, Windows, Linux, Docker, Replit
- ✅ **Zero compilation**: Pure-Python dependencies only
- ✅ **Identical behavior**: Same code runs everywhere
- ✅ **Simple setup**: `pip install -r requirements.txt`
- ✅ **Portable**: Copy `ledger/` folder between platforms

**No platform-specific workarounds or configuration needed!**

---

## 📞 Support

For issues or questions:
1. Check troubleshooting section above
2. Review `JOIN_TESTNET.md` for quick start
3. Check `DOCKER_QUICKSTART.md` for Docker deployment
4. Visit GitHub Issues for community support

---

*Built with TIMPAL's core principle: Absolute Fairness - now with universal cross-platform accessibility*
