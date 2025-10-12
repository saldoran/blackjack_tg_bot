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
from functools import partial
import asyncio

load_dotenv()

JOIN_TIMEOUT = settings.JOIN_TIMEOUT  # 60 секунд
PLAYER_WARN_TIMEOUT = settings.PLAYER_WARN_TIMEOUT
PLAYER_EXPIRE_TIMEOUT = settings.PLAYER_EXPIRE_TIMEOUT

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
    context.chat_data['owner_id'] = update.effective_user.id
    context.chat_data['join_count'] = 0
    # по умолчанию ставка = 0
    context.chat_data.setdefault('price', settings.DEFAULT_PRICE)
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

async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить цену (ставку) для новой партии."""
    owner = context.chat_data.get('owner_id')
    if update.effective_user.id != owner:
        return await update.message.reply_text("⚠ Только инициатор игры может менять ставку.")

    # парсим аргумент
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Использование: /setprice <цена>")
    price = int(context.args[0], settings.DEFAULT_PRICE)
    if price < 0:
        return await update.message.reply_text("Цена должна быть неотрицательной.")

    context.chat_data['price'] = price
    await update.message.reply_text(f"💰 Ставка для этой игры установлена: {price}💳")


async def cb_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user    = query.from_user
    group_id= update.effective_chat.id
    game    = context.chat_data.get('game')
    price   = context.chat_data.get('price', 0)
    udata   = storage.get_user(group_id, user.id, user.first_name)

    # 1) Проверяем ставку
    if udata['money'] < price:
        return await query.answer(
            f"У вас недостаточно фишек (ставка {price}, у вас {udata['money']})",
            show_alert=True
        )

    # 2) Проверяем состояние игры
    if not game or game.started:
        return await query.answer("Игра не создана или уже идёт.", show_alert=True)

    # 3) Списываем ставку сразу
    storage.add_money(group_id, user.id, -price)
    storage.save()

    # 4) Добавляем в игру
    ok = game.add_player(user.id, user.first_name)
    if not ok:
        return await query.answer("Вы уже в игре.", show_alert=True)

    # 5) Уведомляем игрока в личке
    try:
        await context.bot.send_message(
            user.id,
            "✅ Вы присоединились! Ждите раздачи карт в личке."
        )
        await query.answer()
    except Forbidden:
        # если не удалось в личку — отменяем
        game.players.pop(user.id, None)
        await query.answer()
        return await context.bot.send_message(
            group_id,
            f"👤 {user.first_name}, напишите боту /start в личку, чтобы играть."
        )

    # 6) Публичное сообщение и обновление кнопки
    await context.bot.send_message(group_id, f"👤 {user.first_name} присоединился к игре.")
    cnt = context.chat_data.get('join_count', 0) + 1
    context.chat_data['join_count'] = cnt
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Join ({cnt})", callback_data="join")]])
    await context.bot.edit_message_reply_markup(
        chat_id=group_id,
        message_id=context.chat_data['join_msg_id'],
        reply_markup=kb
    )

    # 7) Запланировать таймеры хода
    # предупреждение через 30 секунд
    context.job_queue.run_once(
        player_warning,
        when=PLAYER_WARN_TIMEOUT,
        chat_id=user.id
    )
    # окончательный таймаут через 45 секунд (30+15)
    context.job_queue.run_once(
        partial(player_timeout, group_id=group_id),
        when=PLAYER_EXPIRE_TIMEOUT,
        chat_id=user.id
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

async def player_warning(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.chat_id
    try:
        await context.bot.send_message(
            uid,
            "⚠ Вы не сделали ход за 30 секунд. Не забудьте нажать кнопку!"
        )
    except Forbidden:
        pass

async def player_timeout(context: ContextTypes.DEFAULT_TYPE, group_id: int):
    uid = context.job.chat_id
    game: Game = context.application.chat_data[group_id].get('game')
    if not game or uid not in game.players or game.players[uid]['stand']:
        return

    # помечаем как «выбыл»
    game.players[uid]['bust'] = True
    game.players[uid]['stand'] = True

    try:
        await context.bot.send_message(uid, "⏰ Время вышло — вы выбываете.")
    except Forbidden:
        pass

    # информируем группу
    name = game.players[uid]['name']
    await context.bot.send_message(
        group_id,
        f"⚠ Игрок {name} не успел сделать ход и выбывает."
    )

    # если все ещё окончено, подводим итоги
    if game.all_done():
        await finish_game_group(context, group_id)

async def finish_game_group(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    # достаём и удаляем игру
    game: Game = context.application.chat_data[chat_id].pop('game', None)
    if not game:
        return

    # Итог для чата
    result = game.results(chat_id)
    await context.bot.send_message(chat_id, "🃏 Игра окончена!\n" + result)

    # Личный баланс каждому игроку
    for uid in game.players:
        user = storage.get_user(chat_id, uid)
        bal = user['money']
        try:
            await context.bot.send_message(uid, f"Ваш текущий баланс: {bal}💳")
        except Forbidden:
            # если нельзя писать в личку – пропускаем
            pass

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
        await finish_game_group(context, group_id)

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
    lines = ["🏆 Топ-5 лидеров:"]
    for i, u in enumerate(top, 1):
        lines.append(f"{i}. {u['name']}")
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    c = storage.chat_stats(group_id)
    await update.message.reply_text(f"Всего игр сыграно: {c['games_played']}")


async def cmd_autogame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление автозапуском игр"""
    group_id = update.effective_chat.id
    
    if not context.args:
        # Показать текущие настройки
        enabled = context.chat_data.get('auto_game_enabled', settings.AUTO_GAME_ENABLED)
        interval = context.chat_data.get('auto_game_interval', settings.AUTO_GAME_INTERVAL)
        price = context.chat_data.get('auto_game_price', settings.AUTO_GAME_PRICE)
        
        status = "✅ Включен" if enabled else "❌ Выключен"
        hours = interval // 3600
        minutes = (interval % 3600) // 60
        
        text = f"""🎰 <b>Настройки автозапуска игр</b>

{status}
⏰ Интервал: {hours}ч {minutes}м
💰 Ставка: {price}💳

<b>Команды:</b>
/autogame on - включить автозапуск
/autogame off - выключить автозапуск
/autogame interval <минуты> - установить интервал
/autogame price <ставка> - установить ставку"""
        
        await update.message.reply_text(text, parse_mode='HTML')
        return
    
    command = context.args[0].lower()
    
    if command == "on":
        context.chat_data['auto_game_enabled'] = True
        # Запускаем автозапуск если его еще нет
        if not context.chat_data.get('auto_game_job'):
            interval = context.chat_data.get('auto_game_interval', settings.AUTO_GAME_INTERVAL)
            job = context.job_queue.run_repeating(
                auto_start_game,
                interval=interval,
                chat_id=group_id,
                first=interval
            )
            context.chat_data['auto_game_job'] = job
        await update.message.reply_text("✅ Автозапуск игр включен!")
        
    elif command == "off":
        context.chat_data['auto_game_enabled'] = False
        # Останавливаем автозапуск
        if context.chat_data.get('auto_game_job'):
            context.chat_data['auto_game_job'].schedule_removal()
            context.chat_data['auto_game_job'] = None
        await update.message.reply_text("❌ Автозапуск игр выключен!")
        
    elif command == "interval" and len(context.args) > 1:
        try:
            minutes = int(context.args[1])
            if minutes < 1:
                return await update.message.reply_text("❌ Интервал должен быть больше 0 минут")
            
            interval = minutes * 60
            context.chat_data['auto_game_interval'] = interval
            
            # Перезапускаем автозапуск с новым интервалом
            if context.chat_data.get('auto_game_job'):
                context.chat_data['auto_game_job'].schedule_removal()
                job = context.job_queue.run_repeating(
                    auto_start_game,
                    interval=interval,
                    chat_id=group_id,
                    first=interval
                )
                context.chat_data['auto_game_job'] = job
            
            await update.message.reply_text(f"⏰ Интервал автозапуска установлен: {minutes} минут")
        except ValueError:
            await update.message.reply_text("❌ Неверный формат времени. Используйте: /autogame interval <минуты>")
            
    elif command == "price" and len(context.args) > 1:
        try:
            price = int(context.args[1])
            if price < 0:
                return await update.message.reply_text("❌ Ставка не может быть отрицательной")
            
            context.chat_data['auto_game_price'] = price
            await update.message.reply_text(f"💰 Ставка для автозапуска установлена: {price}💳")
        except ValueError:
            await update.message.reply_text("❌ Неверный формат ставки. Используйте: /autogame price <ставка>")
    else:
        await update.message.reply_text("❌ Неизвестная команда. Используйте: /autogame on/off/interval/price")


