#!/bin/bash
# Inventory App — startup script for Synology NAS (DSM Task Scheduler)
#
# Setup:
#   1. Copy this file to your inventory_app_v5/ folder on the NAS
#   2. Make it executable:  chmod +x start_app.sh
#   3. In DSM: Control Panel > Task Scheduler > Create > Triggered Task
#      Trigger: Boot-up   User: root   Script: bash /path/to/start_app.sh
#
# The app will then start automatically every time the NAS reboots.

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$APP_DIR/app.log"
PID_FILE="$APP_DIR/app.pid"

# Try to find Python 3 (works for built-in DSM Python and Entware installs)
PYTHON_BIN=""
for p in /usr/local/bin/python3 /usr/bin/python3 /opt/bin/python3; do
    if [ -x "$p" ]; then
        PYTHON_BIN="$p"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): ERROR — Python 3 not found. Install Python 3 via Synology Package Center." >> "$LOG_FILE"
    exit 1
fi

# Kill any previous instance
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID"
        sleep 2
    fi
    rm -f "$PID_FILE"
fi

# Rotate log if it exceeds 5 MB
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.old"
fi

# Start the app in the background
cd "$APP_DIR"
nohup "$PYTHON_BIN" app.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "$(date): Inventory app started (PID: $(cat "$PID_FILE"), Python: $PYTHON_BIN)" >> "$LOG_FILE"
