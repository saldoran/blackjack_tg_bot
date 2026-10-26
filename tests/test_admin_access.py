import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import TelegramError

import main

GROUP_ID = -1001234567890
OWNER_ID = 1
CHAT_ADMIN_ID = 2
MEMBER_ID = 3
GROUP_ANONYMOUS_BOT_ID = 1087968824


def make_update(user_id, chat_type="supergroup", sender_chat=None):
    chat_id = user_id if chat_type == "private" else GROUP_ID
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        message=SimpleNamespace(sender_chat=sender_chat, reply_text=AsyncMock()),
    )


def make_callback_update(user_id):
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=query.from_user,
        effective_chat=SimpleNamespace(id=GROUP_ID, type="supergroup"),
        message=None,
        callback_query=query,
    )


def make_context(error=None):
    admins = (SimpleNamespace(user=SimpleNamespace(id=CHAT_ADMIN_ID)),)
    get_admins = AsyncMock(side_effect=error) if error else AsyncMock(return_value=admins)
    return SimpleNamespace(
        bot=SimpleNamespace(get_chat_administrators=get_admins),
        application=SimpleNamespace(stop_running=Mock()),
        chat_data={},
    )


@patch.dict(os.environ, {"TELEGRAM_ADMIN_ID": str(OWNER_ID)})
class CanManageTests(unittest.TestCase):
    def can_manage(self, update, context):
        return asyncio.run(main.can_manage(update, context))

    def test_env_admin_skips_api_call(self):
        context = make_context()
        self.assertTrue(self.can_manage(make_update(OWNER_ID), context))
        context.bot.get_chat_administrators.assert_not_awaited()

    def test_chat_admin_can_manage(self):
        context = make_context()
        self.assertTrue(self.can_manage(make_update(CHAT_ADMIN_ID), context))
        context.bot.get_chat_administrators.assert_awaited_once_with(GROUP_ID)

    def test_member_cannot_manage(self):
        self.assertFalse(self.can_manage(make_update(MEMBER_ID), make_context()))

    def test_private_chat_skips_api_call(self):
        context = make_context()
        update = make_update(CHAT_ADMIN_ID, chat_type="private")
        self.assertFalse(self.can_manage(update, context))
        context.bot.get_chat_administrators.assert_not_awaited()

    def test_anonymous_admin_can_manage(self):
        context = make_context()
        update = make_update(GROUP_ANONYMOUS_BOT_ID, sender_chat=SimpleNamespace(id=GROUP_ID))
        self.assertTrue(self.can_manage(update, context))
        context.bot.get_chat_administrators.assert_not_awaited()

    def test_api_error_denies_access(self):
        context = make_context(error=TelegramError("Chat not found"))
        self.assertFalse(self.can_manage(make_update(CHAT_ADMIN_ID), context))


@patch.dict(os.environ, {"TELEGRAM_ADMIN_ID": str(OWNER_ID)})
class AdminHandlersTests(unittest.TestCase):
    def test_chat_admin_opens_setup(self):
        update = make_update(CHAT_ADMIN_ID)
        with patch.object(main, "storage", SimpleNamespace(_data={})):
            asyncio.run(main.cmd_setup(update, make_context()))
        self.assertIn("Настройки", update.message.reply_text.await_args.args[0])

    def test_member_cannot_open_setup(self):
        update = make_update(MEMBER_ID)
        asyncio.run(main.cmd_setup(update, make_context()))
        self.assertIn("нет прав", update.message.reply_text.await_args.args[0])

    def test_chat_admin_presses_setup_button(self):
        update = make_callback_update(CHAT_ADMIN_ID)
        with patch.object(main, "storage", SimpleNamespace(_data={})):
            asyncio.run(main.cb_setup_back(update, make_context()))
        update.callback_query.edit_message_text.assert_awaited_once()

    def test_member_cannot_press_setup_button(self):
        update = make_callback_update(MEMBER_ID)
        asyncio.run(main.cb_setup_back(update, make_context()))
        update.callback_query.answer.assert_awaited_once_with("Только для админа.", show_alert=True)
        update.callback_query.edit_message_text.assert_not_awaited()

    def test_chat_admin_cannot_stop_bot(self):
        context = make_context()
        asyncio.run(main.cmd_stop(make_update(CHAT_ADMIN_ID), context))
        context.application.stop_running.assert_not_called()

    def test_env_admin_stops_bot(self):
        context = make_context()
        asyncio.run(main.cmd_stop(make_update(OWNER_ID), context))
        context.application.stop_running.assert_called_once()


if __name__ == "__main__":
    unittest.main()
