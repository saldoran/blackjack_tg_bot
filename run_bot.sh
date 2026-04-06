#!/bin/bash
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_NAME="$(basename "$BOT_DIR")"
PID_FILE="/tmp/${BOT_NAME}.pid"
LOG_DIR="$BOT_DIR/logs"

cd "$BOT_DIR" || exit 1
mkdir -p "$LOG_DIR"

# Проверяем, не запущен ли уже бот
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Бот $BOT_NAME уже запущен (PID: $PID)"
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

echo "Запуск бота $BOT_NAME..."

# Активируем виртуальное окружение
source venv/bin/activate 2>/dev/null || true

# Запускаем бота в фоне
nohup python3 main.py > "$LOG_DIR/${BOT_NAME}_stdout_$(date +%Y%m%d).log" 2>&1 &

# Сохраняем PID
echo $! > "$PID_FILE"

echo "Бот $BOT_NAME запущен (PID: $!)"
