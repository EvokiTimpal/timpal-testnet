# Running a TIMPAL Node - 24/7 Operation Guide

**Last Updated:** November 11, 2025  
> **TESTNET NOTE**: This guide references **Block 550,000** for the testnet (~19 days). Mainnet uses Block 5,000,000 (~6 months).

---

## 🚨 CRITICAL: Deposit Requirement Transition (Block 550,000 - Testnet)

### **Timeline Overview:**

**Phase 1: Free Validation** (Blocks 0 - 524,999 / ~18 days)
- ✅ **NO deposit required** to validate
- ✅ Free participation for all
- ✅ Earn rewards immediately

**Phase 2: Advance Deposit Window** (Blocks 525,000 - 549,999 / ~24 hours)
- ⚠️ **Deposit requirement coming soon**
- 📅 Optional: Schedule your deposit to auto-lock at block 550K
- 🔧 Optional: Disable auto-lock if you want manual control
- ⏰ Node will display countdown warnings

**Phase 3: Automatic Transition** (Block 550,000 exactly)
- 🔒 **100 TMPL deposit becomes required** for ALL validators
- ✅ **DEFAULT**: Your node will automatically lock 100 TMPL if you have sufficient balance
- ✅ Validators with ≥100 TMPL continue validating (zero downtime)
- ⚠️ Validators with <100 TMPL become inactive (can rejoin anytime by depositing)

**Phase 4: Post-Transition** (Block 550,001+)
- 💰 **100 TMPL deposit required** for all validators (new and existing)
- 🔄 Inactive validators can rejoin by depositing 100 TMPL
- ⚖️ All validators receive equal rewards (NOT Proof-of-Stake!)

---

### **What You Need to Know:**

#### **If You Have ≥100 TMPL by Block 550,000:**
✅ **Nothing required!** Your deposit will auto-lock and you'll keep validating.

#### **If You Have <100 TMPL:**
⚠️ You'll be marked inactive at block 550,000  
✅ You can rejoin later when you acquire 100 TMPL  
✅ Your existing balance is safe (not slashed or burned)

#### **Advanced Options (Optional):**

**Option A: Schedule Deposit in Advance**  
During blocks 525K-550K, you can pre-schedule your deposit:
```python
# Available via validator_cli.py during advance window
python validator_cli.py  # Option 4: Schedule deposit
```

**Option B: Disable Auto-Lock (Manual Control)**  
If you don't want automatic locking:
```python
# Available via validator_cli.py
python validator_cli.py  # Option 3: Disable auto-lock
```

---

### **Important Clarifications:**

❌ **This is NOT Proof-of-Stake**
- You cannot stake more than 100 TMPL for extra power
- All validators receive equal rewards regardless of deposit amount
- The 100 TMPL is an anti-Sybil barrier, not proportional staking

✅ **Network Never Stops**
- At least validators who saved earnings will continue automatically
- No coordination required - it's automatic for most validators
- 24-hour advance notice ensures everyone has time to prepare

✅ **Absolute Fairness Maintained**
- Same rules for everyone, regardless of join time
- Deposits returned in full upon voluntary exit (no slashing)
- Equal rewards for all active validators

---

## 💤 What Happens When Your Laptop Sleeps?

### **Short Answer:**
❌ **Your node STOPS when your laptop sleeps or shuts down.**

### **Why This Happens:**
- When your laptop sleeps, all running programs pause
- The TIMPAL node software stops processing blocks
- Network connections are dropped
- Your validator stops participating in consensus

---

## 🌐 Impact on the TIMPAL Network

### **Good News - Network Continues Running:**
✅ Other validators keep producing blocks  
✅ Blockchain doesn't stop  
✅ Transactions still process  
✅ Network remains secure  

### **What You Miss:**
❌ No block rewards while offline  
❌ Not participating in consensus  
❌ Missed validation opportunities  

