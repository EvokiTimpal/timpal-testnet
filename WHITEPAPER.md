<div align="center">
  <img src="app/static/timpal-logo.png" alt="TIMPAL Logo" width="150" />
</div>

# TIMPAL: A Peer-to-Peer Blockchain with Equal Validator Rewards

**Abstract.** A purely peer-to-peer blockchain would allow participants to earn equal rewards regardless of computational power, stake size, or join time. Traditional Proof-of-Work blockchains concentrate rewards among miners with specialized hardware, while Proof-of-Stake systems favor early adopters and large token holders. We propose a solution using VRF-based (Verifiable Random Function) validator selection with device fingerprinting that enforces one validator per physical device, ensuring true decentralization and equal reward distribution. Each active validator receives an identical share of block rewards and transaction fees, creating a truly egalitarian blockchain economy.

---

## 1. Introduction

The current blockchain landscape is dominated by two paradigms: Proof-of-Work (PoW) and Proof-of-Stake (PoS). Both systems create inherent inequalities:

**Proof-of-Work Problems:**
- Rewards concentrate among miners with expensive ASIC hardware
- Energy consumption scales exponentially with network security
- Individual participation becomes economically unviable
- Centralization emerges through mining pools

**Proof-of-Stake Problems:**
- Early adopters gain disproportionate influence
- Large token holders earn more rewards ("rich get richer")
- Minimum stake requirements exclude small participants
- Validator selection favors wealth over network contribution

What is needed is a system where every participant earns equally, regardless of when they join, how much capital they possess, or their computational resources. The network should enforce one validator per device to prevent Sybil attacks while maintaining permissionless participation.

---

## 2. Equal Reward Distribution

### 2.1 Reward Calculation

Every block reward is divided equally among all active validators:

```
reward_per_validator = (block_emission + transaction_fees) / active_validator_count
```

Where:
- `block_emission` = 0.6345 TMPL (fixed per block)
- `transaction_fees` = sum of all transaction fees in the block
- `active_validator_count` = number of registered validators

### 2.2 No Proposer Premium

Unlike many consensus systems, the validator proposing a block receives no additional reward:

```
proposer_reward = non_proposer_reward = reward_per_validator
```

This ensures true equality and removes incentives for proposer manipulation.

### 2.3 Economic Example

With 100 active validators and 10 transactions (0.0005 TMPL fee each):

```
block_emission = 0.6345 TMPL
transaction_fees = 10 × 0.0005 = 0.005 TMPL
total_reward = 0.6345 + 0.005 = 0.6395 TMPL
per_validator = 0.6395 / 100 = 0.006395 TMPL
```

All 100 validators receive exactly 0.006395 TMPL, regardless of their role in block production.

---

## 3. Sybil Attack Prevention

### 3.1 The Sybil Problem

Without protection, a single actor could run thousands of virtual validators on one machine, controlling the network and capturing the majority of rewards.

### 3.2 Device Fingerprinting

TIMPAL enforces one validator per physical device using hardware fingerprinting:

```python
device_fingerprint = hash(
    cpu_id,
    mac_address,
    motherboard_serial,
    disk_serial,
    hardware_uuid
)
```

### 3.3 On-Chain Enforcement

Validator registration requires:
1. Unique device fingerprint (never seen before)
2. Cryptographic signature from device-specific keypair
3. On-chain transaction recorded in blockchain state
4. Network-wide verification by all validators

Attempting to register a duplicate device fingerprint results in transaction rejection.

### 3.4 Unlimited Non-Validator Wallets

Users can create unlimited wallets for:
- Sending and receiving transactions
- Business operations
- Multiple accounts

The one-per-device limit applies **only to validators earning rewards**, not to regular wallets.

### 3.5 Economic Barrier (Post-Grace Period)

**CRITICAL: This is NOT Proof-of-Stake!** The 100 TMPL deposit is a fixed anti-Sybil barrier, not proportional staking. All validators receive equal rewards regardless of deposit amount.

TIMPAL implements a **two-phase economic model**:

**Phase 1: Bootstrap Period**
- **Testnet**: Blocks 0-550,000 (~19 days)
- **Mainnet**: Blocks 0-5,000,000 (~6 months)
- **NO deposit required** for validator registration
- FREE and open participation to enable rapid network growth
- Device fingerprinting provides primary Sybil defense
- Allows TMPL to distribute widely across genuine participants

