# main.py

from dotenv import load_dotenv
import os
import logging
import functools

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import Forbidden

import settings

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Проверка является ли пользователь администратором (поддержка списка через запятую)"""
    admin_ids = os.getenv('TELEGRAM_ADMIN_ID', '')
    if not admin_ids:
        return False
    return user_id in [int(x.strip()) for x in admin_ids.split(',') if x.strip()]

def admin_only(func):
    """Декоратор для команд только для админа"""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not is_admin(user_id):
            logger.warning(f"Non-admin user {user_id} tried to use /{func.__name__.replace('cmd_', '')} command")
            return await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        
        logger.info(f"Admin {user_id} used /{func.__name__.replace('cmd_', '')} command")
        return await func(update, context)
    
    return wrapper
from storage import storage
from economy import give_daily
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
    await update.message.reply_text("Привет! Присоединяйтесь к игре в 21 в групповом чате.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать справку по командам"""
    help_text = """🃏 <b>Blackjack Bot - Справка по командам</b>

<b>🎮 Игровые команды:</b>
Join - присоединиться к игре

<b>💰 Экономика:</b>
/daily - получить ежедневный бонус
/balance - мой баланс и статистика
/top - топ-5 игроков
/stats - сколько игр сыграно в чате

<b>⚙️ Админ:</b>
/setup - настройки (ставка, автозапуск, ожидание)

<b>ℹ️ Как играть:</b>
1. Дождитесь создания игры администратором
2. Нажмите кнопку Join для участия
3. Получите карты в личку и делайте ходы
4. Победитель получает награду!

<b>💡 Подсказки:</b>
• Цель игры - набрать 21 очко или близко к этому числу
• Если наберете больше 21 - проиграете
• Кнопки управления игрой приходят в личные сообщения"""
    
    await update.message.reply_text(help_text, parse_mode='HTML')


@admin_only
async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        return await update.message.reply_text("Эта команда работает только в группе.")
    group_id = update.effective_chat.id

    # Проверяем, не запущена ли уже игра
    if context.chat_data.get('game'):
        return await update.message.reply_text("⚠️ Игра уже запущена! Дождитесь окончания текущей игры.")
    
    price = get_group_setting(group_id, 'auto_game_price', settings.DEFAULT_PRICE)
    join_timeout = get_group_setting(group_id, 'join_timeout', settings.JOIN_TIMEOUT)

    game = Game()
    context.chat_data['game'] = game
    context.chat_data['owner_id'] = update.effective_user.id
    context.chat_data['join_count'] = 0
    context.chat_data['price'] = price

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Join (0)", callback_data="join")]])
    msg = await update.message.reply_text(
        f"Новая игра! Ставка: {price}💳\nЖдём, пока игроки нажмут Join ({join_timeout} сек).",
        reply_markup=kb
    )
    context.chat_data['join_msg_id'] = msg.message_id

    context.job_queue.run_once(
        close_registration,
        when=join_timeout,
        chat_id=group_id
    )

def get_group_setting(group_id, key, default):
    """Получить настройку группы из storage."""
    group_data = storage._data.get(str(group_id), {})
    return group_data.get(key, default)


def set_group_setting(group_id, key, value):
    """Сохранить настройку группы в storage."""
    group_data = storage._data.setdefault(str(group_id), {})
    group_data[key] = value
    storage.save()


