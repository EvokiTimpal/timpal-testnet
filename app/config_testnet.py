"""
TIMPAL TESTNET CONFIGURATION

This is a SEPARATE network from mainnet for testing purposes.
- Testnet coins have NO VALUE
- Network can be reset at any time
- Used to test real-world conditions before mainnet launch
- NO VALIDATOR LIMITS - test unlimited scalability
- DYNAMIC SETTINGS - adjust parameters for different test scenarios
"""

# Network identity (completely separate from mainnet)
CHAIN_ID = "timpal-testnet"
SYMBOL = "tTMPL"  # Testnet symbol (mainnet uses TMPL)
DECIMALS = 8
PALS_PER_TMPL = 100_000_000
MAX_SUPPLY_TMPL = 250_000_000
MAX_SUPPLY_PALS = 250_000_000 * (10 ** 8)

# Block time (seconds)
#
# 3s blocks are fine, but ONLY if genesis + proposer selection are deterministic
# across nodes. This testnet config therefore uses a FIXED genesis timestamp and
# a CANONICAL genesis hash so all nodes share one network history.
BLOCK_TIME = 3

# Emission schedule (same as mainnet for realistic testing)
EMISSION_PER_BLOCK_TMPL = 0.6345
EMISSION_PER_BLOCK_PALS = 63_450_000
PHASE1_BLOCKS = 394_200_000

# Fixed fee
FEE = 50_000  # 0.0005 TMPL (50,000 pals = 0.0005 × 100,000,000)

# Genesis timestamp (FIXED for deterministic multi-node testnet)
#
# IMPORTANT: This must be identical on every node. A dynamic timestamp causes
# each node to create a different genesis block hash → permanent forks.
#
# 2026-01-04 00:00:00 UTC
GENESIS_TIMESTAMP = 1767484800

# Canonical genesis block hash (SECURITY: prevents eclipse attacks)
# Set to None for fresh testnet - the first genesis block will be accepted
# After genesis is created, this can be updated to lock the genesis block
# Generated with v2 wallet (BIP-39) - FIXED ADDRESS FORMAT (44 hex chars)
# Seed phrase: "occur twice shock opinion detail round ridge tape modify stay bargain suffer"
# Address: tmpl7a255cb7912eed25bac00c5a2e6b5604518d2b0b2c8e
# Canonical genesis block hash (SECURITY + network coherence)
#
# Locking this prevents nodes from silently booting a different genesis and
# forking into a separate network.
#
# This hash is deterministically derived from:
# - GENESIS_TIMESTAMP (above)
# - GENESIS_VALIDATORS (below)
CANONICAL_GENESIS_HASH = "107d8bd3a5cc233a68550f89a2936223482825588a467d69147fc47a8885cab0"

# EPOCH-BASED CONSENSUS (for 100,000+ validator scalability)
# TESTNET ADJUSTMENT: Shorter epochs (10 blocks = 30s) for faster testing
# Mainnet uses 100 blocks = 5 minutes, but testnet needs faster committee rotation
# for rapid validator onboarding and testing. Consensus logic remains identical.
EPOCH_LENGTH = 10  # 10 blocks per epoch (30 seconds at 3s/block) - testnet only
ATTESTATION_WINDOW = 10  # Full epoch to submit attestation (30 seconds) - testnet only
ATTESTATION_COMMITTEE_SIZE = 1000  # Rotating committee: only 1000 validators attest per epoch
MIN_COMMITTEE_PARTICIPATION = 0.67  # 67% of committee (670 validators) must attest
PROPOSER_CACHE_SIZE = 200  # Cache proposer schedule for 200 blocks ahead (20 epochs in testnet)
EPOCH_HISTORY_RETENTION = 10  # Keep only 10 epochs of history (~5 minutes in testnet, reduces memory)

