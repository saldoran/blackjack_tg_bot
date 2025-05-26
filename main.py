
import random
from collections import namedtuple
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

Card = namedtuple("Card", ["rank", "suit"])

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]

def new_deck():
    return [Card(rank, suit) for rank in RANKS for suit in SUITS]

def card_value(card: Card):
    if card.rank in ["J", "Q", "K", "10"]:
        return 10
    if card.rank == "A":
        return 11
    return int(card.rank)

def hand_value(hand):
    value = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c.rank == "A")
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value

class Game:
    def __init__(self):
        self.deck = new_deck()
        random.shuffle(self.deck)
        self.players = {}  # user_id: {"name": str, "hand": list[Card], "stand": bool, "bust": bool}
        self.started = False
        self.dealer_hand = []

    def add_player(self, user_id, name):
        if self.started:
            return False
        self.players[user_id] = {"name": name, "hand": [], "stand": False, "bust": False}
        return True

    def deal_initial(self):
        for _ in range(2):
            for p in self.players.values():
                p["hand"].append(self.deck.pop())
            self.dealer_hand.append(self.deck.pop())

    def hit(self, user_id):
        player = self.players[user_id]
        player["hand"].append(self.deck.pop())
        if hand_value(player["hand"]) > 21:
            player["bust"] = True
            player["stand"] = True

    def all_done(self):
        return all(p["stand"] for p in self.players.values())

    def dealer_play(self):
        while hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())

    def results(self):
        dealer_score = hand_value(self.dealer_hand)
        dealer_bust = dealer_score > 21
        lines = [
            f"Дилер: {format_hand(self.dealer_hand)} ({dealer_score}{' перебор' if dealer_bust else ''})"
        ]
        for p in self.players.values():
            score = hand_value(p["hand"])
            status = 'перебор' if p["bust"] else ''
            if p["bust"]:
                outcome = 'проигрыш'
            elif dealer_bust or score > dealer_score:
                outcome = 'победа'
            elif score == dealer_score:
                outcome = 'ничья'
            else:
                outcome = 'проигрыш'
            lines.append(f"{p['name']}: {format_hand(p['hand'])} ({score} {status}) → {outcome}")
        return "\n".join(lines)

def format_hand(hand):
    return " ".join(f"{c.rank}{c.suit}" for c in hand)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Используй /newgame чтобы начать новую игру в 21 (Blackjack).")

async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['game'] = Game()
    await update.message.reply_text(
        "Новая игра создана! Все желающие нажимайте кнопку 'Join'. Когда все готовы, инициатор напишет /deal.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Join', callback_data='join')]])
    )

async def join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    game: Game = context.chat_data.get('game')
    if not game:
        await query.answer('Игра не создана.')
        return
    if game.add_player(user.id, user.first_name):
        await query.answer('Вы присоединились к игре!')
    else:
        await query.answer('Игра уже началась либо вы уже в игре.')

async def deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game: Game = context.chat_data.get('game')
    if not game or game.started:
        await update.message.reply_text('Игра не создана или уже началась.')
        return
    if not game.players:
        await update.message.reply_text('Нет игроков.')
        return
    game.started = True
    game.deal_initial()
    # личные сообщения игрокам
    for uid, p in game.players.items():
        await context.bot.send_message(
            uid,
            f"Ваши карты: {format_hand(p['hand'])} ({hand_value(p['hand'])}).\n"
            "В групповом чате используйте /hit чтобы взять карту или /stand чтобы остановиться."
        )
    dealer_first = game.dealer_hand[0]
    await update.message.reply_text(
        f"Раздача завершена. Первая карта дилера: {dealer_first.rank}{dealer_first.suit}"
    )

async def hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    game: Game = context.chat_data.get('game')
    if not game or not game.started:
        return
    if user.id not in game.players:
        return
    if game.players[user.id]['stand']:
        await update.message.reply_text('Вы уже остановились.')
        return
    game.hit(user.id)
    player = game.players[user.id]
    await update.message.reply_text(
        f"Ваши карты: {format_hand(player['hand'])} ({hand_value(player['hand'])})"
    )
    if player['bust']:
        await update.message.reply_text('Перебор! Вы выбываете.')
    if game.all_done():
        await finish_game(update, context)

async def stand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    game: Game = context.chat_data.get('game')
    if not game or not game.started:
        return
    if user.id not in game.players:
        return
    game.players[user.id]['stand'] = True
    await update.message.reply_text('Вы остановились.')
    if game.all_done():
        await finish_game(update, context)

async def finish_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game: Game = context.chat_data.get('game')
    game.dealer_play()
    result = game.results()
    await context.bot.send_message(update.effective_chat.id, "Игра окончена!\n" + result)
    context.chat_data['game'] = None

def main():
    import os
    token = os.getenv('TG_BOT_TOKEN')
    if not token:
        raise RuntimeError('Необходимо установить переменную окружения TG_BOT_TOKEN с токеном вашего бота')
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('newgame', newgame))
    app.add_handler(CallbackQueryHandler(join_cb, pattern='^join$'))
    app.add_handler(CommandHandler('deal', deal))
    app.add_handler(CommandHandler('hit', hit))
    app.add_handler(CommandHandler('stand', stand))

    print('Bot is running...')
    app.run_polling()

if __name__ == '__main__':
    main()
