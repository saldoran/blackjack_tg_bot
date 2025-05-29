from dotenv import load_dotenv
import random, os, time
from collections import namedtuple
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

import settings
from storage import storage
from economy import give_daily, reward_player
load_dotenv()

Card = namedtuple("Card", ["rank", "suit"])
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]

# ---------------- Blackjack helpers ------------------------------------
def new_deck():
    return [Card(r, s) for r in RANKS for s in SUITS]

def card_value(card: Card):
    if card.rank in ["J", "Q", "K", "10"]:
        return 10
    if card.rank == "A":
        return 11
    return int(card.rank)

def hand_value(hand):
    v = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c.rank == "A")
    while v > 21 and aces:
        v -= 10
        aces -= 1
    return v

def fmt_hand(hand):
    return " ".join(f"{c.rank}{c.suit}" for c in hand)

# ---------------- Game class -------------------------------------------
class Game:
    def __init__(self):
        self.deck = new_deck()
        random.shuffle(self.deck)
        self.players = {}  # uid -> dict(name, hand, stand, bust)
        self.dealer = []
        self.started = False

    def add_player(self, uid, name):
        if self.started or uid in self.players:
            return False
        self.players[uid] = {"name": name, "hand": [], "stand": False, "bust": False}
        return True

    def deal_initial(self):
        for _ in range(2):
            for p in self.players.values():
                p["hand"].append(self.deck.pop())
            self.dealer.append(self.deck.pop())

    def hit(self, uid):
        p = self.players[uid]
        p["hand"].append(self.deck.pop())
        if hand_value(p["hand"]) > 21:
            p["bust"] = True
            p["stand"] = True

    def all_done(self):
        return all(p["stand"] for p in self.players.values())

    def dealer_play(self):
        while hand_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())

    def results(self, chat_id):
        dealer_score = hand_value(self.dealer)
        dealer_bust = dealer_score > 21
        lines = [f"Дилер: {fmt_hand(self.dealer)} ({dealer_score}{' перебор' if dealer_bust else ''})"]
        for uid, p in self.players.items():
            score = hand_value(p["hand"])
            name = p['name']
            if p["bust"]:
                outcome = 'lose'
            elif dealer_bust or score > dealer_score:
                outcome = 'win'
            elif score == dealer_score:
                outcome = 'draw'
            else:
                outcome = 'lose'
            delta = reward_player(chat_id, uid, outcome)
            sign = "+" if delta >= 0 else "-"
            if delta == 0:
                delta_str = "0"
            else:
                delta_str = f"{sign}{abs(delta)}"
            lines.append(
                f"{name}: {fmt_hand(p['hand'])} ({score}) → {outcome.upper()} ({delta_str} фишек)"
            )
        storage.add_game(chat_id)
        storage.save()
        return "\n".join(lines)

# ---------------- Telegram handlers ------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! /newgame чтобы начать новую игру в 21.")

async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['game'] = Game()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Join", callback_data="join")]])
    await update.message.reply_text("Новая игра! Нажимайте Join, затем админ введёт /deal.", reply_markup=kb)

async def cb_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game: Game = context.chat_data.get('game')
    if not game:
        await query.answer("Игра ещё не создана.", show_alert=True)
        return
    ok = game.add_player(query.from_user.id, query.from_user.first_name)
    if ok:
        await query.answer("Вы в игре!")
    else:
        await query.answer("Не удалось присоединиться.", show_alert=True)

async def cmd_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game: Game = context.chat_data.get('game')
    if not game or game.started:
        await update.message.reply_text("Игра не создана или уже идёт.")
        return
    if not game.players:
        await update.message.reply_text("Нет игроков — никто не нажал Join.")
        return

    player_names = [p["name"] for p in game.players.values()]
    await update.message.reply_text(
        "Игроки в партии: " + ", ".join(player_names)
    )
    game.started = True
    game.deal_initial()
    # send hands
    for uid, p in game.players.items():
        msg = f"Ваши карты: {fmt_hand(p['hand'])} ({hand_value(p['hand'])}).\n/hit или /stand в чате."
        try:
            await context.bot.send_message(uid, msg)
        except:
            await update.message.reply_text(f"Не удалось отправить сообщения {p['name']} (приватность).")
    dealer_first = game.dealer[0]
    await update.message.reply_text(f"Раздача! Первая карта дилера: {dealer_first.rank}{dealer_first.suit}")

async def cmd_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game: Game = context.chat_data.get('game')
    if not game or not game.started:
        return
    uid = update.effective_user.id
    if uid not in game.players:
        return
    if game.players[uid]["stand"]:
        await update.message.reply_text("Вы уже остановились.")
        return
    game.hit(uid)
    p = game.players[uid]
    await update.message.reply_text(f"{fmt_hand(p['hand'])} ({hand_value(p['hand'])})")
    if p["bust"]:
        await update.message.reply_text("Перебор!")
    if game.all_done():
        await finish_game(update, context)

async def cmd_stand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game: Game = context.chat_data.get('game')
    if not game or not game.started:
        return
    uid = update.effective_user.id
    if uid not in game.players:
        return
    game.players[uid]["stand"] = True
    await update.message.reply_text("Вы остановились.")
    if game.all_done():
        await finish_game(update, context)

async def finish_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game: Game = context.chat_data.get('game')
    chat_id = update.effective_chat.id
    game.dealer_play()
    txt = game.results(chat_id)
    await context.bot.send_message(chat_id, "Игра окончена!\n" + txt)
    context.chat_data['game'] = None

# -------- Economy commands --------------------------------------------
async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    ok, remaining = give_daily(chat_id, uid)
    if ok:
        money = storage.get_user(chat_id, uid)['money']
        await update.message.reply_text(f"💰 +{settings.DAILY_BONUS}! Ваш баланс: {money}")
    else:
        await update.message.reply_text(f"Бонус уже получен. Попробуйте через {remaining} ч.")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    user = storage.get_user(chat_id, uid, update.effective_user.first_name)
    await update.message.reply_text(
        f"Баланс: {user['money']} фишек\nПобед: {user['wins']}\nИгр: {user['games']}"
    )

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    top = storage.leaderboard(chat_id, key="money", limit=5)
    lines = ["🏆 Top-5 по фишкам:"]
    for i, u in enumerate(top, 1):
        lines.append(f"{i}. {u['name']} — {u['money']} фишек (побед {u['wins']})")
    await update.message.reply_text("\n".join(lines))

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = storage.chat_stats(chat_id)
    await update.message.reply_text(
        f"Всего игр сыграно: {chat['games_played']}"
    )

# ---------------- Main -------------------------------------------------
def main():
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("Установите TG_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newgame", cmd_newgame))
    app.add_handler(CallbackQueryHandler(cb_join, pattern="^join$"))
    app.add_handler(CommandHandler("deal", cmd_deal))
    app.add_handler(CommandHandler("hit", cmd_hit))
    app.add_handler(CommandHandler("stand", cmd_stand))

    # Economy
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("stats", cmd_stats))

    print("Bot up...")
    app.run_polling()

if __name__ == "__main__":
    main()
