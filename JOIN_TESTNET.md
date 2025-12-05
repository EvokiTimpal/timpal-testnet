# Join the TIMPAL Testnet as a Validator

Welcome to the TIMPAL blockchain testnet!  
Running a validator node is fast, simple, and requires no technical experience.

This guide shows **exactly** how to start a validator node on macOS, Windows, or Linux.

---

# What You Will Do
1. Clone the TIMPAL Testnet repository  
2. Install Python requirements  
3. Create your validator wallet  
4. Set your wallet PIN  
5. Start your validator node  

That's it - your node will automatically join the P2P network and start earning testnet rewards.

---

# Requirements
- Python 3.8 or higher  
- Git (download from https://git-scm.com)
- Internet connection  
- Mac, Windows, or Linux  

---

# Network Architecture

TIMPAL is a **truly decentralized** blockchain. There is no central server or VPS.

**How nodes connect:**
- The first node to start becomes a seed node
- Other nodes connect to any existing node
- Nodes discover each other through P2P gossip
- Anyone can run a seed node

**To start a new testnet:**
1. First node starts with `--genesis` flag (creates the blockchain)
2. Other nodes connect with `--seed ws://FIRST_NODE_IP:PORT`

**To join an existing testnet:**
- Get the seed node address from timpal.org or the community
- Connect with `--seed ws://SEED_NODE_IP:PORT`

---

# Choose Your Operating System

- **[Windows Users - Click Here](#windows-installation-guide)**
- **[Mac/Linux Users - Click Here](#maclinux-installation-guide)**

---

# Windows Installation Guide

## Step 1: Install Prerequisites

**Install Python 3.8+ (if not already installed):**
1. Download Python from https://www.python.org/downloads/
2. **IMPORTANT:** During installation, check "Add Python to PATH"
3. Restart your computer after installation

**Install Git (if not already installed):**
1. Download Git from https://git-scm.com/download/win
2. Use default installation settings
3. Restart your computer after installation

**Verify installations (open Command Prompt and run):**
```cmd
python --version
git --version
```

You should see Python 3.8+ and Git version numbers.

## Step 2: Clone the Repository

**Open Command Prompt (cmd)** and run:

```cmd
cd %USERPROFILE%\Desktop
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
```

## Step 3: Install Dependencies

```cmd
pip install -r requirements.txt
```

If you see an error, try:
```cmd
python -m pip install -r requirements.txt
```

## Step 4: Create Your Validator Wallet

```cmd
python wallet_cli.py
```

**Follow the prompts:**
1. Choose "Create new wallet"
2. Choose seed phrase length: **12 words** (recommended) or **24 words** (extra security)
3. Enter a secure password (min 8 characters)
4. Enter a secure PIN (min 6 digits)
5. **WRITE DOWN your seed phrase on paper!** (CRITICAL - needed for recovery)
6. Verify your seed phrase by entering the first 3 words
7. Save as `wallet_v2.json`

## Step 5: Start Your Validator Node

**Set your wallet password (replace `your_password_here` with your actual password):**
```cmd
set TIMPAL_WALLET_PASSWORD=your_password_here
```

**Option A: Start as the FIRST node (creates new testnet):**
```cmd
python run_testnet_node.py --port 9000 --genesis
```

**Option B: Join an existing testnet:**
```cmd
python run_testnet_node.py --port 3000 --seed ws://SEED_NODE_IP:9000
```
(Replace `SEED_NODE_IP` with the IP address of an existing node)

**IMPORTANT FOR WINDOWS:**
- The `set` command only works in the current Command Prompt window
- You must run `set TIMPAL_WALLET_PASSWORD=your_password` **every time** you open a new window

### Alternative: PowerShell Users

If you prefer PowerShell:

```powershell
$env:TIMPAL_WALLET_PASSWORD="your_password_here"
python run_testnet_node.py --port 3000 --seed ws://SEED_NODE_IP:9000
```

## Step 6: (Optional) Start the Block Explorer

**In a NEW Command Prompt window:**

```cmd
cd %USERPROFILE%\Desktop\timpal-testnet
set EXPLORER_API_PORT=3001
python start_explorer.py --port 8080
```

Then open your browser to: http://localhost:8080

**To stop the node or explorer:**  
Press **Ctrl + C** in the Command Prompt window

---

# Mac/Linux Installation Guide

## Step 1: Clone the Repository

**First, navigate to your Desktop:**
```bash
cd ~/Desktop
```

**Then clone the repository:**
```bash
git clone https://github.com/EvokiTimpal/timpal-testnet.git
cd timpal-testnet
```

---

## Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

**For older systems (macOS, older Linux):**  
If you see `command not found: pip`, use `pip3` instead:
```bash
pip3 install -r requirements.txt
```

---

## Step 3: Create Your Validator Wallet

A wallet is required to operate a validator.  
Run this command and **write down** your:

- **Seed phrase** (12 or 24 words - CRITICAL for wallet recovery)
- **Wallet address** (starts with "tmpl")
- **Password** (8+ characters - used to decrypt wallet file)
- **PIN** (6+ digits - used to authorize transactions)

```bash
python3 wallet_cli.py
```

**For older systems (macOS, older Python):**  
If you see `ModuleNotFoundError: No module named 'config'`, set PYTHONPATH first:
```bash
export PYTHONPATH="$PWD/app:$PWD"
python3 wallet_cli.py
```

**Follow the prompts:**
1. Choose "Create new wallet"
2. Choose seed phrase length: **12 words** (recommended) or **24 words** (extra security)
3. Enter a secure password (min 8 characters)
4. Enter a secure PIN (min 6 digits)
5. **WRITE DOWN your seed phrase on paper** (never store digitally!)
6. Verify your seed phrase by entering the first 3 words
7. Save as `wallet_v2.json`

**Important:** The seed phrase is the ONLY way to recover your wallet if you lose the file. Store it safely!

---

## Step 4: Set Your Wallet Password

Your node uses the PASSWORD to decrypt the wallet file during startup.

**Remember the password you chose** when creating your wallet.  
You'll need to export it **every time** before starting the node.

---

## Step 5: Start Your Validator Node

**First, set your wallet password:**
```bash
export TIMPAL_WALLET_PASSWORD="your_secure_password"
```

**Option A: Start as the FIRST node (creates new testnet):**
```bash
python3 run_testnet_node.py --port 9000 --genesis
```

**Option B: Join an existing testnet:**
```bash
python3 run_testnet_node.py --port 3000 --seed ws://SEED_NODE_IP:9000
```
(Replace `SEED_NODE_IP` with the IP address of an existing node)

**Important:**
- You **MUST** export your PASSWORD before running the node (every time)
- Use the same password you chose when creating the wallet
- `--port 3000` - Your local P2P port (can be any available port)
- `--genesis` - Only use this flag for the FIRST node starting a new testnet

Your node will:

- Create local blockchain storage  
- Connect to the seed node (if specified)
- Sync the blockchain from the network
- Register automatically as a validator  
- Start validating and earning testnet rewards  

You will see logs similar to:

```
Starting node...
Node is running!
P2P: Attempting to connect to seed node...
P2P: Connected successfully!
P2P: Completed handshake with peer...
Registered validator tmplxxxxxxxx...
Creating or syncing blockchain...
```

---

# You're now part of the TIMPAL Testnet!

Your validator begins participating in the network immediately.

To stop the node:  
Press **Ctrl + C**

To restart the node:  
Run the same command:

```bash
export TIMPAL_WALLET_PASSWORD="your_password"
python3 run_testnet_node.py --port 3000 --seed ws://SEED_NODE_IP:9000
```

---

# Optional Commands

## Start the Block Explorer

View the blockchain in your browser with the TIMPAL Block Explorer.

**IMPORTANT:** The explorer needs to connect to your node's HTTP API port!

### Understanding Ports

Your blockchain node creates **TWO ports**:

| Component | Port | Purpose |
|-----------|------|---------|
| **P2P Network** | Your `--port` value (e.g., 3000) | Node-to-node blockchain communication |
| **HTTP API** | Your port + 1 (e.g., 3001) | Explorer connects here for data |

### Starting the Explorer

**If you started your node with `--port 3000`:**

```bash
export EXPLORER_API_PORT=3001
python3 start_explorer.py --port 8080
```

**If you started your node with `--port 9000`:**

```bash
export EXPLORER_API_PORT=9001
python3 start_explorer.py --port 8080
```

**Quick Reference:**

| Your Node Port | HTTP API Port | Explorer Command |
|----------------|---------------|------------------|
| 3000 | 3001 | `EXPLORER_API_PORT=3001 python3 start_explorer.py --port 8080` |
| 8001 | 8002 | `EXPLORER_API_PORT=8002 python3 start_explorer.py --port 8080` |
| 9000 | 9001 | `EXPLORER_API_PORT=9001 python3 start_explorer.py --port 8080` |

Then open your browser and visit:
```
http://localhost:8080
```

**What you can see:**
- Chain statistics (total blocks, validators, transactions)
- Search blocks by height or hash
- View transactions and transfers
- Browse all validators and their balances
- Network activity charts
- Real-time blockchain updates

Press **Ctrl + C** to stop the explorer.

---

## Check your balance
```bash
python3 wallet_cli.py
# Choose "Check balance" and enter your password
```

---

# Important Notes
- **One validator per device** (Sybil-resistant design)  
- Your wallet PASSWORD must always be set before running the node
- **Do NOT delete your 12-word seed phrase** - it's the ONLY way to recover your wallet
- **Do NOT share your password, PIN, or seed phrase** - anyone with these can steal your funds
- The PIN is stored inside your wallet and is only used when making transactions

---

# Troubleshooting

## Import Errors on Older Systems

If you see `ModuleNotFoundError: No module named 'config'`:

```bash
git pull origin main
```

**If you still see import errors after updating:**
- Verify you're using Python 3.8 or higher: `python3 --version`
- Ensure all files are up to date from GitHub
- Try running from the project root directory

## Validator Registration Not Appearing

If your node shows connected peers but you don't appear as a validator:
1. Update to the latest code: `git pull origin main`
2. Restart your node
3. The registration will be broadcast with a unique hash and included in the next block
4. Check the block explorer after 2-3 blocks to confirm registration

## Port Already in Use

If you see `Address already in use` error:
1. Try a different port: `--port 3001` or `--port 8001`
2. Or close the program using that port

## Explorer Cannot Connect

Make sure:
1. Your node is running in another window
2. You set `EXPLORER_API_PORT` correctly (your node port + 1)
3. Example: If node uses `--port 3000`, set `EXPLORER_API_PORT=3001`

---

# Windows-Specific Troubleshooting

## "Python is not recognized"

If you see `'python' is not recognized as an internal or external command`:
1. Reinstall Python from https://www.python.org/downloads/
2. **CRITICAL:** Check "Add Python to PATH" during installation
3. Restart your computer
4. Try again

## "Git is not recognized"

If you see `'git' is not recognized as an internal or external command`:
1. Install Git from https://git-scm.com/download/win
2. Restart your computer
3. Try again

## Password Not Working

**Command Prompt (cmd):**
```cmd
set TIMPAL_WALLET_PASSWORD=your_password
```

**PowerShell:**
```powershell
$env:TIMPAL_WALLET_PASSWORD="your_password"
```

Remember:
- NO spaces around the `=` sign in cmd
- Use quotes `"..."` in PowerShell
- Must be set in EVERY new window

## Firewall Issues

Windows Firewall may block the node. When you see a popup:
1. Click "Allow access"
2. Select both "Private networks" and "Public networks"
3. Click "Allow access"

---

# Need Help?
Open an issue on the GitHub repository.  
We're building a fair, equal-reward blockchain - welcome aboard!