def fmt_interval(seconds):
    """Форматировать интервал в человекочитаемый вид."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m:
        return f"{h}ч {m}м"
    if h:
        return f"{h}ч"
    return f"{m}м"


def make_setup_text(group_id, context=None):
    """Текст настроек с инфо о следующем автозапуске."""
    autogame = get_group_setting(group_id, 'auto_game_enabled', settings.AUTO_GAME_ENABLED)
    text = "⚙️ Настройки"
    if autogame and context:
        job = context.chat_data.get('auto_game_job')
        if job and job.next_t:
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            delta = job.next_t - now
            total_sec = max(0, int(delta.total_seconds()))
            m, s = divmod(total_sec, 60)
            h, m = divmod(m, 60)
            if h:
                text += f"\n\n🕐 След. автозапуск через {h}ч {m}м"
            else:
                text += f"\n\n🕐 След. автозапуск через {m}м {s}с"
    return text


def make_setup_kb(group_id):
    """Создать клавиатуру настроек."""
    autogame = get_group_setting(group_id, 'auto_game_enabled', settings.AUTO_GAME_ENABLED)
    price = get_group_setting(group_id, 'auto_game_price', settings.AUTO_GAME_PRICE)
    timeout = get_group_setting(group_id, 'join_timeout', settings.JOIN_TIMEOUT)
    interval = get_group_setting(group_id, 'auto_game_interval', settings.AUTO_GAME_INTERVAL)
    autogame_label = "🎰 Автозапуск: ВКЛ" if autogame else "🎰 Автозапуск: ВЫКЛ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(autogame_label, callback_data="setup_autogame")],
        [InlineKeyboardButton(f"🔄 Интервал: {fmt_interval(interval)}", callback_data="setup_interval")],
        [InlineKeyboardButton(f"💰 Ставка: {price}💳", callback_data="setup_price")],
        [InlineKeyboardButton(f"⏱ Ожидание: {timeout} сек", callback_data="setup_timeout")],
    ])


@admin_only
async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        return await update.message.reply_text("Эта команда работает только в группе.")
    group_id = update.effective_chat.id
    await update.message.reply_text(make_setup_text(group_id, context), reply_markup=make_setup_kb(group_id))


async def cb_setup_autogame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    group_id = update.effective_chat.id

    enabled = get_group_setting(group_id, 'auto_game_enabled', settings.AUTO_GAME_ENABLED)
    if enabled:
        set_group_setting(group_id, 'auto_game_enabled', False)
        if context.chat_data.get('auto_game_job'):
            context.chat_data['auto_game_job'].schedule_removal()
            context.chat_data['auto_game_job'] = None
    else:
        set_group_setting(group_id, 'auto_game_enabled', True)
        interval = get_group_setting(group_id, 'auto_game_interval', settings.AUTO_GAME_INTERVAL)
        if not context.chat_data.get('auto_game_job'):
            job = context.job_queue.run_repeating(
                auto_start_game, interval=interval, chat_id=group_id, first=interval
            )
            context.chat_data['auto_game_job'] = job

    await query.edit_message_text(make_setup_text(group_id, context), reply_markup=make_setup_kb(group_id))


async def cb_setup_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0", callback_data="setprice:0"),
            InlineKeyboardButton("10", callback_data="setprice:10"),
            InlineKeyboardButton("20", callback_data="setprice:20"),
            InlineKeyboardButton("50", callback_data="setprice:50"),
            InlineKeyboardButton("100", callback_data="setprice:100"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="setup_back")],
    ])
    await query.edit_message_text("💰 Выберите ставку:", reply_markup=kb)


async def cb_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    group_id = update.effective_chat.id
    price = int(query.data.split(":")[1])
    set_group_setting(group_id, 'auto_game_price', price)
    context.chat_data['price'] = price
    await query.edit_message_text(make_setup_text(group_id, context), reply_markup=make_setup_kb(group_id))


async def cb_setup_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30с", callback_data="settimeout:30"),
            InlineKeyboardButton("60с", callback_data="settimeout:60"),
            InlineKeyboardButton("90с", callback_data="settimeout:90"),
            InlineKeyboardButton("120с", callback_data="settimeout:120"),
            InlineKeyboardButton("180с", callback_data="settimeout:180"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="setup_back")],
    ])
    await query.edit_message_text("⏱ Выберите время ожидания игроков:", reply_markup=kb)


async def cb_settimeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    group_id = update.effective_chat.id
    timeout = int(query.data.split(":")[1])
    set_group_setting(group_id, 'join_timeout', timeout)
    await query.edit_message_text(make_setup_text(group_id, context), reply_markup=make_setup_kb(group_id))


async def cb_setup_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30м", callback_data="setinterval:1800"),
            InlineKeyboardButton("1ч", callback_data="setinterval:3600"),
            InlineKeyboardButton("2ч", callback_data="setinterval:7200"),
            InlineKeyboardButton("3ч", callback_data="setinterval:10800"),
            InlineKeyboardButton("6ч", callback_data="setinterval:21600"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="setup_back")],
    ])
    await query.edit_message_text("🔄 Выберите интервал автозапуска:", reply_markup=kb)


async def cb_setinterval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    group_id = update.effective_chat.id
    interval = int(query.data.split(":")[1])
    set_group_setting(group_id, 'auto_game_interval', interval)
    # Перезапускаем job если автозапуск включён
    if context.chat_data.get('auto_game_job'):
        context.chat_data['auto_game_job'].schedule_removal()
        job = context.job_queue.run_repeating(
            auto_start_game, interval=interval, chat_id=group_id, first=interval
        )
        context.chat_data['auto_game_job'] = job
    await query.edit_message_text(make_setup_text(group_id, context), reply_markup=make_setup_kb(group_id))


async def cb_setup_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("Только для админа.", show_alert=True)
    await query.answer()
    group_id = update.effective_chat.id
    await query.edit_message_text(make_setup_text(group_id, context), reply_markup=make_setup_kb(group_id))


async def cb_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user    = query.from_user
    group_id= update.effective_chat.id
    game    = context.chat_data.get('game')
    price   = context.chat_data.get('price', 0)
    udata   = storage.get_user(group_id, user.id, user.first_name)
    
    logger.info(f"User {user.id} ({user.first_name}) joined game in group {group_id}, price: {price}")

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
        # если не удалось в личку — отменяем и возвращаем ставку
        game.players.pop(user.id, None)
        storage.add_money(group_id, user.id, price)
        storage.save()
        await query.answer()
        
        # Создаем кнопку со ссылкой на бота
        bot_username = context.bot.username
        start_url = f"https://t.me/{bot_username}?start=start"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Начать игру", url=start_url)]
        ])
        
        return await context.bot.send_message(
            group_id,
            f"👤 {user.first_name}, нажмите кнопку ниже, чтобы начать игру!",
            reply_markup=keyboard
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

    # Таймеры хода будут запущены после раздачи карт в close_registration


async def close_registration(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    group_id = job.chat_id
    data = context.chat_data
    game: Game = data.get('game')
    count = data.get('join_count', 0)

    try:
        await context.bot.edit_message_reply_markup(
            chat_id=group_id,
            message_id=data.get('join_msg_id'),
            reply_markup=None
        )
    except Exception:
        # Игнорируем ошибки редактирования сообщения
        pass

    if count < 1:
        price = data.get('price', 0)
        if game and price > 0:
            for uid in game.players:
                storage.add_money(group_id, uid, price)
            storage.save()
        data['game'] = None
        return await context.bot.send_message(
            group_id,
            f"⏱ Регистрация завершена — никто не присоединился. Игра отменена."
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
        
        # Запускаем таймеры хода для каждого игрока
        # предупреждение через 30 секунд
        context.job_queue.run_once(
            player_warning,
            when=PLAYER_WARN_TIMEOUT,
            chat_id=uid,
            data={'group_id': group_id},
            name=f"player_warning_{uid}"
        )
        # окончательный таймаут через 45 секунд (30+15)
        context.job_queue.run_once(
            partial(player_timeout, group_id=group_id),
            when=PLAYER_EXPIRE_TIMEOUT,
            chat_id=uid,
            name=f"player_timeout_{uid}"
        )

    first = game.dealer[0]
    await context.bot.send_message(group_id, f"Первая карта дилера: {first.rank}{first.suit}")

async def player_warning(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.chat_id
    group_id = context.job.data.get('group_id') if hasattr(context.job, 'data') else None
    
    # Проверяем, активна ли еще игра
    if group_id:
        game = context.application.chat_data.get(group_id, {}).get('game')
        if not game or not game.started or uid not in game.players:
            logger.info(f"Game ended, skipping warning for user {uid}")
            return
    
    try:
        await context.bot.send_message(
            uid,
            "⚠ Вы не сделали ход за 30 секунд. Не забудьте нажать кнопку!"
        )
        logger.info(f"Sent warning to user {uid}")
    except Forbidden:
        logger.warning(f"Cannot send warning to user {uid} - forbidden")
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
        game.dealer_play()
        await finish_game_group(context, group_id)

async def finish_game_group(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    # достаём и удаляем игру
    game: Game = context.application.chat_data[chat_id].pop('game', None)
    if not game:
        return

    # Отменяем таймеры ходов для всех игроков
    for uid in game.players:
        for name in (f"player_warning_{uid}", f"player_timeout_{uid}"):
            for job in context.job_queue.get_jobs_by_name(name):
                job.schedule_removal()

    # Итог для чата
    price = context.application.chat_data[chat_id].get('price', 0)
    result = game.results(chat_id, price=price)
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
    
    logger.info(f"User {uid} performed action '{action}' in group {group_id}")

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
        elif score == 21:
            # Если ровно 21 — автоматически выполняем stand
            await context.bot.edit_message_text(
                chat_id=uid,
                message_id=query.message.message_id,
                text=f"Ваши карты: {fmt_hand(hand)} ({score})\n🎯 У вас 21! Автоматически останавливаетесь."
            )
            # Выполняем stand автоматически
            game.players[uid]["stand"] = True
            await context.bot.send_message(group_id, f"{game.players[uid]['name']} остановился с {score}.")
        else:
            # Если не перебор и не 21 — обновляем сообщение с новыми картами и новыми кнопками
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

@admin_only
async def cmd_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        return await update.message.reply_text("Эта команда работает только в группе.")
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
        await update.message.reply_text(f"💰 +{settings.DAILY_BONUS} фишек!")
    else:
        await update.message.reply_text(f"Бонус уже получен. Попробуйте через {rem} ч.")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    group_id = update.effective_chat.id
    u = storage.get_user(group_id, uid, update.effective_user.first_name)
    await update.message.reply_text(
        f"Баланс: {u['money']} фишек\nПобед: {u['wins']}\nИгр: {u['games']}"
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    top = storage.leaderboard(group_id, key="money", limit=5)
    if not top:
        return await update.message.reply_text("Пока нет игроков в рейтинге.")
    lines = ["🏆 Топ-5:"]
    for i, u in enumerate(top, 1):
        lines.append(f"{i}. {u['name']}")
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    c = storage.chat_stats(group_id)
    await update.message.reply_text(f"Всего игр сыграно: {c['games_played']}")


@admin_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановить бота (только для админа)"""
    await update.message.reply_text("🛑 Останавливаю бота...")
    context.application.stop_running()

