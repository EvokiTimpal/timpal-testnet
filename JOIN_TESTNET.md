# Join the TIMPAL Testnet as a Validator

Welcome to the TIMPAL blockchain testnet!  
Running a validator node is fast, simple, and requires no technical experience.

This guide shows **exactly** how to start a validator node on macOS, Windows, or Linux.

---

# What You Will Do
1. Clone the TIMPAL Testnet repository  
2. Install Python requirements  
3. Create your validator wallet  
4. Set your wallet password  
5. Start your validator node  

That's it - your node will automatically join the P2P network, register as a validator, and start earning testnet rewards.

---

# Requirements
- Python 3.8 or higher  
- Git (download from https://git-scm.com)
- Internet connection  
- Mac, Windows, or Linux  

---

# ⚠️ macOS Users: Important iCloud Warning

**If you use iCloud Desktop & Documents sync on macOS, read this carefully!**

macOS can transparently relocate your Desktop and Documents folders into iCloud Drive. If you clone the TIMPAL repository to your Desktop, the blockchain data may be synced to iCloud, causing:

- **Chain state duplication** across devices
- **Old genesis data** being restored from iCloud backups
- **Permanent consensus forks** that look like network bugs
- **Sync failures** with "FORK DETECTED" errors

**How to avoid this issue:**

1. **Option A (Recommended):** The node now stores data in `~/.timpal/` by default, which is safe from iCloud sync. No action needed if using the latest version.

2. **Option B:** Disable "Desktop & Documents" sync in System Settings → Apple ID → iCloud → iCloud Drive → Options.

3. **Option C:** Clone the repository to a non-iCloud location like `~/Projects/` instead of Desktop.

4. **Option D:** Set a custom data path:
   ```bash
   export TIMPAL_DATA_PATH=~/.timpal
   ```

**Note:** If you previously ran a node from Desktop with iCloud sync enabled, you may need to:
1. Delete the old `testnet_data_node_*` folders from Desktop
2. Check iCloud Drive for any synced copies and delete them
3. Start fresh with the new default location (`~/.timpal/`)

---

# Network Architecture

TIMPAL is a **truly decentralized** blockchain.

**How nodes connect:**
- Validators connect to the seed node
- Nodes discover each other through P2P gossip
- All online validators share block rewards equally
- Validator registration happens automatically on startup

**Public Seed Node URL:**
```
ws://5.78.43.127:9000
```

---

# Quick Reference: All Commands

| Action | Command |
|--------|---------|
| **Create wallet** | `python3 wallet_cli.py` |
| **Join testnet** | `python3 run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000` |
| **Start genesis node** | `python3 run_testnet_node.py --port 9000 --genesis` |
| **Testing (multi-node)** | `python3 run_testnet_node.py --port 9000 --skip-device-check --seed ws://5.78.43.127:9000` |
| **Check sync status** | `curl http://localhost:9001/api/blockchain/info` |
| **Start explorer** | `EXPLORER_API_PORT=9001 python3 start_explorer.py --port 8080` |
| **Check balance** | `python3 wallet_cli.py` (choose "Check balance") |

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

**Start your validator node (join the testnet):**
```cmd
python run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000
```

**IMPORTANT FOR WINDOWS:**
- The `set` command only works in the current Command Prompt window
- You must run `set TIMPAL_WALLET_PASSWORD=your_password` **every time** you open a new window

### Alternative: PowerShell Users

If you prefer PowerShell:

```powershell
$env:TIMPAL_WALLET_PASSWORD="your_password_here"
python run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000
```

## Step 6: (Optional) Start the Block Explorer

**In a NEW Command Prompt window:**

```cmd
cd %USERPROFILE%\Desktop\timpal-testnet
set EXPLORER_API_PORT=9001
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

**Start your validator node (join the testnet):**
```bash
python3 run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000
```

**Important:**
- You **MUST** export your PASSWORD before running the node (every time)
- Use the same password you chose when creating the wallet
- `--port 9000` - Your local P2P port (default, can be any available port)
- `--seed` - Points to the public seed node for syncing

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
python3 run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000
```

---

# Advanced Commands

## Starting a Genesis Node

The genesis node creates the initial blockchain. **Only use this if you're starting a brand new testnet** - not for joining an existing network.

```bash
export TIMPAL_WALLET_PASSWORD="your_password"
python3 run_testnet_node.py --port 9000 --genesis
```

**Important:**
- Only ONE genesis node should exist per network
- Do NOT use `--genesis` when joining an existing testnet
- Genesis nodes do NOT use `--seed` (they ARE the seed)

---

## Testing with Multiple Nodes (--skip-device-check)

TIMPAL enforces **one validator per device** to prevent Sybil attacks. For testing purposes, you can run multiple nodes on the same machine using the `--skip-device-check` flag.

**Start multiple test nodes on different ports:**
```bash
# Terminal 1 - First node
export TIMPAL_WALLET_PASSWORD="your_password"
python3 run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000 --skip-device-check

# Terminal 2 - Second node (different port)
export TIMPAL_WALLET_PASSWORD="your_password"
python3 run_testnet_node.py --port 9002 --seed ws://5.78.43.127:9000 --skip-device-check

# Terminal 3 - Third node (different port)
export TIMPAL_WALLET_PASSWORD="your_password"
python3 run_testnet_node.py --port 9004 --seed ws://5.78.43.127:9000 --skip-device-check
```

**Note:** Each node uses TWO ports:
- P2P port: The `--port` value (e.g., 9000)
- HTTP API port: P2P port + 1 (e.g., 9001)

So if you run nodes on ports 9000, 9002, and 9004, their HTTP APIs will be on 9001, 9003, and 9005 respectively.

---

## Checking Sync Status

Check if your node is synced with the network:

```bash
curl http://localhost:9001/api/blockchain/info
```

This returns JSON with:
- `height` - Current block height
- `latest_block_hash` - Hash of the latest block
- `validator_count` - Number of registered validators

**Example response:**
```json
{
  "height": 1234,
  "latest_block_hash": "abc123...",
  "validator_count": 5,
  "chain_id": "timpal-testnet"
}
```

**Check health:**
```bash
curl http://localhost:9001/api/health
```

---

## Connecting from Another Machine

To connect to the TIMPAL testnet from any machine (local network or internet):

**Network Binding:** The P2P server automatically binds to `0.0.0.0` (all network interfaces), so no `--host` flag is needed. Both P2P (WebSocket) and HTTP API are externally reachable by default if your firewall/NAT allows it.

**1. Use the public seed node:**
```bash
python3 run_testnet_node.py --port 9000 --seed ws://5.78.43.127:9000
```

**2. Requirements for external validators to connect to YOUR node:**

For other validators on the internet to connect to your node, you need ALL of the following:

| Requirement | Description |
|-------------|-------------|
| **Port 9000 TCP open** | Your firewall must allow incoming connections on port 9000 |
| **Port forwarding** | If behind NAT/router, forward port 9000 to your machine |
| **Public IP or DDNS** | Your hostname/IP must resolve to your public IP address |

**3. Port forwarding (for home networks):**

If you're running a seed node behind a home router, configure port forwarding:

| Router Setting | Value |
|----------------|-------|
| External Port | 9000 (or your chosen port) |
| Internal Port | 9000 (same as external) |
| Protocol | TCP |
| Internal IP | Your computer's local IP (e.g., 192.168.1.100) |

**How to find your local IP:**
```bash
# Linux/Mac
ip addr show | grep "inet " | grep -v 127.0.0.1

# Windows (Command Prompt)
ipconfig | findstr "IPv4"
```

**4. Firewall configuration:**

Make sure your firewall allows incoming connections on your P2P port:

**Linux (ufw):**
```bash
sudo ufw allow 9000/tcp
sudo ufw reload
```

**Linux (iptables):**
```bash
sudo iptables -A INPUT -p tcp --dport 9000 -j ACCEPT
```

**Windows:**
- Windows Firewall will prompt you when you start the node
- Click "Allow access" for both private and public networks
- Or manually add a rule: Windows Defender Firewall → Advanced Settings → Inbound Rules → New Rule → Port → TCP 9000

**5. Dynamic DNS (DDNS) setup:**

If your ISP assigns a dynamic IP, use a DDNS service like No-IP:
1. Create a free account at https://www.noip.com
2. Create a hostname (e.g., `yournode.ddns.net`)
3. Install the No-IP DUC client to keep the hostname updated
4. Use your DDNS hostname in the `--seed` URL

**6. Verify connectivity:**

After starting your node, check that it's connected to peers:
```bash
curl http://localhost:9001/api/health
```

Look for `"peers": X` where X > 0 indicates successful connections.

**Test external connectivity:**
```bash
# From another machine, test if the port is reachable
nc -zv your-public-ip-or-hostname 9000

# Or use an online port checker like https://www.yougetsignal.com/tools/open-ports/
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
| **P2P Network** | Your `--port` value (e.g., 9000) | Node-to-node blockchain communication |
| **HTTP API** | Your port + 1 (e.g., 9001) | Explorer connects here for data |

### Starting the Explorer

**If you started your node with `--port 9000` (default):**

```bash
export EXPLORER_API_PORT=9001
python3 start_explorer.py --port 8080
```

**Quick Reference:**

| Your Node Port | HTTP API Port | Explorer Command |
|----------------|---------------|------------------|
| 9000 | 9001 | `EXPLORER_API_PORT=9001 python3 start_explorer.py --port 8080` |
| 9002 | 9003 | `EXPLORER_API_PORT=9003 python3 start_explorer.py --port 8080` |
| 8001 | 8002 | `EXPLORER_API_PORT=8002 python3 start_explorer.py --port 8080` |

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
- **One validator per device** (Sybil-resistant design) - use `--skip-device-check` only for testing
- Your wallet PASSWORD must always be set before running the node
- **Do NOT delete your 12-word seed phrase** - it's the ONLY way to recover your wallet
- **Do NOT share your password, PIN, or seed phrase** - anyone with these can steal your funds
- The PIN is stored inside your wallet and is only used when making transactions
- Validator registration is **automatic** - no manual registration required

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
3. Example: If node uses `--port 9000`, set `EXPLORER_API_PORT=9001`

## Node Not Syncing

If your node is not syncing blocks:
1. Check your internet connection
2. Verify the seed node URL: `ws://5.78.43.127:9000`
3. Check sync status: `curl http://localhost:9001/api/blockchain/info`
4. Try restarting the node

## Connection Timeout to Seed Node

If you see this error when trying to connect to a seed node:
```
P2P: Attempting to connect to ws://5.78.43.127:9000
Connection timed out
Possible causes: Node offline, firewall blocking, wrong IP/port
```

**Troubleshooting steps:**

1. **Check if the seed node is online:**
   - Contact the seed node operator to confirm it's running
   - Try pinging the seed node: `ping 5.78.43.127`

2. **Test port connectivity:**
   ```bash
   nc -zv 5.78.43.127 9000
   # or
   telnet 5.78.43.127 9000
   ```
   If this times out, the port is blocked or the node is offline.

3. **Common causes:**
   - **Seed node firewall**: Port 9000 TCP not open on the seed node
   - **Your firewall**: Some corporate/school networks block outgoing WebSocket connections
   - **Seed node offline**: The genesis node process may have crashed or been stopped

4. **If you're running the seed node:**
   - Verify the node is running: `ps aux | grep run_testnet_node`
   - Check firewall: `sudo ufw status` (Linux) or Windows Firewall settings

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
