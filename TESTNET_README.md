# 🌐 TIMPAL Testnet - Cross-Platform Blockchain Testing Network

**Run a TIMPAL validator node on macOS, Windows, Linux, or Docker in under 5 minutes!**

---

## 🚀 Quick Start (Choose Your Platform)

### Universal Installation (All Platforms)

TIMPAL now uses **pure-Python JSON storage** - works identically everywhere!

```bash
# Clone repository
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet

# Install dependencies (pure Python only)
pip install -r requirements.txt

# Set wallet PIN
export TIMPAL_WALLET_PIN="your_secure_pin"

# Run node
python run_testnet_node.py --port 9000
```

**That's it - works the same on macOS, Windows, and Linux!** 🎉

---

### Docker (Optional)

```bash
# Clone repository
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet

# Build image
docker build -t timpal-testnet .

# Run node
docker run -p 9000:9000 -p 9001:9001 \
  -e TIMPAL_WALLET_PIN=my_secure_pin \
  timpal-testnet
```

**See [DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md) for detailed guide**

---

## ✅ What You'll See

When your node starts successfully:

```
================================================================================
TIMPAL TESTNET NODE
================================================================================

✅ Using storage backend: storage_basic (Pure-Python JSON)
✅ Loaded state: height=0
✅ Creating new validator wallet...
✅ Wallet created: tmpl8715a4be34446be1a3f09922dcc41c4c2a30e9e9b89d
✅ Validator registered successfully
🚀 Node started on port 9000
🌐 HTTP API available on port 9001
✅ P2P server listening on port 9000
```

---

## 🎯 What is TIMPAL Testnet?

### Core Principles

- **100% Decentralized** - No central servers, no governance committee
- **Equal Rewards** - Every validator earns the same, regardless of when they joined
- **Permissionless** - Anyone can join, no approval needed
- **Cross-Platform** - Works identically on all platforms

### Network Specifications

| Parameter | Value |
|-----------|-------|
| **Chain ID** | `timpal-testnet` |
| **Block Time** | 3 seconds |
| **Consensus** | Round-robin (all validators take turns) |
| **Reward per Block** | 0.6345 TMPL + transaction fees |
| **Distribution** | Equal among ALL active validators |

---

## 🌐 Join the Network

### Connect to Existing Nodes

If you know another node's address:

```bash
python run_testnet_node.py --port 9000 --seed ws://other-node-ip:9000
```

### Run Multiple Local Nodes

**Terminal 1 - Genesis Node:**
```bash
export TIMPAL_WALLET_PIN="pin123"
python run_testnet_node.py --port 9000
```

**Terminal 2 - Validator Node 2:**
```bash
export TIMPAL_WALLET_PIN="pin456"
python run_testnet_node.py --port 3000 --seed ws://localhost:9000
```

**Terminal 3 - Validator Node 3:**
```bash
export TIMPAL_WALLET_PIN="pin789"
python run_testnet_node.py --port 3001 --seed ws://localhost:9000
```

---

## 💎 Why Pure-Python Storage?

### ✅ Benefits

1. **Universal Compatibility**
   - macOS (Intel & Apple Silicon) ✅
   - Windows ✅
   - Linux (x86_64 & ARM64) ✅
   - Docker ✅
   - Replit ✅

2. **Zero Compilation**
   - No C++ compiler required
   - No platform-specific dependencies
   - Just `pip install` and go

3. **Simple Debugging**
   - Human-readable JSON files
   - Inspect blockchain with any text editor
   - Easy to understand and verify

4. **Crash-Safe**
   - Atomic file operations
   - No data corruption on crashes
   - Automatic recovery

### 🗂️ Data Storage

Your blockchain data is stored in:
```
testnet_data_node_<PORT>/
└── ledger/
    ├── metadata.json      # Chain metadata (height, hash)
    ├── blocks_0.json      # First 1000 blocks
    ├── blocks_1000.json   # Next 1000 blocks
    └── state.json         # Account balances and nonces
```

All files are **human-readable JSON** - inspect them anytime!

---

## 📊 Monitor Your Validator

### Check Your Wallet Balance

```bash
python app/wallet_cli.py
```

### View Blockchain Status