async def auto_start_game(context: ContextTypes.DEFAULT_TYPE):
    """Автоматический запуск игры"""
    job = context.job
    group_id = job.chat_id
    chat_data = context.application.chat_data.get(group_id, {})
    
    # Проверяем включен ли автозапуск
    if not chat_data.get('auto_game_enabled', settings.AUTO_GAME_ENABLED):
        return
    
    # Проверяем что нет активной игры
    if chat_data.get('game'):
        return
    
    # Получаем настройки
    price = chat_data.get('auto_game_price', settings.AUTO_GAME_PRICE)
    min_players = settings.AUTO_GAME_MIN_PLAYERS
    
    # Проверяем есть ли достаточно игроков с деньгами
    users_with_money = 0
    for user_id, user_data in storage.data.get(str(group_id), {}).items():
        if user_id != 'stats' and user_data.get('money', 0) >= price:
            users_with_money += 1
    
    if users_with_money < min_players:
        await context.bot.send_message(
            group_id,
            f"🎰 Автозапуск: недостаточно игроков с балансом {price}💳 (нужно минимум {min_players})"
        )
        return
    
    # Запускаем игру
    game = Game()
    chat_data['game'] = game
    chat_data['owner_id'] = None  # автозапуск
    chat_data['join_count'] = 0
    chat_data['price'] = price
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Join (0)", callback_data="join")]])
    msg = await context.bot.send_message(
        group_id,
        f"🎰 <b>Автозапуск игры!</b> Ставка: {price}💳\nЖдём игроков ({JOIN_TIMEOUT} сек).",
        reply_markup=kb,
        parse_mode='HTML'
    )
    chat_data['join_msg_id'] = msg.message_id
    
    # Запускаем таймер регистрации
    context.job_queue.run_once(
        close_registration,
        when=JOIN_TIMEOUT,
        chat_id=group_id
    )


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
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("autogame", cmd_autogame))    

    print("Bot up...")
    app.run_polling()


if __name__ == "__main__":
    main()