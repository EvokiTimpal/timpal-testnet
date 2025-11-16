# 🐋 TIMPAL Testnet - Docker Quick Start

Run TIMPAL testnet in Docker with one command!

---

## 🚀 Quick Start

### 1. Build the image

```bash
docker build -t timpal-testnet .
```

### 2. Run a single node

```bash
docker run -p 9000:9000 -p 9001:9001 \
  -e TIMPAL_WALLET_PIN=my_secure_pin \
  timpal-testnet
```

**That's it!** Your testnet node is running.

---

## 🌐 Run a 3-Node Network

### Node 1 (Bootstrap)

```bash
docker run -d \
  --name timpal-node1 \
  --network timpal-net \
  -p 9000:9000 -p 9001:9001 \
  -e TIMPAL_WALLET_PIN=node1_pin \
  timpal-testnet
```

### Node 2

```bash
docker run -d \
  --name timpal-node2 \
  --network timpal-net \
  -p 3000:9000 -p 3001:9001 \
  -e TIMPAL_WALLET_PIN=node2_pin \
  timpal-testnet \
  python3 run_testnet_node.py --port 9000 --seed ws://timpal-node1:9000
```

### Node 3

```bash
docker run -d \
  --name timpal-node3 \
  --network timpal-net \
  -p 3002:9000 -p 3003:9001 \
  -e TIMPAL_WALLET_PIN=node3_pin \
  timpal-testnet \
  python3 run_testnet_node.py --port 9000 --seed ws://timpal-node1:9000
```

---

## 📊 Check Node Status

```bash
# Check logs
docker logs timpal-node1

# Check health
curl http://localhost:9001/api/health

# Check blocks
curl http://localhost:9001/api/blocks/range?start=0&end=10
```

---

## 🛑 Stop Nodes

```bash
docker stop timpal-node1 timpal-node2 timpal-node3
docker rm timpal-node1 timpal-node2 timpal-node3
```

---

## 💾 Persist Data (Optional)

To keep blockchain data after container restart:

```bash
docker run -d \
  --name timpal-node1 \
  -p 9000:9000 -p 9001:9001 \
  -v timpal-data:/app/testnet_data_9000 \
  -e TIMPAL_WALLET_PIN=my_pin \
  timpal-testnet
```

---

## 🔧 Advanced Usage

### Custom port

```bash
docker run -p 8000:9000 -p 8001:9001 \
  timpal-testnet \
  python3 run_testnet_node.py --port 9000
```

### Access shell

```bash
docker run -it timpal-testnet /bin/bash
```

### View wallet

```bash
docker exec timpal-node1 cat /app/testnet_data_9000/validator_wallet.json
```

---

## ✅ Cross-Platform Support

This Docker image works on:
- ✅ **macOS** (Intel & Apple Silicon)
- ✅ **Linux** (x86_64 & ARM64)
- ✅ **Windows** (with Docker Desktop)

TIMPAL uses **pure-Python JSON storage** - works identically on all platforms with no dependencies!

---

## 🎉 You're Ready!

Your TIMPAL testnet is now running in Docker!

For native macOS installation, see [MACOS_README.md](MACOS_README.md)