**Phase 2: Mature Network**
- **Testnet**: Block 550,000+
- **Mainnet**: Block 5,000,000+
- **100 TMPL deposit required for ALL validators** to stay active
- **CRITICAL**: Network continuity guaranteed through automatic transition
- Makes mass Sybil attacks economically prohibitive

#### Deposit Transition

To ensure the network NEVER stops, TIMPAL uses automatic deposit enforcement:

**At Transition Block (Testnet: 550,000 / Mainnet: 5,000,000)**
- **ONE-TIME automatic enforcement** on all existing validators:
  - Validators with ≥100 TMPL: ✅ Deposit auto-locked, remain active
  - Validators with <100 TMPL: ⚠️ Marked `inactive_pending_deposit`
- Inactive validators removed from proposer pool but keep their balances
- **Network continues** with all validators who have locked deposits
- **Zero downtime** - validators who saved earnings remain active

**After Transition**
- New validators must explicitly lock 100 TMPL to register
- Inactive validators can rejoin anytime by depositing 100 TMPL
- Deposit is locked (not burned) and returned upon voluntary exit
- **CRITICAL FAIRNESS**: Rules apply equally regardless of join time

#### Why This is NOT Staking:

| Feature | Proof-of-Stake | TIMPAL |
|---------|----------------|---------|
| **Deposit Amount** | Variable (stake more = earn more) | Fixed (100 TMPL only) |
| **Rewards** | Proportional to stake | Equal for all validators |
| **Consensus Power** | Weighted by stake | Equal (VRF-based selection) |
| **Purpose** | Earn more by risking more | Anti-Sybil entry barrier |
| **Can Stake More?** | Yes (compound earnings) | No (100 TMPL maximum) |

#### Economic Attack Analysis

Running multiple fake validators becomes expensive:

```
10 validators     = 1,000 TMPL
100 validators    = 10,000 TMPL
1,000 validators  = 100,000 TMPL
10,000 validators = 1,000,000 TMPL
```

This layered approach provides:
1. **Device fingerprinting**: Prevents casual Sybil attacks (physical barrier)
2. **Economic barrier**: Prevents sophisticated Sybil attacks at scale (cost barrier)
3. **Bootstrap period**: Enables organic network growth without barriers (time window)

#### Deposit Mechanics

- **Lock Period**: Deposits locked while actively validating
- **Slashing**: **None** - TIMPAL has no slashing (not PoS)
- **Withdrawal**: Instant return upon deregistration
- **Refund**: Full 100 TMPL deposit always returned (no penalties)

---

## 4. Consensus Mechanism

### 4.1 VRF-Based Validator Selection

TIMPAL uses a Verifiable Random Function (VRF) to select block proposers in a cryptographically secure, unpredictable yet deterministic manner:

```python
# Each validator gets a cryptographic score based on:
# - Epoch seed (derived from finalized blocks)
# - Validator address
# - Block height
vrf_score = hash(epoch_seed + validator_address + block_height)

# Validator with LOWEST score wins the right to propose
proposer = validator_with_min(vrf_scores)
```

**Key Properties:**
- **Unpredictable**: No one can predict future proposers (prevents targeted attacks)
- **Deterministic**: All nodes compute the same proposer (consensus guaranteed)
- **Fair**: Over time, all validators propose roughly equal numbers of blocks
- **Secure**: Cannot be manipulated without controlling the blockchain state

### 4.2 Block Production Process

1. **Proposer Selection**: Deterministic based on block height
2. **Block Creation**: Proposer selects transactions from mempool
3. **Block Signing**: Proposer signs block with their private key
4. **Broadcast**: Block sent to all validators via P2P network
5. **Validation**: All validators verify:
   - Correct proposer signature
   - Valid transactions (signatures, balances, nonces)
   - Proper reward distribution
   - Chain continuity
6. **Acceptance**: Validators add block to their local chain
7. **Next Block**: Process repeats with next proposer

### 4.3 Finality

Blocks achieve finality immediately upon acceptance. There are no forks or probabilistic finality:

