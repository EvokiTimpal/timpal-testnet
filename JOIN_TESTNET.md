# Join TIMPAL Testnet

This guide walks you through joining the TIMPAL testnet as a validator.

## ⚠️ CRITICAL: Port 9000 is Reserved for Bootstrap Node Only

**Only ONE node in the entire testnet uses port 9000** - the genesis/bootstrap node run by the testnet operator.

**All other validators MUST:**
- Use a different port (8001, 8002, 8010, 9005, etc.)
- Include the `--seed` flag pointing to the bootstrap node
- **If you forget `--seed`, you will create a separate private chain!**

---

## Prerequisites

1. **Python 3.10+** installed
2. **Git** installed
3. **Stable internet connection**

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/EvokiTimpal/timpal-testnet
cd timpal-testnet
```

---

## Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

Or if using `pip3`:

```bash
pip3 install -r requirements.txt
```

---

## Step 3: Create Your Wallet

```bash
python3 wallet_cli.py
```

**CRITICAL: Save your recovery phrase!** This is the ONLY way to recover your wallet.

The wallet will be saved to `wallet.json` (encrypted with your PIN).

---

## Step 4: Official Bootstrap Node Information

The TIMPAL testnet uses a dedicated VPS bootstrap node:
- **Bootstrap IP address:** `143.110.129.211`
- **Bootstrap port:** `9000`
- **Full address:** `ws://143.110.129.211:9000`

---

## Step 5: Start Your Validator Node

**Every validator must connect to the official bootstrap node:**

```bash
cd ..  # Go back to project root
python3 run_testnet_node.py --port YOUR_PORT --seed ws://143.110.129.211:9000
```

**Replace:**
- `YOUR_PORT` = Any available port **except 9000** (examples: 8001, 8002, 8010, 9005)

### Examples

```bash
# Validator using port 8001
python3 run_testnet_node.py --port 8001 --seed ws://143.110.129.211:9000

# Validator using port 8012
python3 run_testnet_node.py --port 8012 --seed ws://143.110.129.211:9000

# Validator using port 9005
python3 run_testnet_node.py --port 9005 --seed ws://143.110.129.211:9000
```

### ⚠️ Common Mistakes to Avoid

❌ **WRONG** - Using port 9000:
```bash
python3 run_testnet_node.py --port 9000 --seed ws://143.110.129.211:9000
```
Port 9000 is reserved for the bootstrap node only!

❌ **WRONG** - Forgetting `--seed`:
```bash
python3 run_testnet_node.py --port 8001
```
Without `--seed`, you will create a **private chain** instead of joining the testnet!

✅ **CORRECT** - Different port + seed flag:
```bash
python3 run_testnet_node.py --port 8001 --seed ws://143.110.129.211:9000
```

---

## Step 6: Verify Your Node is Running

You should see log messages like:

```
[NETWORK MODE] This node will connect to the existing testnet.
✅ Validator registered: tmpl568d55ba3f9fb14f...
🔄 Connecting to seed node: ws://143.110.129.211:9000
✅ Connected to peer: ws://143.110.129.211:9000
📊 Node Status:
   Peers: 1+
   Current height: 1234
```

**Key indicators you joined correctly:**
- `[NETWORK MODE]` appears (not `[BOOTSTRAP MODE]`)
- Peers count is 1 or higher
- Current height matches the network height (not starting from 0)

---

## Step 7: Monitor Your Rewards

Your validator will automatically:
- Sync with the existing blockchain
- Participate in block production
- Earn equal rewards with all other validators

Check your balance:

```bash
python3 wallet_cli.py
# Select "View Balance"
```

---

## 🧭 Running the TIMPAL Block Explorer

The TIMPAL Block Explorer provides a web interface to view blockchain data, transactions, validators, and network statistics.

**Default Port (5000):**
```bash
python3 app/explorer.py
```

**Custom Port:**
```bash
# Use any available port
python3 app/explorer.py --port 8080
python3 app/explorer.py --port 6000
python3 app/explorer.py --port 3000
```

**The explorer will start and show:**
```
Starting TIMPAL Block Explorer on port <port>...
Explorer URL: http://0.0.0.0:<port>
```

**Access in your browser:**
- `http://localhost:<port>`
- `http://0.0.0.0:<port>`
- `http://YOUR_MACHINE_IP:<port>`

**Security Note:**
- CORS settings currently allow API requests only from `http://localhost:5000`
- This does NOT prevent you from viewing the explorer UI on any port
- The web interface works on all ports; only API cross-origin requests are restricted

---

## Troubleshooting

### Port 5000 is Busy

**Cause:** macOS ControlCenter (AirPlay Receiver) commonly uses port 5000.

**Fix:** Start the explorer on a different port:
```bash
python3 app/explorer.py --port 6000
```

### "HEALTH CHECK: Only 0 peers connected"

**Cause:** Your node cannot connect to the bootstrap node.

**Fix:**
1. Verify the bootstrap IP and port are correct
2. Check if the bootstrap node is running
3. Ensure your firewall allows outbound connections
4. Try using a different `YOUR_PORT` value

### "Block 0 created" (Genesis block)

**Cause:** You forgot the `--seed` flag or it's incorrect.

**Fix:**
1. Stop your node (Ctrl+C)
2. Delete the testnet data: `rm -rf testnet_data_*`
3. Restart with the correct `--seed` flag:
   ```bash
   python3 run_testnet_node.py --port 8001 --seed ws://143.110.129.211:9000
   ```

### "ERROR: Port 9000 is reserved for the bootstrap node only"

**Cause:** You tried to use port 9000 with the `--seed` flag.

**Fix:** Use a different port (8001, 8002, 8010, etc.)

---

## Network Modes Explained

### Bootstrap Mode (Genesis Operator Only)
```bash
python3 run_testnet_node.py --port 9000
```
- No `--seed` flag
- Creates genesis block
- Produces blocks with 0 peers
- **Only the testnet operator runs this!**

### Network Mode (All Validators)
```bash
python3 run_testnet_node.py --port 8001 --seed ws://143.110.129.211:9000
```
- Includes `--seed` flag
- Connects to existing testnet
- Syncs blockchain from bootstrap node
- **This is what you should run!**

---

## Port Reference

| Port | Usage |
|------|-------|
| `9000` | **Bootstrap node only** (testnet operator) |
| `8001, 8002, 8010, 9005, etc.` | **Validators** (everyone else) |

---

## Need Help?

Contact the testnet operator or open an issue on GitHub.

**Remember:**
- Port 9000 = Bootstrap node only
- All validators = Different port + `--seed` flag
- No `--seed` = Private chain (wrong!)
