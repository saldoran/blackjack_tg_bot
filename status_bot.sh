#!/bin/bash
PID_FILE="/tmp/blackjack_bot.pid"
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "RUNNING:$PID"
    else
        echo "STOPPED"
        rm -f "$PID_FILE"
    fi
else
    echo "STOPPED"
fi
