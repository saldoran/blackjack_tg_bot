#!/bin/bash
BOT_NAME="blackjack_bot"
PID_FILE="/tmp/${BOT_NAME}.pid"

# Проверяем, не запущен ли уже бот
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Бот $BOT_NAME уже запущен (PID: $PID)"
        exit 1
    else
        # Удаляем старый PID файл
        rm -f "$PID_FILE"
    fi
fi

echo "Запуск бота $BOT_NAME..."

# Переходим в директорию бота
cd "$(dirname "$0")"
mkdir -p logs

# Активируем виртуальное окружение
source ../../venv/bin/activate

# Запускаем бота в фоне
nohup python main.py > logs/bot.log 2>&1 &

# Сохраняем PID
echo $! > "$PID_FILE"

echo "Бот $BOT_NAME запущен (PID: $!)"
