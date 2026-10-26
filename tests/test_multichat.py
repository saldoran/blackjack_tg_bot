import asyncio
import os
import tempfile
import unittest
from contextlib import contextmanager
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import game as game_module
import main
from game import Game
from storage import Storage

GROUP_A = -1001111111111
GROUP_B = -1002222222222
OLD_GROUP = -333333333
NEW_GROUP = -1003333333333
OWNER_ID = 1
PLAYER_ID = 7


@contextmanager
def temp_storage():
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(os.path.join(tmp, "storage.json"))
        with patch.object(main, "storage", store), patch.object(game_module, "storage", store):
            yield store


class FakeJob:
    def __init__(self, callback, chat_id, name, data):
        self.callback = callback
        self.chat_id = chat_id
        self.name = name
        self.data = data
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self._jobs = []

    def run_once(self, callback, when=None, chat_id=None, name=None, data=None):
        job = FakeJob(callback, chat_id, name, data)
        self._jobs.append(job)
        return job

    def jobs(self):
        return tuple(job for job in self._jobs if not job.removed)

    def get_jobs_by_name(self, name):
        return tuple(job for job in self.jobs() if job.name == name)


class FakeApplication:
    def __init__(self, chat_data):
        self._chat_data = chat_data

    @property
    def chat_data(self):
        return MappingProxyType(self._chat_data)

    def drop_chat_data(self, chat_id):
        self._chat_data.pop(chat_id, None)


def make_bot():
    return SimpleNamespace(
        send_message=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        delete_message=AsyncMock(),
    )


def migration_message(from_old_group):
    if from_old_group:
        return SimpleNamespace(
            chat=SimpleNamespace(id=OLD_GROUP), migrate_to_chat_id=NEW_GROUP, migrate_from_chat_id=None
        )
    return SimpleNamespace(
        chat=SimpleNamespace(id=NEW_GROUP), migrate_to_chat_id=None, migrate_from_chat_id=OLD_GROUP
    )


class NewChatStorageTests(unittest.TestCase):
    def test_chat_configured_before_first_game_works(self):
        with temp_storage() as store:
            main.set_group_setting(GROUP_A, "auto_game_price", 50)
            store.get_user(GROUP_A, PLAYER_ID, "Игрок")
            store.add_game(GROUP_A)
            self.assertEqual(store.leaderboard(GROUP_A)[0]["name"], "Игрок")
            self.assertEqual(store.chat_stats(GROUP_A)["games_played"], 1)
            self.assertEqual(main.get_group_setting(GROUP_A, "auto_game_price", None), 50)


class TurnTimersTests(unittest.TestCase):
    def test_game_end_keeps_player_timers_in_other_group(self):
        job_queue = FakeJobQueue()
        bot = make_bot()
        chat_data = {}
        for group_id in (GROUP_A, GROUP_B):
            game = Game()
            game.add_player(PLAYER_ID, "Игрок")
            chat_data[group_id] = {"game": game, "price": 0, "join_count": 1, "join_msg_id": 1}
            context = SimpleNamespace(
                job=SimpleNamespace(chat_id=group_id),
                chat_data=chat_data[group_id],
                job_queue=job_queue,
                bot=bot,
            )
            asyncio.run(main.close_registration(context))

        context = SimpleNamespace(application=SimpleNamespace(chat_data=chat_data), job_queue=job_queue, bot=bot)
        with temp_storage():
            asyncio.run(main.finish_game_group(context, GROUP_A))

        groups_left = [
            job.data["group_id"] if job.data else job.callback.keywords["group_id"]
            for job in job_queue.jobs()
        ]
        self.assertEqual(groups_left, [GROUP_B, GROUP_B])


@patch.dict(os.environ, {"TELEGRAM_ADMIN_ID": str(OWNER_ID)})
class SetPriceTests(unittest.TestCase):
    def test_new_price_applies_from_next_game(self):
        query = SimpleNamespace(
            data="setprice:100",
            from_user=SimpleNamespace(id=OWNER_ID),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=query.from_user,
            effective_chat=SimpleNamespace(id=GROUP_A, type="supergroup"),
            message=None,
        )
        context = SimpleNamespace(chat_data={"game": Game(), "price": 20}, job_queue=FakeJobQueue(), bot=make_bot())
        with temp_storage():
            asyncio.run(main.cb_setprice(update, context))
            self.assertEqual(main.get_group_setting(GROUP_A, "auto_game_price", None), 100)
        self.assertEqual(context.chat_data["price"], 20)


class MigrationTests(unittest.TestCase):
    def run_migration(self, context, from_old_group):
        update = SimpleNamespace(message=migration_message(from_old_group))
        asyncio.run(main.migrate_group(update, context))

    def test_data_and_autogame_move_to_new_id(self):
        job_queue = FakeJobQueue()
        context = SimpleNamespace(
            application=FakeApplication({OLD_GROUP: {"price": 20}}),
            job_queue=job_queue,
            bot=make_bot(),
        )
        with temp_storage() as store:
            store.get_user(OLD_GROUP, PLAYER_ID, "Игрок")["money"] = 150
            main.set_group_setting(OLD_GROUP, "auto_game_enabled", True)
            main.schedule_autogame(job_queue, OLD_GROUP, when=100)

            self.run_migration(context, from_old_group=True)
            self.run_migration(context, from_old_group=False)

            self.assertNotIn(str(OLD_GROUP), store._data)
            self.assertEqual(store.get_user(NEW_GROUP, PLAYER_ID)["money"], 150)
            self.assertTrue(main.get_group_setting(NEW_GROUP, "auto_game_enabled", False))
        self.assertIsNone(main.get_autogame_job(job_queue, OLD_GROUP))
        self.assertIsNotNone(main.get_autogame_job(job_queue, NEW_GROUP))
        self.assertNotIn(OLD_GROUP, context.application.chat_data)

    def test_running_game_is_cancelled_with_refund(self):
        job_queue = FakeJobQueue()
        game = Game()
        game.add_player(PLAYER_ID, "Игрок")
        context = SimpleNamespace(
            application=FakeApplication({OLD_GROUP: {"game": game, "price": 20}}),
            job_queue=job_queue,
            bot=make_bot(),
        )
        registration = job_queue.run_once(main.close_registration, when=90, chat_id=OLD_GROUP)
        with temp_storage() as store:
            store.get_user(OLD_GROUP, PLAYER_ID, "Игрок")["money"] = 80
            self.run_migration(context, from_old_group=False)
            self.assertEqual(store.get_user(NEW_GROUP, PLAYER_ID)["money"], 100)
        self.assertTrue(registration.removed)
        self.assertEqual(context.bot.send_message.await_args.args[0], NEW_GROUP)


if __name__ == "__main__":
    unittest.main()
