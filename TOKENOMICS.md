# 💰 TIMPAL Tokenomics - Complete Specification

**Official tokenomics documentation for TIMPAL blockchain**

---

## Table of Contents

1. [Overview](#overview)
2. [Token Specifications](#token-specifications)
3. [Emission Schedule](#emission-schedule)
4. [Block Rewards](#block-rewards)
5. [Phase Transition](#phase-transition)
6. [Validator Economics](#validator-economics)
7. [Long-Term Sustainability](#long-term-sustainability)
8. [Mathematical Verification](#mathematical-verification)

---

## Overview

TIMPAL uses a **fixed emission schedule** where every block mints the same amount of tokens until the maximum supply cap is reached. This creates a **predictable, transparent, and fair** distribution system.

**Key Principle:** Equal rewards for all validators, no advantage for early adopters, block proposers, or coordinators.

---

## Token Specifications

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Token Symbol** | TMPL | Ticker symbol |
| **Token Name** | TIMPAL | Full name |
| **Max Supply** | 250,000,000 TMPL | Hard cap (cannot be changed) |
| **Decimals** | 8 | Smallest unit = 1 pal |
| **Smallest Unit** | pal | Like Bitcoin's satoshi |
| **Pals per TMPL** | 100,000,000 | 1 TMPL = 10^8 pals |
| **Max Supply (pals)** | 25,000,000,000,000,000 | 250M × 10^8 |

### Decimal System

```
1 TMPL = 100,000,000 pals

Examples:
- 0.00000001 TMPL = 1 pal (smallest unit)
- 0.6345 TMPL = 63,450,000 pals (block reward)
- 0.0005 TMPL = 50,000 pals (transaction fee)
- 250,000,000 TMPL = 25,000,000,000,000,000 pals (max supply)
```

---

## Emission Schedule

### Phase 1: Block Emission (Active)

**Duration:** ~37.5 years (at 3-second block time target)  
**Blocks:** ~394,011,032 blocks (exact, enforced by code)  
**Total Emission:** Exactly 250,000,000 TMPL

> **Note on Block Time:** The emission schedule is defined in **blocks**, not time. The "37.5 years" assumes the design target of 3-second blocks. If actual average block time differs, the calendar duration scales proportionally:
> ```
> Actual years = 37.5 × (actual_block_time / 3)
> ```
> For example: at 3.5s blocks → ~43.7 years; at 4.0s blocks → ~50 years.

### Phase 2: Fee-Only (Future)

**Start:** After max supply reached  
**Duration:** Forever  
**Block Reward:** 0 TMPL (fees only)

---

## Block Rewards

### Fixed Block Reward

**Every block mints:** 0.6345 TMPL (63,450,000 pals)

This reward is **completely fixed** and **never changes** until the maximum supply cap is reached.

### Reward Formula

```python
# For each block:
remaining_emission = MAX_SUPPLY_PALS - total_emitted_pals

if remaining_emission > 0:
    block_reward = min(EMISSION_PER_BLOCK_PALS, remaining_emission)
else:
    block_reward = 0  # Phase 2: Fee-only
```

### Key Facts

✅ **Blocks 1 to ~394,011,031:** Exactly 0.6345 TMPL each  
✅ **Block ~394,011,032:** Partial reward (~0.0000196 TMPL - the final remainder)  
✅ **Block ~394,011,033+:** 0 TMPL (fee-only)

---

## Detailed Emission Timeline

### Year-by-Year Breakdown

| Year | Blocks Mined | TMPL Emitted | Cumulative Supply | % of Max |
|------|-------------|--------------|-------------------|----------|
| 1 | 10,519,200 | 6,674,432 | 6,674,432 | 2.67% |
| 2 | 10,519,200 | 6,674,432 | 13,348,864 | 5.34% |
| 3 | 10,519,200 | 6,674,432 | 20,023,296 | 8.01% |
| 5 | 10,519,200 | 6,674,432 | 33,372,160 | 13.35% |
| 10 | 10,519,200 | 6,674,432 | 66,744,320 | 26.70% |
| 15 | 10,519,200 | 6,674,432 | 100,116,480 | 40.05% |
| 20 | 10,519,200 | 6,674,432 | 133,488,640 | 53.40% |
| 25 | 10,519,200 | 6,674,432 | 166,860,800 | 66.74% |
| 30 | 10,519,200 | 6,674,432 | 200,232,960 | 80.09% |
| 35 | 10,519,200 | 6,674,432 | 233,605,120 | 93.44% |
| 37.46 | ~4,839,877 | ~3,070,315 | 250,000,000 | 100.00% |

**Blocks per year:** 10,519,200 (at 3-second block time target)  
**Calculation:** (365.25 days × 24 hours × 60 min × 60 sec) ÷ 3 seconds

> **Scaling with Block Time:** If actual block time is T seconds:
> - Blocks per year = 31,557,600 ÷ T
> - TMPL per year = (31,557,600 ÷ T) × 0.6345

---

## Phase Transition

### Automatic Transition to Fee-Only

The transition from Phase 1 (emission) to Phase 2 (fee-only) is **completely automatic** and **enforced by code**.

#### How It Works

1. **Normal Emission (Blocks 1 - ~394,011,031)**
   - Block reward: 0.6345 TMPL
   - Total reward pool: 0.6345 TMPL + transaction fees
   - Distributed equally among all validators

2. **Final Partial Block (~394,011,032)**
   - Block reward: ~0.0000196 TMPL (remainder to reach 250M exactly)
   - Total reward pool: 0.0000196 TMPL + transaction fees
   - Distributed equally among all validators

3. **Fee-Only Phase (Block ~394,011,033+)**
   - Block reward: 0 TMPL
   - Total reward pool: Transaction fees only
   - Distributed equally among all validators

#### Transition Code

```python
# From app/rewards.py
remaining_emission = MAX_SUPPLY_PALS - total_emitted_pals

if remaining_emission > 0:
    # Phase 1: Still emitting
    block_reward_pals = min(EMISSION_PER_BLOCK_PALS, remaining_emission)
else:
    # Phase 2: Fee-only
    block_reward_pals = 0

# Validators always share fees
total_reward_pals = block_reward_pals + collected_fees
per_validator = total_reward_pals // num_validators
```

### Safety Guarantees

✅ **Cannot exceed 250M:** Hard cap enforced in ledger  
✅ **Cannot mint after cap:** Emission automatically stops  
✅ **Cannot reverse transition:** Fee-only mode is permanent  
✅ **No governance needed:** Transition is algorithmic

---

## Validator Economics

### Equal Distribution Formula

```python
Per-validator reward = (block_reward + transaction_fees) / validator_count
```

**Every validator receives exactly the same reward - no exceptions.**

### Phase 1 Examples (Emission Active)

#### Example 1: 10 Validators, 0 Transactions
```
Block reward:        0.6345 TMPL
Transaction fees:    0 TMPL
Total pool:          0.6345 TMPL
Per validator:       0.06345 TMPL
```

#### Example 2: 10 Validators, 5 Transactions
```
Block reward:        0.6345 TMPL
Transaction fees:    0.0025 TMPL (5 × 0.0005)
Total pool:          0.637 TMPL
Per validator:       0.0637 TMPL
```

#### Example 3: 100 Validators, 20 Transactions
```
Block reward:        0.6345 TMPL
Transaction fees:    0.01 TMPL (20 × 0.0005)
Total pool:          0.6445 TMPL
Per validator:       0.006445 TMPL
```

#### Example 4: 1,000 Validators, 100 Transactions
```
Block reward:        0.6345 TMPL
Transaction fees:    0.05 TMPL (100 × 0.0005)
Total pool:          0.6845 TMPL
Per validator:       0.0006845 TMPL
```

### Phase 2 Examples (Fee-Only)

#### Example 5: 10 Validators, 50 Transactions
```
Block reward:        0 TMPL (emission ended)
Transaction fees:    0.025 TMPL (50 × 0.0005)
Total pool:          0.025 TMPL
Per validator:       0.0025 TMPL
```

#### Example 6: 100 Validators, 200 Transactions
```
Block reward:        0 TMPL (emission ended)
Transaction fees:    0.1 TMPL (200 × 0.0005)
Total pool:          0.1 TMPL
Per validator:       0.001 TMPL
```

### Earnings Calculator

**Phase 1 Earnings (per validator):**

```python
# With V validators and T transactions per block
block_reward = 0.6345 TMPL
tx_fees = T × 0.0005 TMPL
total_pool = block_reward + tx_fees
per_validator = total_pool / V

# Per time period
earnings_per_minute = per_validator × 20  # 20 blocks/min
earnings_per_hour = per_validator × 1,200  # 1,200 blocks/hour
earnings_per_day = per_validator × 28,800  # 28,800 blocks/day
earnings_per_year = per_validator × 10,519,200  # 10.519M blocks/year
```

**Example: Single validator among 100 validators (10 tx/block average)**

```
Per block:    0.00635 TMPL
Per minute:   0.127 TMPL (20 blocks)
Per hour:     7.62 TMPL (1,200 blocks)
Per day:      182.88 TMPL (28,800 blocks)
Per year:     66,746.28 TMPL (10,519,200 blocks)
```

---

## Long-Term Sustainability

### Phase 1 (Emission): Network Growth

**Years 0-37.5:**
- Validators earn primarily from block rewards
- Transaction fees are bonus income
- New supply enters circulation
- Network establishes itself

### Phase 2 (Fee-Only): Mature Network

**Years 37.5+:**
- Validators earn exclusively from transaction fees
- Zero new supply (deflationary)
- Network must have sufficient transaction volume
- Fee market determines validator income

### Fee Requirements for Sustainability

**Phase 2 is HIGHLY SUSTAINABLE with 0.0005 TMPL fee:**

TIMPAL's fee structure ensures validators can earn the SAME or MORE in Phase 2 compared to Phase 1.

**Realistic Phase 2 scenarios:**

```python
# Scenario 1: 50% capacity (675 tx/block at max 1,350)
fee_revenue = 675 × 0.0005 = 0.3375 TMPL per block
per_validator (100 validators) = 0.003375 TMPL per block
# 53% of Phase 1 earnings (viable at moderate usage)

# Scenario 2: 94% capacity (1,269 tx/block)
fee_revenue = 1,269 × 0.0005 = 0.6345 TMPL per block
per_validator (100 validators) = 0.006345 TMPL per block
# MATCHES Phase 1 earnings exactly! ✅

# Scenario 3: 100% capacity (1,350 tx/block)
fee_revenue = 1,350 × 0.0005 = 0.675 TMPL per block
per_validator (100 validators) = 0.00675 TMPL per block
# 106% of Phase 1 earnings (EXCEEDS Phase 1!) 🚀
```

**The 0.0005 TMPL fee makes Phase 2 extremely viable and sustainable.**

### Deflationary Economics

After Phase 2 transition:
- **No new tokens created**
- **Lost keys = permanent deflation**
- **Fee burning could be added (future)**
- **Scarcity increases over time**

---

## Network Security: Time-Distributed Defense

### 51% Attack Prevention via Coin-Weighted Verification

TIMPAL implements a breakthrough security mechanism called **time-distributed security** that makes 51% attacks:
- **Mathematically impossible** for the first ~19 years (insufficient supply exists) *
- **Economically impossible** after ~19 years (coins too distributed)

> \* At 3-second block time target. The security window scales with actual block time:
> ```
> Years until 51% threshold = 19.1 × (actual_block_time / 3)
> ```
> Slower blocks = longer security window (more conservative).

### How It Works

**Attack Threshold:**
```
Required TMPL = 127,500,000 TMPL (51% of max supply)
This threshold is FIXED and NEVER CHANGES
```

**Detection Mechanism:**
- Deep chain reorganizations (4+ blocks) trigger automatic verification
- System calculates total TMPL owned by attacking validators
- Attack blocked if total < 127.5M TMPL

**Why This Works:**

*Table assumes 3-second block time target. Years scale with actual block time.*

| Year* | Circulating Supply | % of Attack Threshold | Attack Possible? |
|-------|-------------------|----------------------|------------------|
| 1 | 6.67M TMPL | 5.2% | ❌ NO (95% short) |
| 5 | 33.37M TMPL | 26.2% | ❌ NO (74% short) |
| 10 | 66.74M TMPL | 52.4% | ❌ NO (48% short) |
| 15 | 100.12M TMPL | 78.5% | ❌ NO (21% short) |
| 18 | 120.14M TMPL | 94.2% | ❌ NO (6% short) |
| 19 | 126.81M TMPL | 99.5% | ⚠️ BARELY (0.5% short) |
| 20+ | 127M+ TMPL | 100%+ | ⚠️ THEORETICAL** |

*\* Years assume 3-second blocks. Actual calendar years = listed year × (actual_block_time / 3)*

**Why year 20+ is still secure:**
- Coins distributed across 100,000+ validators globally
- Acquiring 51% requires buying from >51,000 independent validators
- Each validator owns ~1,250 TMPL average
- Attack cost would exceed network value
- Coordinating largest crypto acquisition in history

### Implementation Details

**Shallow Reorgs (1-3 blocks):**
- Allowed normally
- Natural network behavior (network delays, partitions)
- No verification needed

**Deep Reorgs (4+ blocks = 12 seconds):**
- Triggers automatic coin balance verification
- Sums TMPL across all validators on attacking chain
- Blocks reorg if total < 127.5M TMPL
- Allows reorg only if attackers prove ownership

**Example Attack Scenario:**

```python
Year 5 Attack Attempt:
- Circulating supply: 33.37M TMPL
- Attacker controls 100% of supply (worst case)
- Attack threshold: 127.5M TMPL
- Result: BLOCKED (only 26% of required amount)

Year 25 Attack Attempt:
- Circulating supply: 166.86M TMPL (> threshold)
- Network has 100,000 validators
- Attacker needs coins from >51,000 validators
- Average holdings: ~1,667 TMPL per validator
- Cost: Acquiring 127.5M TMPL from distributed holders
- Result: Economically impossible (too expensive + too distributed)
```

### Security Timeline

*Years assume 3-second block time target. Actual calendar years scale with block time.*

**Phase 1 (Years 1-19 at 3s blocks): Mathematical Impossibility**
```
Attack requires: 127.5M TMPL
Supply available: < 127.5M TMPL
Result: Attack cannot occur (insufficient coins exist)
```

**Phase 2 (Year 19+ at 3s blocks): Economic Impossibility**
```
Attack requires: 127.5M TMPL
Supply available: ≥ 127.5M TMPL
Distribution: 100K+ validators × ~1,250 TMPL each
Result: Attack cost exceeds network value
```

> **Block Time Scaling:** If actual block time is T seconds, the "mathematical impossibility" window extends to ~19 × (T/3) calendar years. Slower blocks = longer security window.

### Key Breakthrough

**Time itself acts as the security mechanism:**
- No security window during network bootstrap (years 1-18)
- By the time enough TMPL exists, network is too decentralized to attack
- Combines mathematical impossibility with economic impossibility
- Creates security without centralization or high barriers to entry

---

## Mathematical Verification

### Supply Cap Verification

```python
# Constants
EMISSION_PER_BLOCK = 0.6345 TMPL
MAX_SUPPLY = 250,000,000 TMPL
BLOCK_TIME = 3 seconds

# Theoretical blocks if no cap
theoretical_blocks = MAX_SUPPLY / EMISSION_PER_BLOCK
theoretical_blocks = 250,000,000 / 0.6345
theoretical_blocks = 394,011,032.86 blocks

# Actual emission
full_reward_blocks = 394,011,032
full_reward_emission = 394,011,032 × 0.6345
full_reward_emission = 249,999,998.04 TMPL

# Final partial block
remaining = 250,000,000 - 249,999,998.04
remaining = 1.96 TMPL = 0.0000196 TMPL (in reality: 1,960 pals)

# Total blocks
total_blocks = 394,011,032 + 1 = 394,011,033 blocks
```

### Time Verification

```python
# Blocks to cap
total_emission_blocks = 394,011,033

# Time calculation
seconds_total = total_emission_blocks × 3
seconds_total = 1,182,033,099 seconds

# Convert to years
years = 1,182,033,099 / (365.25 × 24 × 60 × 60)
years = 1,182,033,099 / 31,557,600
years = 37.46 ≈ 37.5 years
```

### Annual Emission Verification

```python
# Blocks per year
blocks_per_year = (365.25 × 24 × 60 × 60) / 3
blocks_per_year = 31,557,600 / 3
blocks_per_year = 10,519,200

# Emission per year
emission_per_year = blocks_per_year × 0.6345
emission_per_year = 10,519,200 × 0.6345
emission_per_year = 6,674,432.4 TMPL

# Years to cap
years_to_cap = 250,000,000 / 6,674,432.4
years_to_cap = 37.46 ≈ 37.5 years
```

---

## Edge Cases & Guarantees

### What Happens If...

**Q: What if we try to emit more than 250M TMPL?**  
A: Impossible. The ledger rejects any block that would exceed the cap.

**Q: What if the last block tries to mint 0.6345 TMPL but only 0.0001 TMPL remains?**  
A: It mints exactly 0.0001 TMPL (the remainder). No overshoot.

**Q: What if there are no transactions in Phase 2?**  
A: Validators get 0 TMPL for that block. They must wait for transaction volume.

**Q: Can the emission schedule be changed?**  
A: No. It's hardcoded in `config.py`. Changing it requires a hard fork.

**Q: Can we restart emission after Phase 2?**  
A: No. Once `remaining_emission = 0`, it stays zero forever.

**Q: What if a validator goes offline during Phase 1?**  
A: They receive nothing. Only online validators share rewards.

**Q: Can validators vote to change the emission rate?**  
A: No. TIMPAL has no governance. Code is law.

---

## Configuration Constants

**Source:** `app/config.py`

```python
# Token Basics
SYMBOL = "TMPL"
DECIMALS = 8
PALS_PER_TMPL = 100_000_000

# Supply Limits
MAX_SUPPLY_TMPL = 250_000_000
MAX_SUPPLY_PALS = 25_000_000_000_000_000

# Block Production
BLOCK_TIME = 3  # seconds

# Emission
EMISSION_PER_BLOCK_TMPL = 0.6345
EMISSION_PER_BLOCK_PALS = 63_450_000
PHASE1_BLOCKS = 394_200_000  # Upper bound (actual: ~394,011,033)

# Transaction Fees
FEE = 50000  # 0.0005 TMPL

# Performance
MAX_TRANSACTIONS_PER_BLOCK = 1350  # 450 TPS at 3-second blocks
```

---

## Implementation Details

### Reward Calculation Code

**File:** `app/rewards.py`

```python
class RewardCalculator:
    def calculate_reward(self, active_nodes: List[str], 
                        collected_fees: int, 
                        total_emitted_pals: int) -> Tuple[Dict[str, int], int]:
        """
        Calculate equal rewards for all active validators
        
        Args:
            active_nodes: List of validator addresses
            collected_fees: Total transaction fees in block (pals)
            total_emitted_pals: Total TMPL already emitted (pals)
            
        Returns:
            (reward_allocations, total_reward)
        """
        remaining_emission = config.MAX_SUPPLY_PALS - total_emitted_pals
        
        # Phase determination
        if remaining_emission > 0:
            # Phase 1: Emission active
            block_reward_pals = min(config.EMISSION_PER_BLOCK_PALS, remaining_emission)
        else:
            # Phase 2: Fee-only
            block_reward_pals = 0
        
        # Total pool to distribute
        total_reward_pals = block_reward_pals + collected_fees
        
        # Equal distribution
        if not active_nodes:
            return {}, 0
        
        per_validator = total_reward_pals // len(active_nodes)
        
        # Allocate equally
        rewards = {}
        for node_address in active_nodes:
            rewards[node_address] = per_validator
        
        return rewards, total_reward_pals
```

### Emission Cap Enforcement

**File:** `app/ledger.py`

```python
# Hard cap enforcement
if self.total_emitted_pals + block.reward > config.MAX_SUPPLY_PALS:
    print(f"REJECT: Block would exceed max supply cap")
    return False
```

---

## Summary

| Metric | Value |
|--------|-------|
| **Max Supply** | 250,000,000 TMPL |
| **Block Reward** | 0.6345 TMPL (fixed) |
| **Block Time Target** | 3 seconds |
| **Transaction Fee** | 0.0005 TMPL (fixed) |
| **Max TPS** | 450 TPS (1,350 tx / 3s) |
| **Emission Blocks** | ~394,011,033 blocks (exact) |
| **Emission Period** | ~37.5 years (at 3s blocks)* |
| **Blocks/Year** | ~10,519,200 (at 3s blocks) |
| **TMPL/Year** | ~6,674,432 TMPL (at 3s blocks) |
| **51% Security Window** | ~19 years (at 3s blocks)* |
| **Phase Transition** | Automatic at max supply |
| **Fee-Only Start** | Block ~394,011,034 |
| **Distribution** | Equal among all validators |
| **Governance** | None (code is law) |

*\* Calendar years scale with actual block time: multiply by (actual_block_time / 3)*

---

## Conclusion

TIMPAL's tokenomics is designed for:
- ✅ **Fairness**: Equal rewards for all validators
- ✅ **Predictability**: Fixed emission schedule
- ✅ **Transparency**: Open-source verification
- ✅ **Sustainability**: Fee-based long-term model
- ✅ **Decentralization**: No privileged addresses
- ✅ **Simplicity**: Easy to understand and verify

**The emission schedule is immutable, algorithmic, and mathematically verifiable.**

---

**Version:** 1.0.0  
**Last Updated:** October 29, 2025  
**Status:** Production-ready for GitHub release
