# main.py

from dotenv import load_dotenv
import os

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import Forbidden

import settings
from storage import storage
from economy import give_daily, reward_player
from game import Game, fmt_hand, hand_value

load_dotenv()

JOIN_TIMEOUT = settings.JOIN_TIMEOUT  # 60 секунд

def make_private_kb(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Взять карту", callback_data=f"hit:{group_id}")],
        [InlineKeyboardButton("✋ Остановиться", callback_data=f"stand:{group_id}")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! /newgame чтобы начать новую игру в 21.")


async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    game = Game()
    context.chat_data['game'] = game
    context.chat_data['join_count'] = 0

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Join (0)", callback_data="join")]])
    msg = await update.message.reply_text(
        f"Новая игра! Ждём, пока игроки нажмут Join ({JOIN_TIMEOUT} сек).",
        reply_markup=kb
    )
    context.chat_data['join_msg_id'] = msg.message_id

    context.job_queue.run_once(
        close_registration,
        when=JOIN_TIMEOUT,
        chat_id=group_id
    )


async def cb_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    group_id = update.effective_chat.id
    game: Game = context.chat_data.get('game')

    if not game or game.started:
        return await query.answer("Игра не создана или уже идёт.", show_alert=True)

    if not game.add_player(user.id, user.first_name):
        return await query.answer("Вы уже в игре.", show_alert=True)

    try:
        await context.bot.send_message(
            user.id,
            "✅ Вы присоединились к игре! Ждите раздачи карт в личном чате."
        )
        await query.answer()
    except Forbidden:
        game.players.pop(user.id, None)
        await query.answer()
        await context.bot.send_message(
            group_id,
            f"👤 {user.first_name}, я не могу написать вам в личку. "
            "Пожалуйста, напишите боту /start:\n"
            f"https://t.me/{(await context.bot.get_me()).username}"
        )
        return

    await context.bot.send_message(group_id, f"👤 {user.first_name} присоединился к игре.")
    count = context.chat_data.get('join_count', 0) + 1
    context.chat_data['join_count'] = count

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Join ({count})", callback_data="join")]])
    await context.bot.edit_message_reply_markup(
        chat_id=group_id,
        message_id=context.chat_data['join_msg_id'],
        reply_markup=kb
    )


async def close_registration(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    group_id = job.chat_id
    data = context.chat_data
    game: Game = data.get('game')
    count = data.get('join_count', 0)

    await context.bot.edit_message_reply_markup(
        chat_id=group_id,
        message_id=data.get('join_msg_id'),
        reply_markup=None
    )

    if count < 2:
        data['game'] = None
        return await context.bot.send_message(
            group_id,
            f"⏱ Регистрация завершена — зарегистрировался {count} игрок(ов). Нужно минимум 2. Игра отменена."
        )

    names = [p['name'] for p in game.players.values()]
    await context.bot.send_message(
        group_id,
        "⏱ Регистрация завершена. Игроки: " + ", ".join(names)
    )

    game.started = True
    game.deal_initial()
    for uid, p in game.players.items():
        await context.bot.send_message(
            uid,
            f"Ваши карты: {fmt_hand(p['hand'])} ({hand_value(p['hand'])})",
            reply_markup=make_private_kb(group_id)
        )

    first = game.dealer[0]
    await context.bot.send_message(group_id, f"Первая карта дилера: {first.rank}{first.suit}")


async def cb_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # разбираем action и group_id из callback_data вида "hit:<group_id>" или "stand:<group_id>"
    action, group_id = query.data.split(":")
    group_id = int(group_id)
    uid = query.from_user.id

    # достаём данные конкретной игры из chat_data группового чата
    game = context.application.chat_data.get(group_id, {}).get('game')

    # Если нет игры или пользователь не в списке — скрываем кнопки и выходим
    if not game or not game.started or uid not in game.players:
        await context.bot.edit_message_reply_markup(
            chat_id=uid,
            message_id=query.message.message_id,
            reply_markup=None
        )
        return await context.bot.send_message(uid, "Игра неактивна или вы не в ней.")

    # Убираем старую клавиатуру (скрываем кнопки)
    await context.bot.edit_message_reply_markup(
        chat_id=uid,
        message_id=query.message.message_id,
        reply_markup=None
    )

    # Обрабатываем ход
    if action == "hit":
        game.hit(uid)
        hand = game.players[uid]["hand"]
        score = hand_value(hand)

        # Если перебор — редактируем текст и всё, без кнопок
        if score > 21:
            await context.bot.edit_message_text(
                chat_id=uid,
                message_id=query.message.message_id,
                text=f"Ваши карты: {fmt_hand(hand)} ({score})\nПеребор! Вы выбываете."
            )
        else:
            # Если не перебор — обновляем сообщение с новыми картами и новыми кнопками
            await context.bot.edit_message_text(
                chat_id=uid,
                message_id=query.message.message_id,
                text=f"Ваши карты: {fmt_hand(hand)} ({score})",
                reply_markup=make_private_kb(group_id)
            )

    else:  # action == "stand"
        game.players[uid]["stand"] = True
        await context.bot.edit_message_text(
            chat_id=uid,
            message_id=query.message.message_id,
            text="✋ Вы остановились."
        )

    # Если после хода все закончили — подводим итоги в группе
    if game.all_done():
        game.dealer_play()
        result = game.results(group_id)
        await context.bot.send_message(group_id, "Игра окончена!\n" + result)
        # сбрасываем игру
        context.application.chat_data[group_id]['game'] = None


async def cmd_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    game: Game = context.chat_data.get('game')
    if not game or game.started:
        return await update.message.reply_text("Игра не создана или уже идёт.")
    if not game.players:
        return await update.message.reply_text("Нет игроков — никто не нажал Join.")

    names = [p['name'] for p in game.players.values()]
    await update.message.reply_text("Игроки: " + ", ".join(names))

    game.started = True
    game.deal_initial()
    for uid, p in game.players.items():
        await context.bot.send_message(
            uid,
            f"Ваши карты: {fmt_hand(p['hand'])} ({hand_value(p['hand'])})",
            reply_markup=make_private_kb(group_id)
        )

    first = game.dealer[0]
    await update.message.reply_text(f"Первая карта дилера: {first.rank}{first.suit}")


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    group_id = update.effective_chat.id
    ok, rem = give_daily(group_id, uid)
    if ok:
        bal = storage.get_user(group_id, uid)['money']
        await update.message.reply_text(f"💰 +{settings.DAILY_BONUS}! Ваш баланс: {bal}")
    else:
        await update.message.reply_text(f"Бонус уже получен. Попробуйте через {rem} ч.")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    group_id = update.effective_chat.id
    u = storage.get_user(group_id, uid, update.effective_user.first_name)
    await update.message.reply_text(
        f"Баланс: {u['money']} фишек\nПобед: {u['wins']}\nИгр: {u['games']}"
    )


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    top = storage.leaderboard(group_id, key="money", limit=5)
    lines = ["🏆 Топ-5 по фишкам:"]
    for i, u in enumerate(top, 1):
        lines.append(f"{i}. {u['name']} — {u['money']} фишек (побед {u['wins']})")
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    c = storage.chat_stats(group_id)
    await update.message.reply_text(f"Всего игр сыграно: {c['games_played']}")


def main():
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("Установите TG_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newgame", cmd_newgame))
    app.add_handler(CallbackQueryHandler(cb_join, pattern="^join$"))
    app.add_handler(CallbackQueryHandler(cb_action, pattern="^(hit|stand):"))
    app.add_handler(CommandHandler("deal", cmd_deal))

    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("stats", cmd_stats))

    print("Bot up...")
    app.run_polling()


if __name__ == "__main__":
    main()