### **When You Come Back Online:**
✅ Node automatically syncs with the network  
✅ Downloads any missed blocks  
✅ Rejoins the validator rotation  
✅ Starts earning rewards again  

---

## 🎯 Running Modes: Choose What's Right for You

### **Option 1: Casual/Testing Mode (Laptop)**

**Best for:**
- Learning how TIMPAL works
- Testing transactions
- Development and experimentation
- Non-critical operation

**Setup:**
```bash
python app/explorer.py
```

**Characteristics:**
- ⚠️ Node stops when laptop sleeps
- ⚠️ Rewards only earned while active
- ✅ No infrastructure costs
- ✅ Easy to start/stop
- ✅ Perfect for testing

**Expected uptime:** 10-30% (only when laptop is on)

---

### **Option 2: Serious Validator Mode (Cloud Server)**

**Best for:**
- Earning consistent block rewards
- Supporting the network 24/7
- Professional validator operation
- Maximum uptime

**Setup Options:**

#### **A. Cloud VPS (Recommended)**

**Popular Providers:**
- DigitalOcean ($6-12/month)
- AWS EC2 ($5-10/month)
- Google Cloud ($5-10/month)
- Linode ($5-10/month)
- Vultr ($6-12/month)

**Setup Steps:**
```bash
# 1. Create Ubuntu 22.04 VPS (1GB RAM minimum)
# 2. SSH into server
ssh root@your-server-ip

# 3. Install dependencies
apt update && apt upgrade -y
apt install -y python3 python3-pip git

# 4. Clone TIMPAL
git clone https://github.com/[username]/timpal-genesis.git
cd timpal-genesis

# 5. Install requirements
pip3 install -r requirements.txt

# 6. Create wallet
python3 app/wallet.py

# 7. Run node (with screen/tmux for persistence)
screen -S timpal
python3 app/explorer.py

# Press Ctrl+A, then D to detach
# Node keeps running even after you disconnect!
```

**Characteristics:**
- ✅ Runs 24/7 (99.9% uptime)
- ✅ Consistent block rewards
- ✅ Supports the network
- 💰 $5-12/month cost
- ✅ Remote management

**Expected uptime:** 99.5%+ (professional operation)

---

#### **B. Home Server (Raspberry Pi / Old Laptop)**

**Best for:**
- No monthly cloud costs
- Learning server management
- Home lab enthusiasts

**Setup:**
```bash
# On Raspberry Pi 4 (4GB+ RAM) or old laptop with Ubuntu:

# 1. Install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip git

# 2. Clone and install TIMPAL
git clone https://github.com/[username]/timpal-genesis.git
cd timpal-genesis
pip3 install -r requirements.txt

# 3. Create wallet
python3 app/wallet.py

# 4. Set up autostart (systemd service)
sudo nano /etc/systemd/system/timpal.service

# Paste this configuration:
[Unit]
Description=TIMPAL Blockchain Node
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/timpal-genesis
ExecStart=/usr/bin/python3 app/explorer.py
Restart=always

[Install]
WantedBy=multi-user.target

# 5. Enable and start service
sudo systemctl enable timpal
sudo systemctl start timpal

# 6. Check status
sudo systemctl status timpal
```

**Characteristics:**
- ✅ No monthly hosting fees
- ✅ Full control over hardware
- ⚠️ Your electricity cost (~$2-5/month)
- ⚠️ Affected by internet/power outages
- ⚠️ Need static IP or dynamic DNS

**Expected uptime:** 95-99% (depends on home internet/power)

---

### **Option 3: Hybrid Mode (Best of Both)**

**Strategy:**
- Run node on laptop for development/testing
- Deploy second node on cloud server for rewards
- Use laptop node to monitor/interact with blockchain
- Use cloud node for consistent validation

**Setup:**
```bash
# Laptop: Development node
python app/explorer.py  # Runs when you're working

# Cloud: Production validator (separate wallet)
# Runs 24/7 on VPS as described in Option 2
```

