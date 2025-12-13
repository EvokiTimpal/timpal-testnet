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

Final Law:
A validator that peers would reject must reject itself first.