```
finality_time = 1 block = 3 seconds
```

### 4.4 Network Partition Handling

If the network partitions:
- Each partition continues with available validators
- Upon reconnection, validators sync to longest valid chain
- Block hashes must match exactly (deterministic consensus)
- Invalid chains are rejected

---

## 5. Network Architecture

### 5.1 Peer-to-Peer Discovery

Nodes discover peers through:
1. **Manual configuration**: Initial seed nodes (specified via --seed flag)
2. **Peer gossiping**: Nodes share known peer addresses
3. **DNS seeds**: Volunteer-run DNS services (after network establishes)

### 5.2 Message Authentication

All P2P messages are signed:

```python
message = {
    "type": "new_block" | "new_transaction" | "sync_request",
    "data": {...},
    "sender_id": device_id,
    "signature": sign(hash(data), private_key)
}
```

Recipients verify:
1. Signature authenticity
2. Sender identity
3. Message integrity

### 5.3 Synchronization

New validators joining the network:
1. Connect to any existing node
2. Request blockchain from genesis
3. Verify all blocks and transactions
4. Register as validator (on-chain transaction)
5. Begin participating in consensus
6. Start earning equal rewards

---

## 6. Transaction Model

### 6.1 Transaction Structure

```python
transaction = {
    "tx_hash": sha256(tx_data),
    "sender": "tmpl...",
    "recipient": "tmpl...",
    "amount_pals": int,
    "fee_pals": int,
    "nonce": int,
    "timestamp": unix_timestamp,
    "signature": sign(tx_hash, sender_private_key)
}
```

### 6.2 Nonce-Based Ordering

Each address maintains a sequential nonce:
- Prevents replay attacks
- Ensures transaction ordering
- Detects double-spend attempts

```python
valid = (tx.nonce == account.nonce + 1)
```

### 6.3 Fee Market

Transaction fees are **fixed at 0.0005 TMPL (50,000 pals)** per transaction:
- Fixed fee ensures predictable transaction costs
- All fees distributed equally among validators
- No fee burning (zero waste)

**How Transaction Fees Work:**

When sending TMPL, the sender pays **amount + fee**, while the recipient receives the **exact amount** specified:

**Example Transaction:**
```
Sender wants to send: 2.00000 TMPL to recipient

Calculation:
- Amount to recipient: 2.00000 TMPL
- Transaction fee:      0.0005 TMPL
- Total sender pays:    2.0005 TMPL

Result:
- Sender balance:    -2.0005 TMPL
- Recipient balance: +2.00000 TMPL
- Validators share:  +0.0005 TMPL (distributed equally)
```

**Key Point:** The sender always pays slightly more than the amount being sent. If you want to send exactly 2.00000 TMPL, you must have at least 2.0005 TMPL in your balance.

---

## 7. Tokenomics

### 7.1 Supply Parameters

| Parameter | Value |
|-----------|-------|
| **Max Supply** | 250,000,000 TMPL |
| **Decimals** | 8 (pals) |
| **Block Time Target** | 3 seconds |
| **Block Reward** | 0.6345 TMPL |
| **Emission Blocks** | ~394,011,033 blocks |
| **Emission Period** | ~37.5 years (at 3s blocks)* |

*\* Calendar years scale with actual block time: years = 37.5 × (actual_block_time / 3)*

### 7.2 Emission Schedule

**Phase 1: Emission (Blocks 0 - ~394,011,033)**
- Fixed reward: 0.6345 TMPL per block
- Duration: ~37.5 years at 3-second block time target
- Total emission: 250 million TMPL
- Note: Emission is defined in blocks, not time. Actual calendar duration scales with block time.

**Phase 2: Fee-Only (After block 394,200,000)**
- Block reward: 0 TMPL
- Validator earnings: Transaction fees only
- Deflationary economics
- Self-sustaining network

### 7.3 Economic Security

Network security derives from:
- Equal reward distribution (no centralization incentive)
- Device fingerprinting (Sybil resistance)
- Deterministic consensus (no mining competition)
- Validator count (more validators = more decentralization)

---

## 8. Cryptography

### 8.1 Digital Signatures

