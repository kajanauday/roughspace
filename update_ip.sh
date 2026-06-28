#!/bin/bash
# update_ip.sh — Update all IP references and restart services
# Usage: bash update_ip.sh <new-ip>
#        bash update_ip.sh          (auto-detects current external IP)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"
VENV_UVICORN="$SCRIPT_DIR/../.venv/bin/uvicorn"
ENV_FILE="$SCRIPT_DIR/.env"
DASHBOARD_JSON="$SCRIPT_DIR/travel_planner_dashboard.json"
APP_LOG="/tmp/travel_app.log"
PORT=6080

# ── 1. Resolve new IP ─────────────────────────────────────────────────────────
if [ -n "$1" ]; then
    NEW_IP="$1"
else
    echo "No IP provided — detecting current external IP..."
    NEW_IP=$(curl -s -H "Metadata-Flavor: Google" \
        "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" 2>/dev/null)
    if [ -z "$NEW_IP" ]; then
        NEW_IP=$(curl -s ifconfig.me 2>/dev/null)
    fi
fi

if [ -z "$NEW_IP" ]; then
    echo "ERROR: Could not determine IP. Pass it as argument: bash update_ip.sh <new-ip>"
    exit 1
fi

echo ""
echo "========================================"
echo "  New IP: $NEW_IP"
echo "========================================"
echo ""

# ── 2. Detect old IP from dashboard JSON ──────────────────────────────────────
OLD_IP=$(grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' "$DASHBOARD_JSON" | head -1)
if [ -z "$OLD_IP" ]; then
    OLD_IP="NONE"
fi
echo "[1] Old IP found in dashboard: $OLD_IP"

# ── 3. Update dashboard JSON ──────────────────────────────────────────────────
if [ "$OLD_IP" != "$NEW_IP" ] && [ "$OLD_IP" != "NONE" ]; then
    sed -i "s/$OLD_IP/$NEW_IP/g" "$DASHBOARD_JSON"
    echo "    ✓ Updated travel_planner_dashboard.json  ($OLD_IP → $NEW_IP)"
else
    echo "    ✓ Dashboard JSON already up to date"
fi

# ── 4. Stop existing services ─────────────────────────────────────────────────
echo ""
echo "[2] Stopping existing services..."
pkill -f "uvicorn travel_app" 2>/dev/null && echo "    ✓ Stopped uvicorn" || echo "    - uvicorn was not running"
pkill -f "cloudflared" 2>/dev/null && echo "    ✓ Stopped cloudflared" || true
sleep 2

# ── 5. Ensure websockify is on internal port 6081 ─────────────────────────────
if ss -tlnp | grep -q ":6080 "; then
    echo "[3] Port 6080 is in use — freeing it..."
    fuser -k 6080/tcp 2>/dev/null || true
    sleep 1
fi

if ! ss -tlnp | grep -q ":6081 "; then
    echo "[3] Starting websockify (noVNC) on port 6081..."
    /usr/bin/python3 /usr/bin/websockify \
        --web /usr/share/novnc \
        --cert /home/uday_k/novnc.pem \
        6081 localhost:5901 > /tmp/websockify.log 2>&1 &
    echo "    ✓ websockify running on port 6081"
else
    echo "[3] websockify already running on port 6081 ✓"
fi

# ── 6. Start travel app on port 6080 ─────────────────────────────────────────
echo ""
echo "[4] Starting travel app on port $PORT..."
cd "$SCRIPT_DIR"
PYTHONPATH="$SCRIPT_DIR" "$VENV_UVICORN" travel_app:app \
    --host 0.0.0.0 --port "$PORT" > "$APP_LOG" 2>&1 &
APP_PID=$!

# Wait for startup
for i in {1..10}; do
    sleep 1
    if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/" 2>/dev/null | grep -q "200"; then
        echo "    ✓ Travel app running (PID=$APP_PID)"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "    ✗ Travel app failed to start. Check logs: $APP_LOG"
        tail -20 "$APP_LOG"
        exit 1
    fi
done

# ── 7. Verify ports ───────────────────────────────────────────────────────────
echo ""
echo "[5] Port status:"
for port in 5432 6080 6081; do
    if ss -tlnp | grep -q ":$port "; then
        echo "    ✓ Port $port — OPEN"
    else
        echo "    ✗ Port $port — NOT listening"
    fi
done

# ── 8. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  DONE — Update complete"
echo "========================================"
echo ""
echo "  Travel App:  http://$NEW_IP:$PORT"
echo "  App log:     $APP_LOG"
echo ""
echo "  ⚠️  Manual steps in Grafana (takes ~30 seconds):"
echo "  ┌─────────────────────────────────────────────────────────────"
echo "  │ 1. Connections → Data sources → PostgreSQL"
echo "  │    Change Host to:  $NEW_IP:5432"
echo "  │    Click Save & Test"
echo "  │"
echo "  │ 2. Import updated dashboard:"
echo "  │    Dashboards → Import → Upload JSON file"
echo "  │    → $DASHBOARD_JSON"
echo "  │    → Overwrite existing"
echo "  └─────────────────────────────────────────────────────────────"
echo ""