# DYNAMIC TESTING: Grace period can be adjusted
# Options: 100 blocks (5 min), 1000 blocks (50 min), 100_000 blocks (3.5 days), 550_000 blocks (~19 days)
# For testnet with NO-VALUE coins, shorter grace period enables faster deposit transition testing
# This allows validators to test the deposit system without waiting 6 months
DEPOSIT_GRACE_PERIOD_BLOCKS = 550_000  # ~19 days (550,000 blocks × 3s = 1,650,000s ≈ 19 days)

# NO VALIDATOR LIMITS - test unlimited scalability
MAX_VALIDATORS = None  # None = unlimited (can support 100,000+ validators)

# Transaction limits (same as mainnet)
MAX_TRANSACTION_AMOUNT = MAX_SUPPLY_PALS
MAX_TRANSACTIONS_PER_BLOCK = 1350
MAX_BLOCK_SIZE_BYTES = 900_000
MAX_FUTURE_TIMESTAMP_DRIFT = 300

GENESIS_VALIDATORS = {
    # Genesis validator from wallet_v2.json (BIP-39 testnet bootstrap node)
    # FIXED ADDRESS FORMAT: 44 hex chars after "tmpl" prefix (matches Transaction._public_key_to_address)
    # Seed phrase: "occur twice shock opinion detail round ridge tape modify stay bargain suffer"
    "tmpl7a255cb7912eed25bac00c5a2e6b5604518d2b0b2c8e":
    "f65f0b2af11bd445c5f3e2c3d912138569a36a0e4f4a49dc000a0ef5355cc6a6b5bbe3248d49650e3c7b1de452840a6ea3631a87d69f0d09740a8033d90b06fd"
}

DEFAULT_P2P_PORT = 8765
SEED_NODES = []  # No hardcoded seeds - users specify with --seed flag

# HTTP API endpoints for block sync (HTTP port = P2P port + 1)
# These are used for reliable initial sync before joining P2P network
HTTP_SEEDS = []  # No hardcoded seeds - users specify with --seed flag

MAX_REORG_DEPTH = 80
FINALITY_CHECKPOINT_INTERVAL = 100
ATTACK_PREVENTION_THRESHOLD = MAX_SUPPLY_PALS // 2
ATTACK_REORG_THRESHOLD = 4

# Validator liveness detection
# Only validators within this many blocks of chain head receive rewards
# 3 blocks = ~9 seconds tolerance (fair to online validators)
VALIDATOR_SYNC_TOLERANCE_BLOCKS = 3

# Validator economics (same as mainnet for realistic testing)
VALIDATOR_DEPOSIT_PALS = 100 * PALS_PER_TMPL
MIN_DEPOSIT_PALS = 50 * PALS_PER_TMPL
SLASH_DOUBLE_SIGNING = 100
SLASH_INVALID_BLOCK = 50
WITHDRAWAL_DELAY_BLOCKS = 100

# TESTNET FLAGS
IS_TESTNET = True
TESTNET_NO_VALUE_COINS = True  # Coins have NO real-world value

# DYNAMIC TESTING CONFIGURATION
# Adjust these to test different scenarios
TESTING_CONFIG = {
    # Fast deposit transition testing
    "fast_transition_mode": True,  # Use short grace period
    
    # Scalability testing
    "no_validator_limits": True,  # Support unlimited validators
    "test_100k_validators": True,  # Simulate large-scale network
    
    # Feature testing
    "test_all_tx_types": True,  # Test transfers, deposits, withdrawals
    "test_pool_consensus": True,  # Test round-robin selection
    "test_deposit_system": True,  # Test deposit transitions
    
    # Network testing
    "test_p2p_sync": True,  # Test peer-to-peer synchronization
    "test_chain_reorganization": True,  # Test reorg handling
    "test_finality": True,  # Test finality checkpoints
    
    # Logging
    "verbose_logging": True,  # Detailed logs for debugging
    "log_all_transactions": True,  # Track all tx activity
}
