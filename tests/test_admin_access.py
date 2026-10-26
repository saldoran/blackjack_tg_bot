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
    title = None if chat_type == "private" else "Группа"
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name="Игрок", username=None),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type, title=title),
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
        bot=SimpleNamespace(get_chat_administrators=get_admins, send_message=AsyncMock()),
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
    def owner_report(self, context):
        reports = [call.args[1] for call in context.bot.send_message.await_args_list if call.args[0] == OWNER_ID]
        self.assertEqual(len(reports), 1)
        return reports[0]

    def test_chat_admin_opens_setup(self):
        update = make_update(CHAT_ADMIN_ID)
        context = make_context()
        with patch.object(main, "storage", SimpleNamespace(_data={})):
            asyncio.run(main.cmd_setup(update, context))
        self.assertIn("Настройки", update.message.reply_text.await_args.args[0])
        context.bot.send_message.assert_not_awaited()

    def test_refusal_is_reported_to_owner(self):
        context = make_context()
        asyncio.run(main.cmd_setup(make_update(MEMBER_ID), context))
        report = self.owner_report(context)
        self.assertIn("/setup", report)
        self.assertIn(str(GROUP_ID), report)
        self.assertIn("нет среди 1 админов", report)

    def test_private_refusal_reason_reaches_owner(self):
        context = make_context()
        asyncio.run(main.cmd_setup(make_update(CHAT_ADMIN_ID, chat_type="private"), context))
        self.assertIn("в личке", self.owner_report(context))

    def test_api_error_reason_reaches_owner(self):
        context = make_context(error=TelegramError("Chat not found"))
        asyncio.run(main.cmd_setup(make_update(CHAT_ADMIN_ID), context))
        self.assertIn("Chat not found", self.owner_report(context))

    def test_member_cannot_open_setup(self):
        update = make_update(MEMBER_ID)
        asyncio.run(main.cmd_setup(update, make_context()))
        self.assertIn("только для админов группы", update.message.reply_text.await_args.args[0])

    def test_private_chat_refusal_points_to_group(self):
        update = make_update(CHAT_ADMIN_ID, chat_type="private")
        asyncio.run(main.cmd_setup(update, make_context()))
        self.assertIn("в группе", update.message.reply_text.await_args.args[0])

    def test_channel_sender_refusal_asks_to_write_as_self(self):
        update = make_update(MEMBER_ID, sender_chat=SimpleNamespace(id=-1009999999999))
        asyncio.run(main.cmd_setup(update, make_context()))
        self.assertIn("от имени канала", update.message.reply_text.await_args.args[0])

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