async def auto_start_game(context: ContextTypes.DEFAULT_TYPE):
    """Автоматический запуск игры"""
    job = context.job
    group_id = job.chat_id
    
    # Получаем настройки из storage
    group_data = storage._data.get(str(group_id), {})
    
    # Проверяем включен ли автозапуск
    if not group_data.get('auto_game_enabled', settings.AUTO_GAME_ENABLED):
        return
    
    # Проверяем что нет активной игры
    chat_data = context.application.chat_data.get(group_id, {})
    if chat_data.get('game'):
        return
    
    # Получаем настройки
    price = group_data.get('auto_game_price', settings.AUTO_GAME_PRICE)
    min_players = settings.AUTO_GAME_MIN_PLAYERS
    
    # Проверяем есть ли достаточно игроков с деньгами
    users_with_money = 0
    group_data = storage._data.get(str(group_id), {})
    if 'users' in group_data:
        for user_id, user_data in group_data['users'].items():
            if isinstance(user_data, dict) and user_data.get('money', 0) >= price:
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
    
    join_timeout = get_group_setting(group_id, 'join_timeout', settings.JOIN_TIMEOUT)

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Join (0)", callback_data="join")]])
    msg = await context.bot.send_message(
        group_id,
        f"🎰 <b>Автозапуск игры!</b> Ставка: {price}💳\nЖдём игроков ({join_timeout} сек).",
        reply_markup=kb,
        parse_mode='HTML'
    )
    chat_data['join_msg_id'] = msg.message_id

    # Запускаем таймер регистрации
    context.job_queue.run_once(
        close_registration,
        when=join_timeout,
        chat_id=group_id
    )



