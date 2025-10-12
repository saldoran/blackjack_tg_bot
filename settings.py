
# Настройки экономики
DAILY_BONUS = 100        # фишек за /daily
WIN_REWARD = 50          # фишек победителю
DRAW_REWARD = 0          # фишек за ничью
LOSE_PENALTY = -25       # фишек за проигрыш
DAILY_COOLDOWN_HOURS = 24
JOIN_TIMEOUT = 90
DEFAULT_PRICE = 20
PLAYER_WARN_TIMEOUT = 30
PLAYER_EXPIRE_TIMEOUT = 45

# Файл, где хранится статистика (создаётся автоматически)
STATS_FILE = 'storage.json'

# Настройки автозапуска игр
AUTO_GAME_ENABLED = False        # включен ли автозапуск
AUTO_GAME_INTERVAL = 120        # интервал в секундах (по умолчанию 2 минуты для теста)
AUTO_GAME_PRICE = 20            # ставка для автозапуска
AUTO_GAME_MIN_PLAYERS = 2       # минимальное количество игроков для автозапуска
