#!/bin/bash
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$BOT_DIR/stop_bot.sh"
sleep 1
"$BOT_DIR/run_bot.sh"
