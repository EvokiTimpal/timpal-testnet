# TIMPAL Wallet Security Guide

## ⚠️ Critical Information - Read Carefully

Your TIMPAL wallet contains the cryptographic keys that control your funds. **If you lose access to your wallet, your funds are PERMANENTLY LOST**. There is no "forgot password" option, no customer support that can recover your funds, and no way to reverse this.

---

## 🔑 Wallet Version 2 (BIP-39) - Recommended

TIMPAL now supports **BIP-39** wallets with industry-standard seed phrase backup. This is the **production-ready** wallet format.

### What is a Seed Phrase?

A seed phrase (also called "recovery phrase" or "mnemonic") is a list of **12 or 24 words** that can regenerate your entire wallet. 

Example (DO NOT USE THIS):
```
apple brave cotton duck eagle forest garage happy island jacket
```

### Creating a New Wallet

```bash
# Create new BIP-39 wallet
python wallet_cli_v2.py

# Follow prompts:
# 1. Choose 12 or 24 words
# 2. Set a strong password (min 8 characters)
# 3. Optional: Add passphrase (25th word) for extra security
# 4. WRITE DOWN your seed phrase!
```

### Seed Phrase Backup - THE MOST IMPORTANT STEP

#### ✅ DO:
1. **Write seed phrase on paper** - Use pen and paper, write clearly
2. **Verify accuracy** - Double-check every word, spelling matters
3. **Store in safe place** - Fireproof safe, safety deposit box, or secure location
4. **Make multiple copies** - Store in 2-3 different secure locations
5. **Keep offline** - Never store digitally
6. **Test recovery** - After creation, restore wallet from seed phrase to verify it works
7. **Protect from damage** - Use lamination or metal backup plates

#### ❌ DON'T:
1. ❌ Take photos of seed phrase
2. ❌ Save in cloud storage (Google Drive, iCloud, Dropbox, etc.)
3. ❌ Send via email or messaging apps
4. ❌ Store in password managers (they can be hacked)
5. ❌ Share with anyone (not even TIMPAL team)
6. ❌ Write partial seed phrases hoping to remember the rest
7. ❌ Store on devices (computers, phones, USB drives)

### Passphrase (Optional 25th Word)

The BIP-39 standard supports an optional passphrase that acts as a "25th word."

**Benefits:**
- Extra layer of security
- Plausible deniability (different passphrase = different wallet)
- Protection if seed phrase is discovered

**Risks:**
- If forgotten, funds are PERMANENTLY LOST
- Must be remembered or backed up separately from seed phrase
- No way to recover if lost

**Recommendation:** Only use if you're confident in your ability to remember it permanently.

---

## 🔒 Password Security

Your wallet file is encrypted with your password. Choose a strong password:

**Requirements:**
- Minimum 8 characters
- Mix of uppercase, lowercase, numbers, symbols
- NOT a dictionary word
- NOT reused from other accounts
- NOT shared with anyone

**Good password examples:**
- `Tr0pical$Mang0-2024!`
- `c0RRect_h0rse_BATTERY_staple`
- `MyD0g!sName=Fluffy&Age=7`

**Bad password examples:**
- `password` (too weak)
- `12345678` (too weak)
- `tmpl2024` (too weak)

---

## 📁 Wallet Files

TIMPAL uses two wallet formats:

### wallet_v2.json (Recommended - BIP-39)
- **Version:** 2
- **Standard:** BIP-39/BIP-32/BIP-44
- **Seed phrase:** 12 or 24 words
- **Encryption:** Argon2id (military-grade)
- **Recovery:** Can be restored from seed phrase
- **Derivation:** Deterministic key generation

### wallet.json (Legacy - Deprecated)
- **Version:** 1
- **Standard:** Custom implementation
- **Seed phrase:** Pseudo-BIP-39 (non-standard)
- **Encryption:** PBKDF2 with PIN
- **Recovery:** Limited (seed phrase not fully BIP-39 compliant)
- **Migration:** Use `migrate_wallet.py` to upgrade to v2

---

## 🔄 Migrating from Legacy to v2

If you have a legacy `wallet.json`, migrate to `wallet_v2.json`:

```bash
# Dry run (test without making changes)
python migrate_wallet.py wallet.json --dry-run

# Actual migration (creates backup automatically)
python migrate_wallet.py wallet.json
```

**Important:**
- Migration creates a **new wallet** with a **different address**
- Your old wallet backup will be saved automatically
- You'll get a new BIP-39 seed phrase - **WRITE IT DOWN!**
- For validators: Keep old wallet until you transfer funds to new address

---

## 🚀 For Validators

### Genesis Validators (Mainnet)

