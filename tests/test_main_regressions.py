import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


class DummyStorage:
    def __init__(self):
        self._data = {}

    def get_user(self, chat_id, user_id, name=None):
        chat = self._data.setdefault(str(chat_id), {"users": {}})
        user = chat["users"].setdefault(str(user_id), {"name": name or "Anon", "money": 0})
        if name:
            user["name"] = name
        return user

    def add_money(self, chat_id, user_id, delta):
        self.get_user(chat_id, user_id)["money"] += delta

    def save(self):
        return None


class DummyGame:
    def __init__(self):
        self.started = False
        self.players = {}

    def add_player(self, uid, name):
        if uid in self.players:
            return False
        self.players[uid] = {"name": name, "hand": [], "stand": False, "bust": False}
        return True


class DummyJobQueue:
    def __init__(self):
        self.calls = []

    def run_once(self, callback, when=None, chat_id=None, name=None):
        self.calls.append({"callback": callback, "when": when, "chat_id": chat_id, "name": name})
        return SimpleNamespace()

    def get_jobs_by_name(self, _):
        return []


class MainRegressionTests(unittest.TestCase):
    def test_cb_join_duplicate_does_not_charge_twice(self):
        storage = DummyStorage()
        storage.get_user(777, 101, "Игрок")["money"] = 100
        game = DummyGame()
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=999)),
            edit_message_reply_markup=AsyncMock(),
        )
        context = SimpleNamespace(
            chat_data={"game": game, "price": 20, "join_msg_id": 1, "join_count": 0},
            bot=bot,
        )
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=101, first_name="Игрок"),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=777))

        with patch.object(main, "storage", storage):
            asyncio.run(main.cb_join(update, context))
            asyncio.run(main.cb_join(update, context))

        self.assertEqual(storage.get_user(777, 101)["money"], 80)
        self.assertEqual(len(game.players), 1)

    def test_auto_start_game_persists_group_chat_data(self):
        storage = DummyStorage()
        storage._data = {
            "555": {
                "auto_game_enabled": True,
                "auto_game_price": 20,
                "users": {"1": {"money": 20}},
            }
        }
        job_queue = DummyJobQueue()
        context = SimpleNamespace(
            job=SimpleNamespace(chat_id=555),
            application=SimpleNamespace(chat_data={}),
            job_queue=job_queue,
            bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42))),
        )

        with patch.object(main, "storage", storage):
            asyncio.run(main.auto_start_game(context))

        self.assertIn(555, context.application.chat_data)
        self.assertIn("game", context.application.chat_data[555])
        self.assertEqual(context.application.chat_data[555]["price"], 20)
        self.assertEqual(len(job_queue.calls), 1)

    def test_player_timeout_ignores_missing_group_data(self):
        context = SimpleNamespace(
            job=SimpleNamespace(chat_id=321),
            application=SimpleNamespace(chat_data={}),
            bot=SimpleNamespace(send_message=AsyncMock()),
        )

        asyncio.run(main.player_timeout(context, group_id=999))


if __name__ == "__main__":
    unittest.main()
