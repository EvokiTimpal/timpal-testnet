# TIMPAL Testnet Usage Guide

## Overview

The testnet is a **completely separate network** from mainnet for testing all blockchain functionalities. Testnet coins have **NO VALUE** and the network can be reset at any time.

---

## 🔑 Key Differences: Testnet vs Mainnet

| Feature | Mainnet (`config.py`) | Testnet (`config_testnet.py`) |
|---------|----------------------|-------------------------------|
| **Chain ID** | `timpal-genesis` | `timpal-testnet` |
| **Coin Value** | Real value | NO VALUE |
| **Validator Limits** | Economic barrier only | **UNLIMITED** |
| **Grace Period** | 5M blocks (~6 months) | **550,000 blocks (~19 days)** - adjustable |
| **Network** | Production | Testing/Development |
| **Reset** | Never | Can reset anytime |

---

## 🚀 How to Use Testnet Config

### Option 1: Import Testnet Config in Your Code

```python
# Instead of:
import config

# Use:
import config_testnet as config
```

### Option 2: Environment Variable

```python
import os

if os.getenv('TESTNET') == 'true':
    import config_testnet as config
else:
    import config
```

Then run:
```bash
TESTNET=true python app/node.py
```

---

## 🧪 Dynamic Testing Features

### Adjustable Grace Period

Edit `config_testnet.py` to test different transition scenarios:

```python
# Current testnet setting (19 days)
DEPOSIT_GRACE_PERIOD_BLOCKS = 550_000

# Fast testing (5 minutes)
DEPOSIT_GRACE_PERIOD_BLOCKS = 100

# Medium testing (50 minutes)  
DEPOSIT_GRACE_PERIOD_BLOCKS = 1_000

# Long testing (3.5 days)
DEPOSIT_GRACE_PERIOD_BLOCKS = 100_000
```

### No Validator Limits

```python
MAX_VALIDATORS = None  # Unlimited - test 100,000+ validators
```

### Testing Configuration Flags

The `TESTING_CONFIG` dictionary allows you to enable/disable test features:

```python
TESTING_CONFIG = {
    "fast_transition_mode": True,      # Quick deposit testing
    "no_validator_limits": True,       # Unlimited validators
    "test_100k_validators": True,      # Large-scale simulation
    "test_all_tx_types": True,         # All transaction types
    "verbose_logging": True,           # Detailed logs
}
```

---

## 📊 What You Can Test

### 1. **Deposit Transition System**
- Grace period expiration
- Auto-deposit from balances
- Validator removal (insufficient balance)
- Network continuity during transition

### 2. **Withdrawal System**
- Request withdrawal
- 100-block waiting period
- Process withdrawal (get 100 TMPL back)
- Exit validator pool

### 3. **Scalability**
- 100+ validators
- 1,000+ validators  
- 10,000+ validators
- 100,000+ validators (stress test)

### 4. **Consensus**
- Round-robin pool selection
- Equal reward distribution
- Block proposal rotation

### 5. **Network Features**
- P2P synchronization
- Chain reorganization
- Finality checkpoints
- Transaction processing

---

## 🛡️ Safety

- **Mainnet is PROTECTED** - `config.py` remains unchanged
- **Testnet is ISOLATED** - Separate chain ID prevents cross-contamination
- **No Risk** - Testnet coins have no value

---

## 🔄 Resetting Testnet

To start fresh:

```bash
# Delete testnet data
rm -rf testnet_data_*

# Restart testnet nodes
python run_testnet_node.py --port 9000
```

---

## 📝 Example: Fast Deposit Testing

1. **Current grace period** (set to 550,000 blocks = ~19 days)
2. **Start testnet node**
3. **Create validator with 100+ TMPL balance**
4. **Wait 5 minutes** (100 blocks)
5. **Verify auto-deposit occurs**
6. **Check balance deduction**
7. **Confirm validator stays active**

---

## 💡 Tips

- Use verbose logging for debugging: `TESTING_CONFIG["verbose_logging"] = True`
- Test edge cases: exactly 100 TMPL, 99.99999999 TMPL, etc.
- Simulate network failures: stop/start nodes
- Test with multiple validators: run 10+ testnet nodes simultaneously
- Monitor the Block Explorer on testnet data

---

**Remember:** Testnet is for testing only. Always verify on mainnet config before production deployment!
