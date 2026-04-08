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
# Run with full output so failures are visible in Render build logs
playwright install chromium --with-deps || {
    echo "[build] WARNING: playwright install chromium --with-deps failed (exit $?)"
    echo "[build] Trying without --with-deps (system deps may already be present)..."
    playwright install chromium || echo "[build] WARNING: Playwright browser install failed — cookie refresh will be unavailable"
}
# Verify the browser executable actually exists after install
PLAYWRIGHT_CACHE=$(python3 -c "import playwright; import os; print(os.path.join(os.path.dirname(playwright.__file__), '..', '..', '..', '.cache', 'ms-playwright'))" 2>/dev/null || echo "/opt/render/.cache/ms-playwright")
if find "${PLAYWRIGHT_CACHE}" -name "chrome-headless-shell" -type f 2>/dev/null | grep -q .; then
    echo "[build] ✅ Playwright headless shell found"
else
    echo "[build] ⚠️  Playwright headless shell not found in ${PLAYWRIGHT_CACHE} — cookie refresh will fall back gracefully"
fi

echo "=== Build complete ==="
