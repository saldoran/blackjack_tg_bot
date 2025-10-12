#!/bin/bash
cd "$(dirname "$0")"
mkdir -p logs
source ../../venv/bin/activate
nohup python main.py > logs/bot.log 2>&1 &
echo $! > /tmp/blackjack_bot.pid