- **Algorithm**: ECDSA (Elliptic Curve Digital Signature Algorithm)
- **Curve**: secp256k1 (same as Bitcoin)
- **Key Size**: 256-bit private keys
- **Signature Size**: 64-71 bytes (DER encoding)

### 8.2 Hashing

- **Algorithm**: SHA-256
- **Applications**: 
  - Block hashing
  - Transaction hashing
  - Merkle tree construction
  - Address derivation

### 8.3 Wallet Security

- **Encryption**: AES-256-GCM
- **Key Derivation**: PBKDF2-HMAC-SHA512 (210,000 iterations)
- **Recovery**: BIP39-style 12-word mnemonic phrases
- **PIN Protection**: Minimum 6-digit PIN requirement

---

## 9. Scalability

### 9.1 Current Performance

| Metric | Value |
|--------|-------|
| **Block Time** | 3 seconds |
| **TPS** | 450 transactions/second (1,350 tx per block) |
| **Block Size** | 0.9 MB (accommodates 1,350 transactions at ~650 bytes each) |
| **Validator Limit** | Tested up to 100, designed for 100,000+ |

### 9.2 Scaling Strategy

**Horizontal Scaling:**
- More validators = more network security
- No performance degradation with validator count
- Deterministic consensus scales efficiently

**Vertical Scaling:**
- Larger blocks (if needed)
- Transaction batching
- Signature aggregation (future)

### 9.3 Network Capacity

With 100,000 validators:
- Consensus still deterministic
- Equal rewards still enforced
- 3-second block time maintained
- Global decentralization achieved

---

## 10. Governance

### 10.1 No Governance

TIMPAL has no on-chain governance mechanism by design:
- Rules are fixed in code
- No voting on protocol changes
- No privileged addresses or roles
- Code modifications require hard fork consensus

### 10.2 Protocol Upgrades

Changes to core protocol require:
1. Code modifications published openly
2. Community discussion and review
3. Majority of validators upgrade voluntarily
4. Network transitions when >66% adopt new version

### 10.3 Philosophy

Like Bitcoin, **code is law**:
- Immutable emission schedule
- Fixed consensus rules
- Equal reward distribution unchangeable
- No backdoors or admin keys

---

## 11. Comparison with Existing Systems

### 11.1 vs. Bitcoin (PoW)

| Aspect | Bitcoin | TIMPAL |
|--------|---------|--------|
| **Consensus** | Proof-of-Work | VRF-Based Validator Selection |
| **Rewards** | Miner earns all | Equal split among validators |
| **Energy** | High (mining) | Low (validation only) |
| **Hardware** | ASICs required | Any computer |
| **Centralization** | Mining pools | One validator per device |
| **Finality** | Probabilistic | Immediate |

### 11.2 vs. Ethereum (PoS)

| Aspect | Ethereum | TIMPAL |
|--------|----------|--------|
| **Consensus** | Proof-of-Stake | VRF-Based Validator Selection |
| **Rewards** | Proportional to stake | Equal for all |
| **Entry Barrier** | 32 ETH | 100 TMPL* (after grace period) |
| **Staking Amount** | Variable (32+) | Fixed (cannot stake more) |
| **Centralization** | Large stakers favored | One validator per device |
| **Slashing** | Yes (penalties) | No (not PoS) |

**Important Note:** TIMPAL is **NOT Proof-of-Stake**. The 100 TMPL deposit is a fixed anti-Sybil entry barrier, not proportional staking. Key differences:

- **PoS (Ethereum)**: More stake = More power + More rewards
- **TIMPAL**: Fixed 100 TMPL = Equal power + Equal rewards for everyone

*During the 6-month grace period (~5 million blocks), no deposit is required. After that, 100 TMPL deposit becomes mandatory to prevent Sybil attacks while maintaining equal validator rights.

### 11.3 Unique Advantages

✅ **True equality**: Every participant earns the same  
✅ **No barriers**: Anyone can validate, no money required  
✅ **Energy efficient**: No mining, no staking  
✅ **Sybil resistant**: Device fingerprinting  
✅ **Immediate finality**: No waiting for confirmations  
✅ **Permissionless**: Open participation  

---

## 12. Security Analysis

### 12.1 Attack Vectors

