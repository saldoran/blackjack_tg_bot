
import time
import settings
from storage import storage

def give_daily(chat_id: int, user_id: int):
    now = time.time()
    user = storage.get_user(chat_id, user_id)
    delta_h = (now - user["last_daily"]) / 3600
    if delta_h < settings.DAILY_COOLDOWN_HOURS:
        return False, round(settings.DAILY_COOLDOWN_HOURS - delta_h, 1)
    storage.add_money(chat_id, user_id, settings.DAILY_BONUS)
    storage.set_daily(chat_id, user_id, now)
    storage.save()
    return True, 0

def reward_player(chat_id: int, user_id: int, outcome: str):
    if outcome == "win":
        delta = settings.WIN_REWARD
        storage.add_win(chat_id, user_id)
    elif outcome == "draw":
        delta = settings.DRAW_REWARD
    else:
        delta = settings.LOSE_PENALTY
    storage.add_money(chat_id, user_id, delta)
    storage.save()
    return delta
