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
| **Check status** | Anytime | `python validator_cli.py` → Option 1 | See deposit, balance, status |
| **Exit validation** | Anytime | `python validator_cli.py` → Option 5 | Request withdrawal (5 min wait) |
| **Get coins back** | After 5 min wait | `python validator_cli.py` → Option 6 | Receive 100 TMPL back to balance |

---

## 🚨 CRITICAL: Deposit Transition at Block 550,000 (Testnet)

### Timeline:

**Blocks 0 - 549,999: Free Validation (~19 days)**
- ✅ NO deposit required
- ✅ Earn rewards immediately
- ✅ Save your earnings to prepare for the transition!

**Block 550,000: Automatic Transition**
- 🔒 **100 TMPL deposit becomes required** for ALL validators
- ✅ **If you have ≥100 TMPL**: Deposit auto-locks, you keep validating
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

### Q: How does the automatic deposit work?

**A:** At block 550,000 (testnet), the system automatically locks 100 TMPL from your balance as a deposit if you have sufficient funds. You don't need to do anything - it happens automatically. If you have less than 100 TMPL, you'll be marked inactive until you deposit.

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

---

## 🎯 Best Practices

### For Maximum Uptime:
1. **Save your earnings** during the free validation period (blocks 0-549,999)
2. **Ensure you have ≥100 TMPL** by block 550,000
3. **Monitor your balance** regularly

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