**Double-Spend Attack:**
- **Prevention**: Sequential nonce verification
- **Detection**: Transaction rejected if nonce invalid
- **Cost**: Computationally infeasible (requires signature forgery)

**51% Attack (Chain Reorganization):**
- **Prevention**: Time-distributed security via coin-weighted fork verification
- **Detection**: Deep reorganizations (4+ blocks) trigger TMPL balance check
- **Threshold**: Attackers must own 127.5M TMPL (51% of max supply)
- **Security Window**: Mathematically impossible for ~19 years at 3s blocks (insufficient supply exists)*
- **Long-term**: Economically impossible after ~19 years (coins distributed across 100K+ validators)

*Security window scales with block time: ~19 × (actual_block_time / 3) calendar years
- **Mechanism**: 
  - Shallow reorgs (1-3 blocks): Allowed normally (natural network splits)
  - Deep reorgs (4+ blocks): Require proof of 51% coin ownership
  - Attack blocked if insufficient TMPL balance detected

**Sybil Attack:**
- **Prevention**: One validator per device (hardware fingerprint)
- **Detection**: Duplicate fingerprint rejected on-chain
- **Cost**: Requires purchasing 51% of network's devices

**Network Partition:**
- **Behavior**: Each partition continues with available validators
- **Recovery**: Sync to longest valid chain upon reconnection
- **Security**: Deterministic consensus prevents conflicting histories

### 12.2 Time-Distributed Security Model

TIMPAL achieves a breakthrough in blockchain security through **time-distributed security**—a novel defense mechanism that makes 51% attacks mathematically impossible for the first ~19 years (at 3-second block time target) and economically impossible thereafter. The security window scales with actual block time.

**The Fundamental Problem:**

Traditional blockchains defend against 51% attacks by requiring attackers to control 51% of:
- **PoW chains**: Hash power (requires expensive hardware)
- **PoS chains**: Circulating supply (can be acquired through purchase)

Both approaches can be defeated if an attacker has sufficient capital.

**TIMPAL's Solution:**

51% attacks require ownership of **51% of maximum supply** (127.5M TMPL), not circulating supply:

```
Attack threshold = 127,500,000 TMPL (fixed, never changes)
Year 1 supply = 6,674,432 TMPL (5% of attack threshold)
Year 10 supply = 66,744,320 TMPL (52% of attack threshold)
Year 19 supply = 126,813,824 TMPL (99.5% of attack threshold)
Year 20+ supply = 127M+ TMPL (attack threshold reached)
```

**Why This Works:**

*Years assume 3-second block time target. Actual calendar years = listed year × (actual_block_time / 3)*

**Phase 1 (Years 1-19 at 3s blocks): Mathematically Impossible**
- Not enough TMPL exists to perform attack
- Even controlling 100% of circulating supply is insufficient
- Time itself is the security mechanism

**Phase 2 (Year 19+ at 3s blocks): Economically Impossible**
- Attack requires 127.5M TMPL
- Coins distributed across 100,000+ validators globally
- Acquiring 51% requires:
  - Buying from >51,000 independent validators
  - Coordinating largest crypto acquisition in history
  - Paying premium above market price
  - Each validator only owns ~1,250 TMPL average
- Attack cost > value of network

**Implementation Details:**

```python
# When deep chain reorganization detected (4+ blocks):
1. Identify validators on attacking chain
2. Sum total TMPL balance across all attacking validators
3. If total < 127,500,000 TMPL:
   → Reject reorganization (attack blocked)
4. If total >= 127,500,000 TMPL:
   → Allow reorganization (legitimate consensus)
```

**Detection Threshold:**
- Shallow reorgs (1-3 blocks): Normal network behavior, allowed
- Deep reorgs (4+ blocks): Suspicious, triggers coin balance verification
- 4-block threshold = 12 seconds at 3-second block time
- Balances rapid detection with minimal false positives

**Attack Timeline:**

| Year | Circulating Supply | Attack Possible? | Reason |
|------|-------------------|------------------|--------|
| 1-18 | < 127.5M TMPL | ❌ NO | Insufficient supply exists |
| 19+ | ≥ 127.5M TMPL | ⚠️ THEORETICAL | Economically impossible (too distributed) |

