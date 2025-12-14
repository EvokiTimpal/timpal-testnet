# TIMPAL — Consensus & System Invariants (v1)

This document is the authoritative specification of the TIMPAL protocol.
If code behavior contradicts this document, the code is incorrect by definition.

This file supersedes all implicit behavior, comments, or legacy assumptions.

---

## 0. Definitions

- Validator: A registered identity allowed to participate in consensus when eligible.
- Node: A running instance of the TIMPAL software.
- Canonical Chain: The single chain all honest nodes must converge on.
- Genesis Hash: The unique identifier of the network.

---

## 1. Genesis Authority

### 1.1 Canonical Genesis
- The network is defined by exactly one CANONICAL_GENESIS_HASH.
- Any peer whose block 0 hash differs MUST be rejected immediately.

### 1.2 Genesis Finality
- Genesis is created once.
- A different genesis hash means a different network.

---

## 2. Validator State Model

### 2.1 REGISTERED
A validator exists in the on-chain registry.

REGISTERED does NOT imply online or eligibility.

### 2.2 STAKED
Validator satisfies staking requirements (if any).

### 2.3 ONLINE
Validator has produced a valid liveness proof within T_online.

ONLINE expires automatically.

### 2.4 SYNCED
Node has validated the canonical tip:
- height matches
- hash matches

### 2.5 HEALTHY
Node is not syncing, cooling, or recovering.

---

## 3. Eligibility & Proposing

ELIGIBLE_SET(h) is the single source of truth.

A validator is eligible iff:
- REGISTERED
- STAKED (if applicable)
- ONLINE
- Node is SYNCED
- Node is HEALTHY

ACTIVE is not a valid eligibility state.

---

## 4. Proposer Rules

Exactly one proposer per height.
Only eligible validators may propose.
If not eligible → do not propose.

---

## 5. Sync & Write Gate

Unsynced or unhealthy nodes must never propose.
Valid inbound blocks must still be accepted.

---

## 6. Rewards

Rewards (including fees) go only to ELIGIBLE_SET(h).

---

## 7. No Dual Truths

Deprecated logic must not affect consensus.

---

## 8. Explorer Isolation

Explorer is read-only and must never affect consensus.

---

## 9. Locked Consensus Constants (v1)

- T_online = 10 seconds
- L_sync = 0 blocks
- MICRO_SYNC_ENABLED = true

---

## 10. Attestation-Based Finality

### 10.1 Definitions

- CONFIRMED: Transaction included in a block at height H (1-block confirmation).
- FINAL: Block at height H has >= QUORUM attestations from eligible validators AND is at least FINALITY_DEPTH blocks behind head.
- finalized_height: The highest block height that is FINAL. Monotonically increasing.

### 10.2 Finality Parameters (Immutable)

- FINALITY_QUORUM = max(2, floor(eligible_validators / 3))
- FINALITY_DEPTH = 1 (finalize block H when head >= H+1)
- Attestation message: hash("TIMPAL_ATTEST_V1" || chain_id || height || block_hash)

### 10.3 Hard Invariants

**F1: NO REORG at heights <= finalized_height**
- This is UNCONDITIONAL - no network recovery exceptions allowed.
- Blocks at or below finalized_height are IMMUTABLE.
- Any competing chain that conflicts at height <= finalized_height MUST be rejected.

**F2: finalized_height is monotonic**
- finalized_height can only increase, never decrease.
- Once a block is FINAL, it remains FINAL forever.

**F3: FINAL requires quorum attestations**
- A block becomes FINAL only when it has >= FINALITY_QUORUM attestations from eligible validators.
- Attestations are counted once per validator (no duplicates).
- Only eligible validators' attestations are counted.

### 10.4 Attestation Rules

- Validators sign attestations for blocks they observe as canonical.
- Attestations propagate via P2P message type "finality_attestation".
- Invalid signatures are rejected.
- Attestations from non-eligible validators are ignored.

---

Final Law:
A validator that peers would reject must reject itself first.
