# game.py
import random
from collections import namedtuple
from economy import reward_player
from storage import storage

# Описание карт
Card = namedtuple("Card", ["rank", "suit"])
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]

# Хелперы для расчёта очков
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

# Собственно класс игры
class Game:
    def __init__(self):
        self.deck = new_deck()
        random.shuffle(self.deck)
        self.players = {}      # uid → {name, hand, stand, bust}
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
            name = p["name"]
            if p["bust"]:
                outcome = "lose"
            elif dealer_bust or score > dealer_score:
                outcome = "win"
            elif score == dealer_score:
                outcome = "draw"
            else:
                outcome = "lose"

            delta = reward_player(chat_id, uid, outcome)
            sign = "+" if delta >= 0 else "-"
            delta_str = "0" if delta == 0 else f"{sign}{abs(delta)}"
            lines.append(
                f"{name}: {fmt_hand(p['hand'])} ({score}) → {outcome.upper()} ({delta_str} фишек)"
            )

        storage.add_game(chat_id)
        storage.save()
        return "\n".join(lines)