async def restore_autogames(app):
    """Восстановить автозапуск игр из storage после рестарта бота."""
    for chat_id_str, group_data in storage._data.items():
        if not isinstance(group_data, dict):
            continue
        if not group_data.get('auto_game_enabled', False):
            continue
        chat_id = int(chat_id_str)
        interval = group_data.get('auto_game_interval', settings.AUTO_GAME_INTERVAL)
        job = app.job_queue.run_repeating(
            auto_start_game,
            interval=interval,
            chat_id=chat_id,
            first=interval
        )
        app.chat_data.setdefault(chat_id, {})['auto_game_job'] = job
        logger.info(f"Restored autogame for chat {chat_id}, interval={interval}s")


def main():
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("Установите TG_BOT_TOKEN")
    app = ApplicationBuilder().post_init(restore_autogames).token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("newgame", cmd_newgame))
    app.add_handler(CallbackQueryHandler(cb_join, pattern="^join$"))
    app.add_handler(CallbackQueryHandler(cb_action, pattern="^(hit|stand):"))
    app.add_handler(CommandHandler("deal", cmd_deal))

    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("stop", cmd_stop))

    # Setup inline callbacks
    app.add_handler(CallbackQueryHandler(cb_setup_autogame, pattern="^setup_autogame$"))
    app.add_handler(CallbackQueryHandler(cb_setup_price, pattern="^setup_price$"))
    app.add_handler(CallbackQueryHandler(cb_setprice, pattern="^setprice:"))
    app.add_handler(CallbackQueryHandler(cb_setup_timeout, pattern="^setup_timeout$"))
    app.add_handler(CallbackQueryHandler(cb_settimeout, pattern="^settimeout:"))
    app.add_handler(CallbackQueryHandler(cb_setup_interval, pattern="^setup_interval$"))
    app.add_handler(CallbackQueryHandler(cb_setinterval, pattern="^setinterval:"))
    app.add_handler(CallbackQueryHandler(cb_setup_back, pattern="^setup_back$"))

    print("Bot up...")
    app.run_polling()


if __name__ == "__main__":
    main()