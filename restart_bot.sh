#!/bin/bash
./stop_bot.sh
sleep 1
nohup ./run_bot.sh > /dev/null 2>&1 &
