#!/bin/bash
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_NAME="$(basename "$BOT_DIR")"
PID_FILE="/tmp/${BOT_NAME}.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "RUNNING:$PID"
    else
        echo "STOPPED"
        rm -f "$PID_FILE"
    fi
else
    echo "STOPPED"
fi
