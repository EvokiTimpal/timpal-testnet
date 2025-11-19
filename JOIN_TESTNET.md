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

**First, navigate to your Desktop:**
```bash
cd ~/Desktop
```

**Then clone the repository:**
```bash
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
```

(Windows users: use `cd %USERPROFILE%\Desktop` instead)

---

# 🟩 2. Install dependencies

```bash
pip install -r requirements.txt
```

**For older systems (macOS, older Linux):**  
If you see `command not found: pip`, use `pip3` instead:
```bash
pip3 install -r requirements.txt
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

**For older systems (macOS, older Python):**  
If you see `ModuleNotFoundError: No module named 'config'`, set PYTHONPATH first:
```bash
export PYTHONPATH="$PWD/app:$PWD"
python3 wallet_cli.py
```

---

# 🟧 4. Choose a secure PIN

Your node uses this PIN to unlock the wallet during startup.

**Choose a 6-digit PIN** (example: 123456) and remember it.  
You'll need to export it **every time** before starting the node.

(We'll set it in the next step before starting the node.)

---

# 🟥 5. Start your validator node

**First, set your wallet PIN:**
```bash
export TIMPAL_WALLET_PIN="your_secure_pin"
```

**Then start the node:**
```bash
python3 run_testnet_node.py --port 3000 --seed ws://143.110.129.211:9000
```

**Important:**
- You **MUST** export your PIN before running the node (every time)
- `--port 3000` - Your local P2P port (can be any available port like 3000, 8001, 8002, etc.)
- `--seed ws://143.110.129.211:9000` - Official TIMPAL Testnet bootstrap node (REQUIRED)

Your node will:

- Create local blockchain storage  
- Connect to the bootstrap node at 143.110.129.211:9000
- Sync the blockchain from the network
- Register automatically as a validator  
- Start validating and earning testnet rewards  

You will see logs similar to:

```
Starting node...
Node is running!
🔄 P2P: Attempting to connect to ws://143.110.129.211:9000...
✅ P2P: Connected successfully!
🤝 P2P: Completed handshake with peer...
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
python3 run_testnet_node.py --port 3000 --seed ws://143.110.129.211:9000
```

---

# 📌 Optional Commands

## Start the Block Explorer

View the blockchain in your browser with the TIMPAL Block Explorer:

```bash
python3 start_explorer.py --port 8080
```

Then open your browser and visit:
```
http://localhost:8080
```

(Port 8080 is used instead of 5000 because macOS blocks port 5000 for AirPlay)

**What you can see:**
- 📊 Chain statistics (total blocks, validators, transactions)
- 🔍 Search blocks by height or hash
- 💰 View transactions and transfers
- 👥 Browse all validators and their balances
- 📈 Network activity charts
- ⛓️ Real-time blockchain updates

Press **Ctrl + C** to stop the explorer.

---

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
- **Port 9000 is reserved for the bootstrap node** - Use a different port (3000, 8001, 8002, etc.)
- **Always include --seed ws://143.110.129.211:9000** to connect to the testnet
- One validator per device (Sybil-resistant design)  
- Your wallet PIN must always be set before running the node  
- **Do NOT delete your 12-word recovery phrase**  
- **Do NOT share your PIN or recovery phrase**  

---

# 🔧 Troubleshooting

## Import Errors on Older Systems

If you see `ModuleNotFoundError: No module named 'config'` when running wallet or scripts, this has been fixed in the latest version. Make sure you have the latest code:

```bash
git pull origin main
```

The issue was that older Python environments needed explicit PYTHONPATH setup. This is now handled automatically in all launcher scripts (`wallet_cli.py`, `start_explorer.py`, `run_testnet_node.py`).

**If you still see import errors after updating:**
- Verify you're using Python 3.8 or higher: `python3 --version`
- Ensure all files are up to date from GitHub
- Try running from the project root directory

## Validator Registration Not Appearing

**FIXED in latest version!** Validator registration transactions now use fresh timestamps on each broadcast attempt, preventing duplicate-hash rejection. 

If your node shows "Network nodes: 2" but you don't appear as a validator:
1. Update to the latest code: `git pull origin main`
2. Restart your node
3. The registration will be broadcast with a unique hash and included in the next block
4. Check the block explorer after 2-3 blocks to confirm registration

## Wallet Creation Issues on Older Systems

If `python3 wallet_cli.py` shows `ModuleNotFoundError: No module named 'config'`, set PYTHONPATH first:

```bash
export PYTHONPATH="$PWD/app:$PWD"
python3 wallet_cli.py
```

**Note:** The latest version should handle this automatically, but if you're on a very old Python environment (pre-3.8) or older macOS, this workaround ensures imports work correctly.

---

# 🧰 Need Help?
Open an issue on the GitHub repository.  
We're building a fair, equal-reward blockchain — welcome aboard!
