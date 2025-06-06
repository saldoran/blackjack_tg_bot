
# Telegram Blackjack («21») Bot v2

Играй в «21» прямо в групповом чате Telegram — с учётом побед и виртуальными фишками!

## Быстрый старт

```bash
python -m pip install -r requirements.txt
export TG_BOT_TOKEN=123456:ABC-DEF...
python main.py
```

## Экономика

| Событие  | Фишки |
|----------|-------|
| /daily   | +100  |
| Победа   | +50   |
| Ничья    | 0     |
| Поражение| -25   |

Эти значения настраиваются в **settings.py**.

## Команды

| Команда        | Кто      | Что делает                                     |
|----------------|----------|-----------------------------------------------|
| /newgame       | админ    | создать игру                                   |
| Join           | игрок    | присоединиться                                 |
| /deal          | админ    | начать раздачу                                 |
| /hit           | игрок    | взять карту                                    |
| /stand         | игрок    | остановиться                                   |
| /daily         | любой    | ежедневный бонус                               |
| /balance       | любой    | мой баланс и статистика                        |
| /leaderboard   | любой    | топ‑5 по деньгам                               |
| /stats         | любой    | сколько игр сыграно в чате                     |

## Хранение данных

Вся статистика хранится в `storage.json` в корне проекта ‒ достаточно для личных или небольших групп.
