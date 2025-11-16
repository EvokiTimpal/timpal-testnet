<div align="center">
  <img src="app/static/timpal-logo.png" alt="TIMPAL Logo" width="120" />
</div>

# TIMPAL Validator Guide - Managing Your Deposit

**Complete guide for validators on deposit management, transition preparation, and exiting validation**

> **TESTNET NOTE**: This guide references **Block 550,000** for the testnet (~19 days). Mainnet uses Block 5,000,000 (~6 months).

---

## 📋 Quick Reference

| Action | When | Command | Result |
|--------|------|---------|--------|
| **Check status** | Anytime | `python validator_cli.py` → Option 1 | See deposit, balance, auto-lock status |
| **Enable auto-lock** | Before block 550K | `python validator_cli.py` → Option 2 | Deposit auto-locks at block 550K |
| **Disable auto-lock** | Before block 550K | `python validator_cli.py` → Option 3 | Manual control (you become inactive) |
| **Schedule deposit** | Blocks 525K-550K | `python validator_cli.py` → Option 4 | Pre-schedule deposit for block 550K |
| **Exit validation** | Anytime | `python validator_cli.py` → Option 5 | Request withdrawal (5 min wait) |
| **Get coins back** | After 5 min wait | `python validator_cli.py` → Option 6 | Receive 100 TMPL back to balance |

---

## 🚨 CRITICAL: Deposit Transition at Block 550,000 (Testnet)

### Timeline:

**Blocks 0 - 524,999: Free Validation (~18 days)**
- ✅ NO deposit required
- ✅ Earn rewards immediately
- ✅ Save your earnings!

**Blocks 525,000 - 549,999: Advance Window (~24 hours)**
- ⚠️ Deposit requirement coming soon
- 📅 **OPTIONAL**: Schedule your deposit to auto-lock at block 550K
- 🔧 **OPTIONAL**: Disable auto-lock if you want manual control
- ⏰ Node displays countdown warnings

**Block 550,000: Automatic Transition**
- 🔒 **100 TMPL deposit becomes required** for ALL validators
- ✅ **DEFAULT BEHAVIOR**: If you have ≥100 TMPL, deposit auto-locks (you keep validating)
- ⚠️ **If balance <100 TMPL**: You become inactive (can rejoin anytime by depositing)

**Block 550,001+: Post-Transition**
- 💰 100 TMPL deposit required for all validators
- 🔄 New validators must deposit 100 TMPL to join
- ⚖️ All validators receive equal rewards (NOT Proof-of-Stake!)

---

## 🎯 What You Need to Know

### If You Have ≥100 TMPL by Block 550,000:

✅ **You don't need to do anything!**
- Your deposit will automatically lock at block 550,000
- You'll keep validating with zero downtime
- 100 TMPL will be deducted from your balance and held as deposit

### If You Have <100 TMPL:

⚠️ You'll be marked **inactive** at block 550,000
- You stop earning rewards
- Your existing balance is safe (not slashed or burned)
- You can rejoin later when you acquire 100 TMPL

### If You Want Manual Control (Advanced):

🔧 Disable auto-lock **before block 550,000**:
```bash
python validator_cli.py
# Select option 3: Disable auto-lock
```

**WARNING**: You will become inactive until you manually deposit!

---

## 💰 How to Exit Validation and Get Your Coins Back

### Step 1: Request Withdrawal

```bash
python validator_cli.py
# Select option 5: Request withdrawal
```

**What happens:**
- You are immediately marked **INACTIVE** (stop earning rewards)
- Waiting period begins: **100 blocks** (~5 minutes)
- This allows the network to detect any recent misbehavior

### Step 2: Wait for Waiting Period (100 blocks / ~5 minutes)

Check your status:
```bash
python validator_cli.py
# Select option 1: Check validator status
```

You'll see:
```
📤 Withdrawal Status:
   Requested at block: 1,234,567
   Can withdraw at block: 1,234,667
   ⏳ 50 blocks remaining (~2.5 minutes)
```

### Step 3: Process Withdrawal (Get Coins Back)

After waiting period completes:
```bash
python validator_cli.py
# Select option 6: Process withdrawal
```

**What happens:**
- Your **100 TMPL deposit is returned** to your balance
- You are fully deregistered as a validator
- Coins are immediately available for spending

---

## 📖 Detailed Instructions

### How to Use the Validator CLI

1. **Start the tool:**
   ```bash
   python validator_cli.py
   ```

2. **Enter your wallet PIN** when prompted

3. **Select an option** from the menu

### Option 1: Check Validator Status

Shows comprehensive information:
- Your address and registration status
- Current balance
- Deposit amount (if any)
- Auto-lock preference (enabled/disabled)
- Scheduled deposit (if scheduled)
- Withdrawal status (if requested)
- Current blockchain height
- Blocks remaining until transition

**Example output:**
```
======================================================================
  VALIDATOR STATUS
======================================================================

Address: tmpl2e5fdbfd278015e38e81554dbeafcacb6b6c81057cd8
Registered: ✅ Yes
Balance: 250.5000 TMPL

Deposit Amount: 0.0000 TMPL
Status: active
Auto-lock Enabled: True

Current block height: 3,500,000
Deposit requirement in: 1,500,000 blocks
```

### Option 2: Enable Auto-Lock

**When to use:** Before block 550,000 (if you disabled it earlier)

**What it does:**
- Sets your preference to auto-lock at block 550,000
- If you have ≥100 TMPL at block 550K, deposit locks automatically
- You continue validating with zero downtime

**Example:**
```
✅ Auto-lock ENABLED
   At block 550,000, your deposit will be automatically locked if you have ≥100 TMPL
```

### Option 3: Disable Auto-Lock

**When to use:** Before block 550,000 (if you want manual control)

