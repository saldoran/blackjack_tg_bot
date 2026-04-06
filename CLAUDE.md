# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Проект

Telegram-бот для игры в блэкджек (21) в групповых чатах. Написан на Python 3.13 с использованием `python-telegram-bot` v22+. Игровой процесс: регистрация через inline-кнопки в группе, ходы — через личные сообщения бота.

## Команды запуска

```bash
# Установка зависимостей (venv находится на два уровня выше: ../../venv)
source ../../venv/bin/activate
pip install -r requirements.txt

# Запуск
python main.py

# Через скрипты управления (используют PID-файл /tmp/blackjack_bot.pid)
./run_bot.sh        # запуск в фоне, логи → logs/bot.log
./stop_bot.sh       # остановка по PID
./restart_bot.sh    # stop + start
./status_bot.sh     # RUNNING:<pid> или STOPPED
```

## Переменные окружения (.env)

- `TG_BOT_TOKEN` — токен бота (обязательно)
- `TELEGRAM_ADMIN_ID` — user ID администратора; все команды управления (@admin_only) доступны только ему

## Архитектура

**main.py** — точка входа, хэндлеры команд и callback-запросов, декоратор `@admin_only`, таймеры ходов. Содержит всю логику взаимодействия с Telegram API.

**game.py** — игровая механика: колода (`Card` namedtuple), подсчёт очков (`hand_value` с учётом тузов), класс `Game` (добавление игроков, раздача, hit/stand, логика дилера, подсчёт результатов). `Game.results()` сам вызывает `reward_player` и `storage.save()`.

**economy.py** — экономическая логика: `give_daily()` (бонус с кулдауном) и `reward_player()` (начисление/списание фишек по итогу раунда). Оба напрямую мутируют `storage`.

**storage.py** — JSON-хранилище (`storage.json`). Синглтон `storage` загружается при импорте. Структура: `{chat_id: {games_played, users: {user_id: {name, money, wins, games, last_daily}}}}`. Автозапуск хранит настройки (`auto_game_enabled`, `auto_game_interval`, `auto_game_price`) в корне объекта чата.

**settings.py** — все числовые константы: экономика (DAILY_BONUS, WIN_REWARD и т.д.), таймауты (JOIN_TIMEOUT, PLAYER_WARN/EXPIRE_TIMEOUT), автозапуск (AUTO_GAME_*).

## Ключевые паттерны

- Состояние игры хранится в `context.chat_data['game']` (объект `Game`), живёт только в памяти на время раунда
- Персистентные данные (баланс, статистика) — в `storage.json` через синглтон `storage`
- Игрок взаимодействует через inline-кнопки в ЛС; callback_data содержит `group_id` для маршрутизации: `"hit:<group_id>"`
- Таймеры хода (`player_warning`, `player_timeout`) запускаются через `job_queue` при раздаче карт
- Автозапуск игр — `run_repeating` job, настройки персистятся в `storage._data`

## Язык

Весь UI и комментарии на русском языке. Сообщения бота на русском.
