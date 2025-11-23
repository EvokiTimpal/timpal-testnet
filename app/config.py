"""
TIMPAL MAINNET CONFIGURATION

This is the PRODUCTION network - identical code to testnet, different config.
"""

import time

CHAIN_ID = "timpal-mainnet"
SYMBOL = "TMPL"
DECIMALS = 8
PALS_PER_TMPL = 100_000_000
MAX_SUPPLY_TMPL = 250_000_000
MAX_SUPPLY_PALS = 250_000_000 * (10 ** 8)
BLOCK_TIME = 3
EMISSION_PER_BLOCK_TMPL = 0.6345
EMISSION_PER_BLOCK_PALS = 63_450_000
PHASE1_BLOCKS = 394_200_000
FEE = 50_000  # 0.0005 TMPL (50,000 pals = 0.0005 × 100,000,000)
GENESIS_TIMESTAMP = int(time.time())  # Dynamic timestamp for mainnet launch
FINALITY_CHECKPOINT_INTERVAL = 100  # Finality checkpoints every 100 blocks

# EPOCH-BASED CONSENSUS (for 100,000+ validator scalability)
EPOCH_LENGTH = 100  # 100 blocks per epoch (5 minutes at 3s/block)
ATTESTATION_WINDOW = 100  # Full epoch to submit attestation (reduces congestion)
ATTESTATION_COMMITTEE_SIZE = 1000  # Only 1000 validators attest per epoch (rotated)
MIN_COMMITTEE_PARTICIPATION = 0.67  # 67% of committee must attest
PROPOSER_CACHE_SIZE = 200  # Cache proposer schedule for 200 blocks ahead
EPOCH_HISTORY_RETENTION = 10  # Keep only 10 epochs of history (50 minutes)

# Validator deposit grace period (3-6 months for network bootstrap)
# During this period, NO deposit required to allow network growth
# After grace period, 100 TMPL deposit required (Sybil defense)
DEPOSIT_GRACE_PERIOD_BLOCKS = 5_000_000  # ~6 months (5,184,000 blocks)

MAX_TRANSACTION_AMOUNT = MAX_SUPPLY_PALS
MAX_TRANSACTIONS_PER_BLOCK = 1350
MAX_BLOCK_SIZE_BYTES = 900_000
MAX_FUTURE_TIMESTAMP_DRIFT = 300

GENESIS_VALIDATORS = {
    "tmpl6065afd538da959a3600d5cf9f0b8b1c74c2e8e5193b": "830deb118bde152ef6dedd48facca2469524379b79d03db40a154c8c0b1932b1b8543ae4efcaf46ca1f2ff0fd6c1bf1624b4fe40ac163c7f04d0618d386c57d8"
}

SEED_NODES = []  # No seed nodes yet - will be populated when mainnet launches

MAX_REORG_DEPTH = 80
ATTACK_PREVENTION_THRESHOLD = MAX_SUPPLY_PALS // 2
ATTACK_REORG_THRESHOLD = 4

# Validator liveness detection
# Only validators within this many blocks of chain head receive rewards
# 3 blocks = ~9 seconds tolerance (fair to online validators)
VALIDATOR_SYNC_TOLERANCE_BLOCKS = 3

# Validator economics
VALIDATOR_DEPOSIT_PALS = 100 * PALS_PER_TMPL
MIN_DEPOSIT_PALS = 50 * PALS_PER_TMPL
SLASH_DOUBLE_SIGNING = 100
SLASH_INVALID_BLOCK = 50
WITHDRAWAL_DELAY_BLOCKS = 100