**WARNING:** You will become INACTIVE at block 550,000!

**What it does:**
- Disables automatic deposit locking
- You must manually deposit 100 TMPL after block 550,000
- Use this only if you want full manual control

**Example:**
```
🔧 Auto-lock DISABLED
   You will need to manually deposit 100 TMPL at/after block 550,000
   ⚠️  You will become INACTIVE until you deposit manually!
```

### Option 4: Schedule Deposit in Advance

**When to use:** Blocks 525,000 - 549,999 only (~24-hour window)

**What it does:**
- Pre-schedules your deposit to lock at block 550,000
- Overrides auto-lock setting (deposit guaranteed)
- Useful if you want certainty

**Requirements:**
- Must have ≥100 TMPL balance
- Must be within advance window (blocks 525K-550K)

**Example:**
```
✅ Deposit scheduled for block 550,000
   100 TMPL will be locked automatically at the transition
   Your balance: 250.0000 TMPL → 150.0000 TMPL after transition
```

### Option 5: Request Withdrawal

**When to use:** Anytime you want to exit validation

**What it does:**
- Starts the exit process
- Marks you inactive immediately (no more rewards)
- Begins 100-block (~5 minute) waiting period

**Example:**
```
📤 Requesting withdrawal...
   Deposit amount: 100.0000 TMPL
   Waiting period: 100 blocks (~5 minutes)

Confirm withdrawal request? (yes/no): yes

✅ Withdrawal requested successfully!
   Withdrawal allowed at height 1,234,667 (~5 minutes)

⚠️  You are now INACTIVE and will not receive block rewards
   Come back after the waiting period to process your withdrawal
```

### Option 6: Process Withdrawal

**When to use:** After 100-block waiting period completes

**What it does:**
- Returns your 100 TMPL deposit to your balance
- Completes deregistration
- Coins available immediately

**Example:**
```
💰 Processing withdrawal...
   Deposit amount: 100.0000 TMPL

Confirm final withdrawal? (yes/no): yes

✅ Withdrawal complete!
   Returned: 100.0000 TMPL
   New balance: 350.5000 TMPL

🎉 You have successfully exited validation!
```

---

## ❓ Frequently Asked Questions

### Q: What is auto-lock?

**A:** Auto-lock is the **default behavior** where your 100 TMPL deposit automatically locks at block 550,000 (testnet) if you have sufficient balance. You don't need to do anything - it happens automatically.

### Q: Do I need to schedule my deposit in advance?

**A:** No! Auto-lock is enabled by default. Scheduling is **optional** and only useful if you want extra certainty or if you previously disabled auto-lock.

### Q: What happens if I have exactly 100 TMPL?

**A:** Your deposit will auto-lock (100 TMPL deducted), leaving you with 0 TMPL balance. You can still validate and earn rewards to build your balance back up.

### Q: What if I have 50 TMPL at block 550,000?

**A:** You'll be marked inactive because you don't have enough for the deposit. You can rejoin later when you acquire 100 TMPL by:
1. Earning rewards elsewhere
2. Receiving transfers
3. Manually depositing when you have enough

### Q: Is this Proof-of-Stake?

**A:** **NO!** This is NOT Proof-of-Stake:
- You **cannot** stake more than 100 TMPL for extra power
- All validators receive **equal rewards** regardless of deposit amount
- The 100 TMPL is an anti-Sybil barrier, not proportional staking
- Running 1,000 validators costs 100,000 TMPL (prevents spam)

### Q: Will I lose my coins if I get slashed?

**A:** TIMPAL does NOT use slashing. Your deposit is returned in full when you exit. There are no penalties or confiscation of funds.

### Q: How long does withdrawal take?

**A:** Withdrawal takes **100 blocks** (~5 minutes):
1. Request withdrawal (immediate, marks you inactive)
2. Wait 100 blocks
3. Process withdrawal (immediate, get coins back)

### Q: Can I cancel a withdrawal request?

**A:** No. Once requested, you must wait the full period. However, you can simply not process the withdrawal and keep validating (though you won't earn rewards during the waiting period).

### Q: What if I want to be a validator again after withdrawing?

**A:** Just register as a validator again and deposit 100 TMPL. There's no penalty for leaving and rejoining.

### Q: When should I disable auto-lock?

**A:** Only disable auto-lock if you:
- Want full manual control over your deposit
- Are comfortable becoming inactive at block 550K
- Plan to manually deposit later

Most validators should keep auto-lock enabled (default).

---

## 🎯 Best Practices

### For Maximum Uptime:
1. **Save your earnings** during the free validation period (blocks 0-525K)
2. **Keep auto-lock enabled** (default - don't disable it)
3. **Ensure you have ≥100 TMPL** by block 550,000
4. **Monitor your balance** regularly

### For Manual Control:
1. Disable auto-lock **before block 550,000**
2. Accept that you'll become inactive at block 550K
3. Manually deposit when ready (via transaction)

### For Exiting:
1. Request withdrawal when ready to exit
2. Wait 100 blocks (~5 minutes)
3. Process withdrawal to get your 100 TMPL back
4. Coins are available immediately after withdrawal

---

## 🛡️ Security Notes

- **Your deposit is safe**: No slashing in TIMPAL
- **Waiting period protects you**: 100 blocks allows network to detect misbehavior before funds are returned
- **PIN protected**: All actions require your wallet PIN
- **Full control**: You decide when to exit and when to join

---

## 📞 Need Help?

- **Check your status**: `python validator_cli.py` → Option 1
- **Read this guide**: Comprehensive instructions above
- **Check the whitepaper**: `WHITEPAPER.md` for technical details
- **GitHub Issues**: For technical support and bug reports

---

**Last Updated:** November 11, 2025  
**Status:** Production-ready for Testnet Launch (550,000 block grace period)