**CRITICAL:** Your genesis validator wallet controls the initial coin supply.

1. **Generate wallet on secure, offline computer**
2. **Create multiple seed phrase backups:**
   - Paper copy in fireproof safe
   - Paper copy in safety deposit box
   - Metal backup plate in secure location
3. **Use passphrase** for additional security
4. **Test recovery** before funding
5. **Never expose private key**
6. **Document succession plan** (in case of emergency)

### Testnet Validators

- Same security practices as mainnet
- Useful for testing backup/recovery procedures
- Test wallet restoration before using mainnet

---

## 🛡️ Advanced Security Options

### Hardware Wallets (Future)

TIMPAL will support hardware wallets (Ledger, Trezor) for maximum security. Hardware wallets:
- Store private keys in secure hardware
- Never expose private keys to computer
- Protected against malware
- Require physical confirmation for transactions

**Status:** Planned for future release

### Multi-Signature Wallets (Future)

Multi-sig requires multiple keys to authorize transactions:
- Example: 2-of-3 setup (any 2 of 3 keys can sign)
- Protection against single key compromise
- Ideal for organizational funds

**Status:** Planned for future release

### Cold Storage

For long-term holdings:
1. Generate wallet on air-gapped (never connected to internet) computer
2. Write down seed phrase
3. Transfer TMPL to the address
4. Store seed phrase securely
5. **Never connect that computer to internet**

---

## 🆘 Emergency Procedures

### Lost Wallet File (wallet_v2.json)

✅ **You have seed phrase → You're safe!**

1. Create new wallet using same seed phrase:
   ```bash
   python wallet_cli_v2.py
   # Choose "Restore wallet from seed phrase"
   ```
2. Enter your seed phrase
3. Use same passphrase (if you had one)
4. Wallet restored with same address and funds

---

### Lost Seed Phrase

❌ **No wallet file + No seed phrase = Funds permanently lost**

There is **NO WAY** to recover funds without either:
- The wallet file + password, OR
- The seed phrase (+ passphrase if used)

**This is why backup is critical!**

---

### Compromised Seed Phrase

If someone has your seed phrase:
1. **Immediately** create new wallet
2. Transfer all funds to new address
3. Never use compromised wallet again

---

### Forgotten Password (Wallet File Exists)

❌ **No password + No seed phrase = Funds locked forever**

✅ **Have seed phrase → Restore wallet:**

```bash
python wallet_cli_v2.py
# Restore from seed phrase
# Set NEW password
```

---

## ✅ Security Checklist

Before using your wallet in production:

- [ ] Seed phrase written down on paper
- [ ] Seed phrase verified (test restoration)
- [ ] Multiple copies in different secure locations
- [ ] Password is strong and memorable
- [ ] Wallet file backed up (encrypted)
- [ ] Never shared seed phrase with anyone
- [ ] Tested sending small amount first
- [ ] Understand recovery procedure
- [ ] Documented for successors (if needed)
- [ ] No digital copies of seed phrase exist

---

## 📊 Security Comparison

| Feature | Legacy (v1) | BIP-39 (v2) | Hardware Wallet (Future) |
|---------|-------------|-------------|------------------------|
| **Standard** | Custom | BIP-39/32/44 | Industry Standard |
| **Seed Phrase** | Pseudo-BIP-39 | True BIP-39 | True BIP-39 |
| **Encryption** | PBKDF2 | Argon2id | Hardware-based |
| **Recovery** | Limited | Full | Full |
| **Checksum** | No | Yes | Yes |
| **Compatibility** | TIMPAL only | Universal | Universal |
| **Key Storage** | File | File | Secure Hardware |
| **Security Level** | Medium | High | Maximum |
| **Recommended** | No | **Yes** | When Available |

---

## 🔗 Additional Resources

- **BIP-39 Wordlist:** https://github.com/bitcoin/bips/blob/master/bip-0039/english.txt
- **BIP-44 Standard:** https://github.com/bitcoin/bips/blob/master/bip-0044.mediawiki
- **Argon2 Security:** https://github.com/P-H-C/phc-winner-argon2

---

## ⚖️ Disclaimer

TIMPAL provides cryptographic wallet tools, but **YOU are responsible** for:
- Backing up your seed phrase
- Securing your password
- Protecting your wallet files
- Following security best practices

**We CANNOT recover your funds if you lose your seed phrase or password.**

The blockchain is immutable and decentralized by design - there is no central authority that can reverse transactions or recover lost keys.

---

## 📧 Support

For questions (not fund recovery):
- GitHub Issues: https://github.com/timpal/blockchain
- Documentation: See project README.md

**Remember:** No legitimate support will ever ask for your seed phrase or private keys!