**Characteristics:**
- ✅ Flexibility for development
- ✅ Consistent rewards from cloud node
- ✅ Best of both worlds
- 💰 ~$6-12/month for cloud portion

---

## 📊 Reward Impact Comparison

| Mode | Uptime | Monthly Rewards* | Cost | Net Value |
|------|--------|-----------------|------|-----------|
| **Laptop (8hr/day)** | 33% | ~33% of max | $0 | 33% rewards |
| **Laptop (24/7 always-on)** | 95% | ~95% of max | $3-8/mo power | 95% rewards - power |
| **Cloud VPS** | 99.5% | ~99.5% of max | $6-12/mo | 99.5% rewards - hosting |
| **Home Server** | 97% | ~97% of max | $2-5/mo power | 97% rewards - power |

*Actual rewards depend on number of active validators and network activity

---

## 🔧 Keeping Your Node Running: Technical Guide

### **Using Screen (Simple)**

```bash
# Start screen session
screen -S timpal

# Run node
python app/explorer.py

# Detach: Press Ctrl+A, then D
# Node keeps running!

# Reattach later
screen -r timpal

# Kill session
screen -X -S timpal quit
```

---

### **Using Systemd (Professional)**

**Create service file:**
```bash
sudo nano /etc/systemd/system/timpal.service
```

**Service configuration:**
```ini
[Unit]
Description=TIMPAL Blockchain Node
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/timpal-genesis
ExecStart=/usr/bin/python3 app/explorer.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable timpal
sudo systemctl start timpal
sudo systemctl status timpal
```

**Management commands:**
```bash
# Check status
sudo systemctl status timpal

# View logs
sudo journalctl -u timpal -f

# Restart
sudo systemctl restart timpal

# Stop
sudo systemctl stop timpal
```

---

### **Using Docker (Advanced)**

**Create Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY blockchain_data/ ./blockchain_data/

EXPOSE 8080
CMD ["python", "app/explorer.py"]
```

**Build and run:**
```bash
# Build image
docker build -t timpal-node .

# Run container
docker run -d \
  --name timpal \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/blockchain_data:/app/blockchain_data \
  timpal-node

# View logs
docker logs -f timpal

# Stop
docker stop timpal
```

---

## 🎯 Best Practices for Validators

### **Security**

✅ **Backup your wallet:**
```bash
# Copy wallet files to secure location
cp wallet.json wallet_backup_$(date +%Y%m%d).json
# Store recovery phrase offline (paper backup)
```

✅ **Use SSH keys (not passwords):**
```bash
# On your laptop
ssh-keygen -t ed25519
ssh-copy-id user@your-server-ip
```

✅ **Enable firewall:**
```bash
# Allow only necessary ports
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 8080/tcp # Block Explorer
sudo ufw allow 8765/tcp # P2P networking
sudo ufw enable
```

---

### **Monitoring**

✅ **Check node health:**
```bash
# Block count
curl http://localhost:8080/stats | jq '.block_count'

# Your validator status
curl http://localhost:8080/validators | jq
```

✅ **Set up monitoring (optional):**
- UptimeRobot (free external monitoring)
- Prometheus + Grafana (advanced metrics)
- Simple cron health check

---

### **Maintenance**

✅ **Regular updates:**
```bash
# Backup first!
cp -r blockchain_data blockchain_data_backup

# Pull updates (if any)
git pull origin main

# Restart node
sudo systemctl restart timpal
```

✅ **Disk space monitoring:**
```bash
# Check blockchain size
du -sh blockchain_data/

