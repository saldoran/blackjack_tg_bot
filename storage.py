
import json, os, time
from threading import Lock
import settings

_lock = Lock()

class Storage:
    def __init__(self, path: str = settings.STATS_FILE):
        self.path = path
        self._data = {}
        self.load()

    # --- File IO --------------------------------------------------------
    def load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # --- Helpers --------------------------------------------------------
    def _chat(self, chat_id: int):
        chat = self._data.setdefault(str(chat_id), {"games_played": 0, "users": {}})
        return chat

    def get_user(self, chat_id: int, user_id: int, name: str | None = None):
        chat = self._chat(chat_id)
        user = chat["users"].setdefault(str(user_id), {
            "name": name or "Anon",
            "money": 0,
            "wins": 0,
            "games": 0,
            "last_daily": 0
        })
        if name:
            user["name"] = name
        return user

    # --- Stats API ------------------------------------------------------
    def add_game(self, chat_id: int):
        chat = self._chat(chat_id)
        chat["games_played"] += 1

    def add_win(self, chat_id: int, user_id: int):
        user = self.get_user(chat_id, user_id)
        user["wins"] += 1

    def add_money(self, chat_id: int, user_id: int, delta: int):
        user = self.get_user(chat_id, user_id)
        user["money"] += delta

    def set_daily(self, chat_id: int, user_id: int, timestamp: float):
        user = self.get_user(chat_id, user_id)
        user["last_daily"] = timestamp

    # --- Queries --------------------------------------------------------
    def leaderboard(self, chat_id: int, key: str = "money", limit: int = 5):
        chat = self._chat(chat_id)
        users = list(chat["users"].values())
        users.sort(key=lambda u: u.get(key, 0), reverse=True)
        return users[:limit]

    def chat_stats(self, chat_id: int):
        return self._chat(chat_id)

# Singleton instance
storage = Storage()