**Key Insight:** Time itself acts as the security mechanism. The attack window opens only after 19 years, by which point the network is so decentralized that acquiring 51% is economically impossible.

### 12.3 Economic Attacks

**Fee Manipulation:**
- **Attack**: Proposer includes only high-fee transactions
- **Impact**: Minimal (fees distributed equally anyway)
- **Mitigation**: Next proposer includes remaining transactions

**Validator Spam:**
- **Attack**: Create many validators to dilute rewards
- **Prevention**: Device fingerprinting limits to physical devices
- **Cost**: Prohibitively expensive at scale

### 12.3 Network Security

**Assumptions:**
- Majority of validators are honest
- P2P network remains connected
- Device fingerprinting cannot be easily spoofed
- Cryptographic primitives remain secure

---

## 13. Future Developments

### 13.1 Potential Enhancements

**Layer 2 Solutions:**
- Payment channels
- State channels
- Rollups

**Cross-Chain:**
- Bridges to other blockchains
- Atomic swaps
- Interoperability protocols

**Privacy:**
- Confidential transactions
- Zero-knowledge proofs
- Stealth addresses

**Smart Contracts:**
- Turing-complete VM (optional)
- Resource metering
- Formal verification

### 13.2 Research Areas

- Alternative device fingerprinting methods
- Quantum-resistant cryptography
- Sharding for increased throughput
- Optimistic execution

---

## 14. Conclusion

TIMPAL represents a new approach to blockchain consensus: one that prioritizes equality, accessibility, and true decentralization over computational power or capital requirements. By enforcing one validator per device and distributing rewards equally, we create a system where participation is truly permissionless and rewards are truly fair.

The VRF-based validator selection provides immediate finality without energy-intensive mining or capital-intensive staking. Device fingerprinting prevents Sybil attacks while maintaining open participation. The result is a blockchain that anyone can validate, where everyone earns equally, and where decentralization is enforced by design rather than hoped for by incentives.

Unlike systems that concentrate power among early adopters, large stakeholders, or specialized hardware operators, TIMPAL treats all validators equally. This is not just a technical design choice—it's a philosophical commitment to egalitarian principles in blockchain infrastructure.

The network launches with no pre-mine, no ICO, and no central authority. It is released as open-source software for anyone to run. The protocol is fixed in code, resistant to governance capture, and designed to remain fair indefinitely.

**Welcome to truly equal blockchain economics.**

---

## References

1. Nakamoto, S. (2008). "Bitcoin: A Peer-to-Peer Electronic Cash System"
2. Buterin, V. (2014). "Ethereum: A Next-Generation Smart Contract and Decentralized Application Platform"
3. Castro, M., Liskov, B. (1999). "Practical Byzantine Fault Tolerance"
4. Douceur, J. (2002). "The Sybil Attack"
5. BIP39. "Mnemonic code for generating deterministic keys"

---

## Appendix A: Technical Specifications

### Block Structure
```python
{
    "height": int,
    "previous_hash": str,
    "timestamp": int,
    "transactions": [Transaction],
    "validator": str,
    "signature": str,
    "block_hash": str,
    "merkle_root": str
}
```

### Address Format
```
tmpl + 44 characters (base58 encoded public key hash)
Example: tmpl1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t1
```

### Network Protocol
- **Transport**: WebSocket (ws:// or wss://)
- **Default Port**: 8765
- **Message Format**: JSON
- **Compression**: Optional gzip

---

## Appendix B: Emission Schedule Details

```python
blocks_per_year = (365.25 * 24 * 60 * 60) / 3  # ~10,519,200
emission_per_year = blocks_per_year * 0.6345  # ~6,674,432 TMPL
total_years = 250_000_000 / emission_per_year  # ~37.5 years

Block 0:              Genesis (special case)
Blocks 1-394.2M:      0.6345 TMPL reward
Block 394,200,001+:   0 TMPL reward (fees only)
```

---

**Version:** 1.0.0  
**Date:** 2025  
**License:** MIT  
**Repository:** https://github.com/EvokiTimpal/timpal-testnet  
**Support:** GitHub Issues for technical discussion

*This whitepaper describes the TIMPAL blockchain protocol as implemented in the reference client. The protocol is open source and permissionless—anyone may implement compatible software.*
