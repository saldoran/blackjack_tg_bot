#!/bin/bash
BOT_NAME="blackjack_bot"
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
