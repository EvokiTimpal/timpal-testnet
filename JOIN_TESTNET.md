# 🚀 Join TIMPAL Blockchain Testnet as a Validator

## 💰 Earn Equal Rewards - True Decentralization

TIMPAL is a **100% decentralized blockchain** where every validator earns equal rewards, regardless of when they joined or who they are.

---

## 🎯 **Testnet Information**

| Parameter | Value |
|-----------|-------|
| **Repository** | `https://github.com/EvokiTimpal/timpal-testnet` |
| **Consensus** | Round-robin (all validators take turns) |
| **Block Time** | 3 seconds |
| **Reward per Block** | 0.6345 TMPL + transaction fees |
| **Distribution** | Equal among ALL active validators |
| **Network Discovery** | Peer-to-peer (no central servers) |

---

## ⚡ **Quick Start (5 Minutes)**

### **Universal Installation (macOS, Windows, Linux, Docker)**

TIMPAL Genesis uses **pure-Python storage** - no system dependencies needed!

```bash
# 1. Clone repository
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet

# 2. Install dependencies (Python 3.8+ required)
pip install -r requirements.txt

# 3. Set your wallet PIN (minimum 6 characters)
export TIMPAL_WALLET_PIN="your_secure_pin"

# 4. Start your validator node
python3 run_testnet_node.py --port 9000

# Your node will automatically discover peers and join the network!
```

**That's it - works identically on all platforms!** 🎉

### **Docker Installation**

```bash
# Build and run
docker build -t timpal-testnet .
docker run -e TIMPAL_WALLET_PIN="your_pin" -p 9000:9000 -p 9001:9001 timpal-testnet
```

**That's it! You're now earning equal rewards through P2P network!** 🎉

---

## 💎 **Why Join?**

### **1. Equal Rewards**
Every validator earns the same - no advantage for early joiners or block proposers:
```
Per Validator = (0.6345 TMPL + Transaction Fees) ÷ Validator Count
```

**Example with 10 validators:**
- Block reward: 0.6345 TMPL
- Transaction fees: 0.001 TMPL  
- Your reward: `(0.6345 + 0.001) / 10 = 0.06355 TMPL` per block
- All 10 validators earn exactly the same!

### **2. Permissionless**
- ✅ No approval needed
- ✅ No staking required
- ✅ No KYC/identity verification
- ✅ No whitelist
- ✅ Just run the software!

### **3. Fair & Decentralized**
- 🌐 One validator per device (Sybil resistance)
- 🔄 Round-robin consensus (everyone participates equally)
- 🔒 Open source (verify the code yourself)
- 👥 Community-driven (no central authority)

### **4. Learn & Contribute**
- 🧠 Hands-on blockchain experience
- 🛠️ Help build decentralized infrastructure
- 👨‍💻 Contribute to open source project
- 🚀 Be part of testnet → mainnet journey

---

## 🔧 **Requirements**

### **Hardware**
- **Minimum:** Raspberry Pi 3/4, old laptop, VPS
- **Recommended:** Modern computer with stable internet
- **Storage:** 10GB+ (grows with blockchain)
- **Memory:** 1GB+ RAM
- **Network:** Stable internet (speed not critical)

### **Software**
- Python 3.8 or higher
- Linux, macOS, or Windows (WSL)
- Command line access

### **Cost**
- **Cloud VPS:** ~$5-10/month (DigitalOcean, Linode, etc.)
- **Home computer:** Just electricity (~$2-5/month)

**Rewards will cover costs after mainnet launch!**

---

## 📊 **Monitor Your Validator**

### **Check Your Balance**
```bash
python3 wallet_cli.py
```

### **Verify Registration**
```bash
python3 << 'EOF'
from app.ledger import Ledger
validators = Ledger().get_active_validators()
print(f"Total validators: {len(validators)}")
for v in validators:
    print(f"  - {v}")
EOF
```

---

## 🛡️ **Security**

### **Sybil Attack Prevention**
- One validator per device (enforced by hardware fingerprint)
- Prevents one person from controlling majority
- Ensures true decentralization

### **Your Wallet Security**
- 🔐 12-word recovery phrase generated during setup
- 📝 **WRITE IT DOWN** and store securely offline
- 💾 Backup `app/wallet.dat` file
- 🔒 Never share your recovery phrase or PIN

### **Network Security**
- ✅ All P2P messages signed and authenticated
- ✅ Blocks cryptographically signed by validators
- ✅ Double-spend prevention via nonce
- ✅ Consensus requires majority agreement

---

## 🎯 **Testnet Goals**

We're testing:
- ✅ Multi-validator consensus
- ✅ Equal reward distribution
- ✅ P2P network stability
- ✅ Transaction processing
- ✅ Scalability (target: 100+ validators)
- ✅ Geographic distribution
- ✅ Crash recovery and persistence

**Help us prove the network is production-ready!**

---

## 📞 **Support & Community**

- **Documentation:** [TESTNET_README.md](TESTNET_README.md)
- **Issues:** [GitHub Issues](https://github.com/EvokiTimpal/timpal-testnet/issues)

---

## ❓ **FAQ**

### **Q: How many TMPL will I earn?**
**A:** Depends on validator count and transaction volume:
- With 10 validators: ~0.0635 TMPL per block
- With 100 validators: ~0.00635 TMPL per block  
- All validators earn equally!

### **Q: Can I run multiple validators?**
**A:** No - device fingerprint enforces 1 validator per device (Sybil resistance). But you can create unlimited non-validator wallets.

### **Q: What if my validator goes offline?**
**A:** You won't earn rewards while offline. Network continues with remaining validators. When you come back online, you sync and resume earning.

### **Q: Is there a minimum uptime requirement?**
**A:** No strict requirement for testnet. For mainnet, higher uptime = more rewards earned (but rate is still equal when online).

### **Q: Can I run this on Raspberry Pi?**
**A:** Yes! Raspberry Pi 3/4 with 1GB+ RAM works great. Low power consumption, perfect for 24/7 operation.

### **Q: How do I update my node?**
**A:** Pull latest code from repository, restart node. Your blockchain data and wallet persist.

### **Q: When is mainnet launch?**
**A:** After 30-90 days of successful testnet operation with 100+ validators and security audit.

---

## 🎉 **Join Now!**

```bash
# Quick start
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
pip install -r requirements.txt
python3 wallet_cli.py
python3 run_testnet_node.py
```

**Welcome to truly decentralized blockchain!** 🚀

---

## 📈 **Current Testnet Stats**

Check stats directly from your node:

```bash
# View all validators
python3 << 'EOF'
from app.ledger import Ledger
validators = Ledger().get_active_validators()
print(f"Active Validators: {len(validators)}")
print(f"Current Height: {Ledger().get_height()}")
EOF
```

**Be part of blockchain history - run a validator today!** 💪
