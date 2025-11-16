# TIMPAL Genesis - 100% Cross-Platform Docker Image
# Works identically on Linux (x86/ARM), macOS, Windows
# NO system dependencies, NO C++ compilation required

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app

# Install Python dependencies
# TIMPAL Genesis uses pure-Python storage - no system dependencies needed!
RUN pip install --no-cache-dir -r requirements.txt

# Environment variables (override with docker run -e)
ENV TIMPAL_WALLET_PIN=""
ENV GENESIS_SEED_CORRECT=""

# Expose ports
# 9000: P2P networking
# 9001: HTTP API
EXPOSE 9000 9001

# Run testnet node
CMD ["python3", "run_testnet_node.py", "--port", "9000"]
