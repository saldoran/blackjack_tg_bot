import asyncio
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden

import main
from storage import Storage

OWNER_ID = 1
MEMBER_ID = 3
ACTIVE_CHAT = -1001111111111
QUIET_CHAT = -1002222222222
GONE_CHAT = -1003333333333
DAY = 86400
TITLES = {ACTIVE_CHAT: "Покерный клуб", QUIET_CHAT: "Тихий чат"}


def make_update(user_id, chat_type="private"):
    chat_id = user_id if chat_type == "private" else ACTIVE_CHAT
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


async def fake_get_chat(chat_id):
    if chat_id not in TITLES:
        raise Forbidden("bot was kicked from the supergroup chat")
    return SimpleNamespace(title=TITLES[chat_id])


class ChatsCommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Storage(os.path.join(tmp.name, "storage.json"))
        now = time.time()
        self.store.get_user(ACTIVE_CHAT, 10, "А")["last_daily"] = now - 3600
        self.store.add_game(ACTIVE_CHAT)
        self.store._chat(ACTIVE_CHAT)["auto_game_enabled"] = True
        self.store.get_user(QUIET_CHAT, 11, "Б")["last_daily"] = now - 30 * DAY
        self.store.get_user(GONE_CHAT, 12, "В")
        self.store.get_user(OWNER_ID, OWNER_ID, "Владелец")
        for patcher in (
            patch.object(main, "storage", self.store),
            patch.dict(os.environ, {"TELEGRAM_ADMIN_ID": str(OWNER_ID)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_chats(self, update):
        context = SimpleNamespace(bot=SimpleNamespace(get_chat=AsyncMock(side_effect=fake_get_chat)))
        asyncio.run(main.cmd_chats(update, context))
        return [call.args[0] for call in update.message.reply_text.await_args_list]

    def test_owner_sees_groups_sorted_by_activity(self):
        text = "\n".join(self.run_chats(make_update(OWNER_ID)))
        self.assertIn("Групп в базе: 3, активных за 7 дн.: 1, недоступных: 1", text)
        self.assertLess(text.index("Покерный клуб"), text.index("Тихий чат"))
        self.assertIn("игр 1, игроков 1, активность сегодня, автозапуск вкл", text)
        self.assertIn("активность 30 дн. назад", text)
        self.assertIn("недоступен", text)
        self.assertNotIn(f"(id {OWNER_ID})", text)

    def test_member_is_refused(self):
        text = "\n".join(self.run_chats(make_update(MEMBER_ID)))
        self.assertIn("только для владельца", text)
        self.assertNotIn("Покерный клуб", text)

    def test_owner_in_group_gets_no_list(self):
        text = "\n".join(self.run_chats(make_update(OWNER_ID, chat_type="supergroup")))
        self.assertIn("в личке", text)
        self.assertNotIn("Покерный клуб", text)

    def test_long_list_is_split_into_messages(self):
        for i in range(80):
            self.store.get_user(-1004000000000 - i, 20, "Г")
        messages = self.run_chats(make_update(OWNER_ID))
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 4096 for message in messages))
        self.assertEqual(sum(message.count("(id -100") for message in messages), 83)


class LastActivityTests(unittest.TestCase):
    def test_last_activity_takes_latest_game_or_daily(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Storage(os.path.join(tmp, "storage.json"))
            self.assertEqual(store.last_activity(ACTIVE_CHAT), 0)
            store.get_user(ACTIVE_CHAT, 10)["last_daily"] = 100.0
            self.assertEqual(store.last_activity(ACTIVE_CHAT), 100.0)
            store.add_game(ACTIVE_CHAT)
            self.assertGreater(store.last_activity(ACTIVE_CHAT), 100.0)


if __name__ == "__main__":
    unittest.main()
