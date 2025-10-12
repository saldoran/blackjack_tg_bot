#!/bin/bash
PID_FILE="/tmp/blackjack_bot.pid"
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    kill "$PID"
    rm -f "$PID_FILE"
fi
