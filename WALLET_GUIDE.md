# TIMPAL Wallet Guide

## 🔐 Creating & Managing Your Wallet

Your wallet is your gateway to the TIMPAL blockchain. It contains your private keys and allows you to send/receive TMPL tokens.

---

## 📥 Installation

No installation needed! The wallet is included with the TIMPAL node.

---

## 🚀 Quick Start

### Option 1: Using the Interactive CLI (Recommended)

```bash
python3 wallet_cli.py
```

This launches an interactive menu where you can:
- Create a new wallet
- Restore from recovery phrase
- View wallet information

### Option 2: Command Line (Direct)

The wallet CLI will guide you through the process with a simple menu.

---

## 🆕 Creating a New Wallet

1. **Run the wallet CLI:**
   ```bash
   python3 wallet_cli.py
   ```

2. **Choose option 1** (Create new wallet)

3. **Set a 6-digit PIN:**
   - Must be at least 6 digits
   - Used to encrypt your wallet file
   - **DON'T FORGET THIS PIN!**

4. **Save your recovery phrase:**
   - You'll receive 12 words
   - **WRITE THEM DOWN ON PAPER**
   - Store in a safe place
   - **NEVER share with anyone**
   - This is the ONLY way to recover your wallet

5. **Your wallet address:**
   - Starts with `tmpl`
   - 48 characters long
   - Share this to receive TMPL tokens

---

## 🔄 Restoring a Wallet

If you already have a recovery phrase:

1. **Run the wallet CLI:**
   ```bash
   python3 wallet_cli.py
   ```

2. **Choose option 2** (Restore wallet)

3. **Enter your 12-word recovery phrase:**
   - Type all 12 words separated by spaces
   - Must be exact (including spelling)

4. **Set a new PIN:**
   - Can be different from your original PIN
   - Used to encrypt the restored wallet

---

## 👁️ Viewing Wallet Information

If you already have a wallet:

1. **Run the wallet CLI:**
   ```bash
   python3 wallet_cli.py
   ```

2. **Choose option 1** (View wallet info)

3. **Enter your PIN**

You'll see:
- Your TIMPAL address
- Your public key

---

## 🔑 Wallet Files

### wallet.json
- Stores your encrypted private keys
- Protected by your PIN
- **BACKUP THIS FILE** (but it's useless without your PIN or recovery phrase)

### Recovery Phrase
- 12 words that can restore your wallet
- **MORE IMPORTANT than wallet.json**
- Works even if you lose your PIN
- **Keep it secret, keep it safe**

---

## ⚠️ Security Best Practices

### ✅ DO:
- Write recovery phrase on paper (not digital)
- Store recovery phrase in a safe place
- Use a strong PIN (not 123456!)
- Backup wallet.json regularly
- Keep your wallet files secure

### ❌ DON'T:
- Share your recovery phrase with anyone
- Store recovery phrase on your computer
- Screenshot your recovery phrase
- Email your recovery phrase
- Use the same PIN for everything
- Share your wallet.json file

---

## 🆘 Troubleshooting

### "No wallet found"
- You need to create a wallet first
- Run `python3 wallet_cli.py` and choose option 1

### "Invalid PIN"
- Your PIN is incorrect
- Try again or restore from recovery phrase

### "Invalid recovery phrase"
- Check spelling of all 12 words
- Ensure words are separated by single spaces
- Recovery phrases are case-sensitive

### "Wallet already exists"
- A wallet.json file exists
- Choose to view, create new (overwrites), or restore (overwrites)

---

## 💡 Tips

1. **Test first:** Create a test wallet to understand the process
2. **Multiple backups:** Store recovery phrase in 2+ safe locations
3. **PIN security:** Don't use obvious PINs (birthdays, 123456, etc.)
4. **Paper backup:** Digital backups can be hacked; paper cannot
5. **Verify address:** Double-check when sharing your address

---

## 🔐 How Encryption Works

1. **Recovery phrase** → Generates private key
2. **Private key** → Encrypted with PIN
3. **Encrypted key** → Stored in wallet.json
4. **PIN** → Unlocks wallet.json

**Security layers:**
- Recovery phrase: Master backup
- PIN: Daily access protection
- Encryption: File protection

---

## 📊 Example Session

```
$ python3 wallet_cli.py

============================================================
          TIMPAL WALLET GENERATOR
============================================================
Create a secure wallet for the TIMPAL blockchain
============================================================

What would you like to do?
  [1] Create new wallet
  [2] Restore wallet from recovery phrase
  [3] Exit

Enter your choice (1-3): 1

🔐 CREATE NEW WALLET

Enter a 6-digit PIN (for wallet encryption): 234567
Confirm PIN: 234567

============================================================
✅ WALLET CREATED SUCCESSFULLY!
============================================================

📝 RECOVERY PHRASE (12 words):
------------------------------------------------------------
  example phrase words here never share this phrase online
------------------------------------------------------------

⚠️  CRITICAL SECURITY WARNINGS:
   1. WRITE DOWN your recovery phrase on paper
   2. NEVER share it with anyone
   3. Store it in a SAFE place
   4. If you lose it, your funds are GONE FOREVER
   5. Anyone with this phrase can steal your funds

📍 Your TIMPAL Address:
   tmpl96301bb781cca04a211024afc8cc4c92bd7660be1e36

💾 Wallet saved to: wallet.json

============================================================
```

---

## 🎯 Next Steps

After creating your wallet:

1. **Save your recovery phrase** (on paper!)
2. **Start your validator node** to earn rewards
3. **Share your address** to receive TMPL
4. **Keep your wallet secure**

See [QUICKSTART.md](QUICKSTART.md) for running a validator node.

---

**Remember: Your recovery phrase is like cash. If someone gets it, they can steal your funds. Keep it safe!** 🔐