# Check available space
df -h
```

---

## ⚠️ Common Issues & Solutions

### **Node Stopped After Laptop Closed**

**Problem:** Node not running after closing laptop  
**Solution:** This is normal! Either:
- Keep laptop awake (power settings)
- Use cloud server for 24/7 operation
- Accept intermittent operation

---

### **Missed Many Blocks**

**Problem:** Node was offline and missed blocks  
**Solution:** Node will auto-sync when restarted
```bash
# Just restart - syncing is automatic
python app/explorer.py
```

---

### **Lost Rewards While Offline**

**Problem:** No rewards earned during downtime  
**Solution:** This is expected behavior
- Rewards only go to active validators
- Consider cloud server for consistent rewards

---

### **How to Prevent Laptop Sleep**

**Linux:**
```bash
# Disable sleep while node running
sudo systemctl mask sleep.target suspend.target
```

**macOS:**
```bash
# Keep awake while plugged in
caffeinate -s
```

**Windows:**
```powershell
# Power settings -> Never sleep when plugged in
powercfg /change standby-timeout-ac 0
```

---

## 📋 Quick Decision Guide

**Choose laptop if:**
- ✅ Just learning/testing TIMPAL
- ✅ Don't care about consistent rewards
- ✅ Want zero infrastructure costs
- ✅ Okay with intermittent operation

**Choose cloud server if:**
- ✅ Want to earn maximum rewards
- ✅ Need 24/7 uptime
- ✅ Comfortable with $5-12/month cost
- ✅ Want to support the network seriously

**Choose home server if:**
- ✅ Have spare hardware (Raspberry Pi, old laptop)
- ✅ Want to avoid monthly cloud costs
- ✅ Have reliable home internet
- ✅ Enjoy home lab projects

---

## 🚀 Getting Started Checklist

### **For Cloud Server Validators:**

- [ ] Choose cloud provider (DigitalOcean recommended)
- [ ] Create Ubuntu 22.04 VPS (1GB+ RAM)
- [ ] Install Python and dependencies
- [ ] Clone TIMPAL repository
- [ ] Create wallet and backup recovery phrase
- [ ] Start node with screen/systemd
- [ ] Verify node is syncing (`curl localhost:8080/stats`)
- [ ] Set up monitoring (optional)
- [ ] Configure firewall
- [ ] Document your server details

### **For Home Server Validators:**

- [ ] Prepare hardware (Raspberry Pi 4+ or laptop)
- [ ] Install Ubuntu/Raspbian
- [ ] Install Python and dependencies
- [ ] Clone TIMPAL repository
- [ ] Create wallet and backup recovery phrase
- [ ] Set up systemd service for autostart
- [ ] Configure router port forwarding (if needed)
- [ ] Set up dynamic DNS (if needed)
- [ ] Test automatic restart after power loss
- [ ] Monitor electricity usage

---

## 💡 Tips for Maximum Rewards

1. **Uptime is everything** - 99% uptime = 99% of possible rewards
2. **Monitor your node** - Check daily that it's still running
3. **Backup your wallet** - Lost wallet = lost rewards forever
4. **Keep it simple** - Don't over-complicate your setup
5. **Start small** - Test on laptop, then upgrade to cloud when ready

---

## 📚 Additional Resources

**Node Management:**
- Block Explorer: `http://localhost:8080`
- Check stats: `curl localhost:8080/stats | jq`
- View logs: `tail -f /var/log/syslog` (systemd) or `screen -r timpal`

**Community:**
- GitHub repository (issues, updates)
- Documentation (`README.md`, `PROJECT_DOCUMENTATION.md`)
- Test suite (`cd app && pytest tests/ -v`)

---

## 🎯 Summary

**The Simple Truth:**
- 💤 Laptop sleep = Node stops = No rewards
- 🌐 Cloud server = 24/7 operation = Maximum rewards
- 🏠 Home server = Good uptime = Good rewards
- 📱 Hybrid = Development + Production = Best flexibility

**Bottom Line:**
For serious validators earning consistent rewards, run your node on a cloud server or always-on home server. For casual use and testing, running on your laptop is perfectly fine!

---

**TIMPAL Genesis - Run Your Node Your Way** 🚀

**Last Updated:** October 29, 2025  
**Version:** 1.0.0
