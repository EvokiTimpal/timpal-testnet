# 🚀 Join the TIMPAL Testnet as a Validator

Welcome to the TIMPAL blockchain testnet!  
Running a validator node is fast, simple, and requires no technical experience.

This guide shows **exactly** how to start a validator node on macOS, Windows, Linux, or Docker.

---

# ⭐ What You Will Do
1. Clone the TIMPAL Testnet repository  
2. Install Python requirements  
3. Create your validator wallet  
4. Set your wallet PIN  
5. Start your validator node  

That's it — your node will automatically join the P2P network and start earning testnet rewards.

---

# 🔧 Requirements
- Python 3.8 or higher  
- Internet connection  
- Mac, Windows (WSL2), Linux, or Docker  

---

# 🟦 1. Clone the repository

```bash
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
```

---

# 🟩 2. Install dependencies

```bash
pip install -r requirements.txt
```

---

# 🟨 3. Create your validator wallet

A wallet is required to operate a validator.  
Run this command and **write down** your:

- **Wallet address**
- **12-word recovery phrase**
- **PIN you choose later**

```bash
python3 wallet_cli.py
```

---

# 🟧 4. Set your wallet PIN

Your node uses this PIN to unlock the wallet during startup.

```bash
export TIMPAL_WALLET_PIN="your_secure_pin"
```

(Use `set` instead of `export` on Windows.)

---

# 🟥 5. Start your validator node

```bash
python3 run_testnet_node.py --port 9000
```

Your node will:

- Create local blockchain storage  
- Register automatically as a validator  
- Connect to the testnet P2P network  
- Start validating and earning testnet rewards  

You will see logs similar to:

```
Starting node...
Node is running!
Registered validator tmplxxxxxxxx...
Creating or syncing blockchain...
```

---

# 🎉 You're now part of the TIMPAL Testnet!

Your validator begins participating in the network immediately.

To stop the node:  
Press **Ctrl + C**

To restart the node:  
Run the same command:

```bash
export TIMPAL_WALLET_PIN="your_pin"
python3 run_testnet_node.py --port 9000
```

---

# 📌 Optional Commands

## Check your balance
```bash
python3 wallet_cli.py
```

## Check active validators
```bash
python3 << 'EOF'
from app.ledger import Ledger
v = Ledger().get_active_validators()
print("Active validators:", len(v))
for x in v:
    print("-", x)
EOF
```

---

# ❗ Important Notes
- One validator per device (Sybil-resistant design)  
- Your wallet PIN must always be set before running the node  
- **Do NOT delete your 12-word recovery phrase**  
- **Do NOT share your PIN or recovery phrase**  

---

# 🧰 Need Help?
Open an issue on the GitHub repository.  
We're building a fair, equal-reward blockchain — welcome aboard!
