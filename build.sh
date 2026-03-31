#!/usr/bin/env bash
# Render build script — ensures Node.js is available for yt-dlp signature solving
set -o errexit

echo "=== Riffd Build ==="

# Check if Node.js is available
if command -v node &> /dev/null; then
    echo "[build] Node.js already available: $(node --version)"
else
    echo "[build] Node.js not found — installing via apt"
    apt-get update -qq && apt-get install -y -qq nodejs npm 2>/dev/null || {
        echo "[build] apt install failed — trying curl install"
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null
        apt-get install -y -qq nodejs 2>/dev/null || echo "[build] WARNING: could not install Node.js"
    }
    if command -v node &> /dev/null; then
        echo "[build] Node.js installed: $(node --version)"
    else
        echo "[build] WARNING: Node.js still not available — yt-dlp signature solving may fail"
    fi
fi

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright Chromium for cookie refresh
echo "[build] Installing Playwright Chromium browser..."
playwright install chromium --with-deps 2>/dev/null || {
    echo "[build] WARNING: Playwright browser install failed — cookie refresh will be unavailable"
    echo "[build] This is non-critical; yt-dlp will fall back to Cobalt/Piped APIs"
}

echo "=== Build complete ==="