```bash
python -c "
from app.ledger import Ledger
ledger = Ledger()
print(f'Height: {ledger.get_height()}')
print(f'Validators: {len(ledger.get_active_validators())}')
"
```

### HTTP API Endpoints

Your node exposes an HTTP API on port `<NODE_PORT + 1>`:

```bash
# Get blockchain height
curl http://localhost:9001/api/height

# Get latest block
curl http://localhost:9001/api/blocks/latest

# Get validators
curl http://localhost:9001/api/validators

# Check node health
curl http://localhost:9001/api/health
```

---

## 🛡️ Security

### Wallet Security
- 🔐 12-word recovery phrase (write it down!)
- 📝 Encrypted with your PIN
- 💾 Backed up in `testnet_data_node_*/wallet.json`

### Network Security
- ✅ All messages cryptographically signed
- ✅ P2P authentication with ECDSA
- ✅ Sybil resistance (one validator per device)
- ✅ Consensus requires majority agreement

---

## 🐛 Troubleshooting

### Port Already in Use
```bash
# Use a different port
python run_testnet_node.py --port 9001
```

### Missing Dependencies
```bash
pip install -r requirements.txt
```

### Reset Blockchain Data
```bash
# Remove data directory
rm -rf testnet_data_node_9000/

# Restart node
python run_testnet_node.py --port 9000
```

### Python Version Too Old
```bash
# Check Python version
python --version  # Should be 3.8 or higher

# Upgrade Python from python.org
```

### "Permission denied" creating files
```bash
# Ensure write permissions
chmod u+w .
```

---

## 📁 Project Structure

```
timpal-testnet/
├── app/
│   ├── storage_basic.py          # Pure-Python JSON storage
│   ├── ledger.py                 # Blockchain ledger
│   ├── node.py                   # P2P node implementation
│   ├── wallet_cli.py             # Wallet management
│   ├── config_testnet.py         # Testnet configuration
│   └── ...
├── run_testnet_node.py           # Testnet launcher
├── requirements.txt              # Dependencies
├── Dockerfile                    # Docker deployment
└── docs/
    ├── JOIN_TESTNET.md          # Quick start guide
    ├── DOCKER_QUICKSTART.md     # Docker guide
    ├── MACOS_README.md          # macOS guide
    └── TESTNET_README.md        # This file
```

---

## 🎯 Testnet Goals

We're testing:
- ✅ Multi-validator consensus
- ✅ Equal reward distribution
- ✅ P2P network stability
- ✅ Transaction processing
- ✅ Cross-platform compatibility
- ✅ Crash recovery and persistence
- ✅ Scalability (target: 100+ validators)

**Help us prove the network is production-ready!**

---

## 📞 Support & Community

- **Quick Start:** [JOIN_TESTNET.md](JOIN_TESTNET.md)
- **Docker Guide:** [DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md)
- **GitHub Issues:** https://github.com/EvokiTimpal/timpal-testnet/issues

---

## ❓ FAQ

### Q: How many TMPL will I earn?
**A:** Depends on validator count:
- With 10 validators: ~0.0635 TMPL per block
- With 100 validators: ~0.00635 TMPL per block
- All validators earn equally!

### Q: Can I run multiple validators?
**A:** No - device fingerprint enforces 1 validator per device (Sybil resistance).

### Q: What if my validator goes offline?
**A:** You won't earn rewards while offline. Network continues with remaining validators. When you come back online, you sync and resume earning.

### Q: Can I run this on Raspberry Pi?
**A:** Yes! Raspberry Pi 3/4 with 1GB+ RAM works great.

### Q: Is blockchain data portable?
**A:** Yes! Just copy the `testnet_data_node_*/` folder to another machine and run the node there.

### Q: How do I update my node?
**A:** Pull latest code from repository, restart node. Your blockchain data and wallet persist.

---

## 🎉 Join Now!

```bash
# Quick start
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
pip install -r requirements.txt
export TIMPAL_WALLET_PIN="your_secure_pin"
python run_testnet_node.py --port 9000
```

**Welcome to truly decentralized blockchain!** 🚀

---

*TIMPAL Genesis - Decentralized. Immutable. Fair.*
