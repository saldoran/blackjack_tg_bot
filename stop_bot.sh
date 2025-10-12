#!/bin/bash
BOT_NAME="blackjack_bot"
PID_FILE="/tmp/${BOT_NAME}.pid"

# Проверяем существование PID файла
if [ ! -f "$PID_FILE" ]; then
    echo "PID файл не найден. Бот $BOT_NAME не запущен или уже остановлен."
    exit 0
fi

# Читаем PID
PID=$(cat "$PID_FILE")

# Проверяем, существует ли процесс
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Процесс с PID $PID не найден. Удаляем PID файл."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Остановка бота $BOT_NAME (PID: $PID)..."

# Пытаемся мягко завершить процесс
kill "$PID"

# Ждем 5 секунд
sleep 5

# Проверяем, завершился ли процесс
if ps -p "$PID" > /dev/null 2>&1; then
    echo "Процесс не завершился мягко, принудительно завершаем..."
    kill -9 "$PID"
    sleep 2
fi

# Проверяем результат
if ps -p "$PID" > /dev/null 2>&1; then
    echo "Ошибка: не удалось остановить бот $BOT_NAME"
    exit 1
else
    echo "Бот $BOT_NAME успешно остановлен"
    rm -f "$PID_FILE"
    exit 0
fi
