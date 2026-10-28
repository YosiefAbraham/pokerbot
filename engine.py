"""A local no-limit hold'em dealer for testing.

Simplifications: stacks reset every hand, and an all-in raise smaller than a
full raise still reopens the betting.
Bots that throw, or return something illegal, are folded (checked if free).
"""
import random
import time
from collections import defaultdict

from cards import evaluate, full_deck
from protocol import STREETS, Event, GameState
from ranges import position_name


def award(contrib, alive, values):
    """Split the pot into side pots. alive is in seat order (odd chips go first)."""
    payouts = defaultdict(int)
    prev = 0
    for level in sorted({c for c in contrib.values() if c > 0}):
        in_layer = [n for n, c in contrib.items() if c >= level]
        amount = (level - prev) * len(in_layer)
        eligible = [n for n in alive if contrib[n] >= level] or alive
        best = max(values[n] for n in eligible)
        winners = [n for n in eligible if values[n] == best]
        share, rem = divmod(amount, len(winners))
        for w in winners:
            payouts[w] += share
        payouts[winners[0]] += rem
        prev = level
    return payouts


class Table:
    def __init__(self, bots, big_blind=100, small_blind=50, stack=10000, seed=None):
        self.bots = bots
        self.bb, self.sb, self.stack = big_blind, small_blind, stack
        self.rng = random.Random(seed)
        self.errors = defaultdict(int)
        self.illegal = defaultdict(int)
        self.max_ms = defaultdict(float)
        self.last_hand = None     # the most recent _Hand, for logging and replays

    def _call(self, bot, method, *args):
        try:
            return getattr(bot, method)(*args)
        except Exception:
            self.errors[bot.name] += 1
            return None

    def play_hand(self, hand_id, button):
        self.last_hand = _Hand(self, hand_id, button)
        return self.last_hand.run()


class _Hand:
    def __init__(self, table, hand_id, button):
        self.t = table
        self.bots = table.bots
        self.names = [b.name for b in self.bots]
        self.by_name = {b.name: b for b in self.bots}
        self.hand_id, self.button = hand_id, button
        self.deck = full_deck()
        table.rng.shuffle(self.deck)
        self.holes = {n: [self.deck.pop(), self.deck.pop()] for n in self.names}
        k = len(self.names)
        self.positions = {self.names[(button + off) % k]: position_name(off, k) for off in range(k)}
        self.stacks = {n: table.stack for n in self.names}
        self.contrib = {n: 0 for n in self.names}
        self.commit = {n: 0 for n in self.names}
        self.folded = set()
        self.board = []
        self.history = []

    def pot(self):
        return sum(self.contrib.values())

    def alive(self):
        return [n for n in self.names if n not in self.folded]

    def can_act(self, n):
        return n not in self.folded and self.stacks[n] > 0

    def put(self, n, amount):
        amount = max(0, min(amount, self.stacks[n]))
        self.stacks[n] -= amount
        self.contrib[n] += amount
        self.commit[n] += amount
        return amount

    def emit(self, ev):
        self.history.append(ev)
        for b in self.bots:
            self.t._call(b, 'observe', ev)

    def run(self):
        t, names, k = self.t, self.names, len(self.names)
        for b in self.bots:
            t._call(b, 'new_hand', self.hand_id, dict(self.positions), list(self.holes[b.name]))
        sb_i, bb_i = ((self.button, (self.button + 1) % k) if k == 2
                      else ((self.button + 1) % k, (self.button + 2) % k))
        for idx, blind in ((sb_i, t.sb), (bb_i, t.bb)):
            n = names[idx]
            before = self.pot()
            amt = self.put(n, blind)
            self.emit(Event(n, 'preflop', 'blind', amt, self.commit[n], before, 0))

        for street in STREETS:
            if street != 'preflop':
                self.board += [self.deck.pop() for _ in range(3 if street == 'flop' else 1)]
                self.commit = {n: 0 for n in names}
            first = (bb_i + 1) % k if street == 'preflop' else (self.button + 1) % k
            self.betting_round(street, first)
            if len(self.alive()) == 1:
                break

        alive = self.alive()
        if len(alive) == 1:
            payouts, shown = {alive[0]: self.pot()}, {}
        else:
            # seat order starting left of the button, for odd chips
            order = [names[(self.button + 1 + i) % k] for i in range(k)]
            alive_ordered = [n for n in order if n in alive]
            values = {n: evaluate(self.holes[n] + self.board) for n in alive}
            payouts = award(self.contrib, alive_ordered, values)
            shown = {n: list(self.holes[n]) for n in alive}
        nets = {n: payouts.get(n, 0) - self.contrib[n] for n in names}
        for b in self.bots:
            t._call(b, 'end_hand', dict(nets), dict(shown), list(self.board))
        return nets

    def betting_round(self, street, first):
        names, k, t = self.names, len(self.names), self.t
        current = max(self.commit.values())
        last_raise = t.bb
        needs = {n for n in names if self.can_act(n)}
        i = first
        while needs and len(self.alive()) > 1:
            n = names[i % k]
            i += 1
            if n not in needs:
                continue
            needs.discard(n)
            to_call = current - self.commit[n]
            others = any(self.can_act(o) for o in self.alive() if o != n)
            if to_call <= 0 and not others:
                continue                                    # nobody left to bet against
            max_to = self.commit[n] + self.stacks[n]
            state = GameState(
                me=n, hole=list(self.holes[n]), board=list(self.board), street=street,
                pot=self.pot(), to_call=min(to_call, self.stacks[n]),
                min_raise_to=min(current + last_raise, max_to), max_raise_to=max_to,
                stack=self.stacks[n], committed=self.commit[n], big_blind=t.bb,
                position=self.positions[n], positions=dict(self.positions),
                opponents=[o for o in self.alive() if o != n],
                history=list(self.history), hand_id=self.hand_id)
            t0 = time.perf_counter()
            action = t._call(self.by_name[n], 'act', state)
            t.max_ms[n] = max(t.max_ms[n], (time.perf_counter() - t0) * 1000)
            kind, to = self.normalize(n, action, state, current)

            before = self.pot()
            if kind == 'fold':
                self.folded.add(n)
                self.emit(Event(n, street, 'fold', 0, self.commit[n], before, to_call))
            elif kind == 'check':
                self.emit(Event(n, street, 'check', 0, self.commit[n], before, 0))
            elif kind == 'call':
                amt = self.put(n, to_call)
                self.emit(Event(n, street, 'call', amt, self.commit[n], before, to_call))
            else:
                amt = self.put(n, to - self.commit[n])
                level = self.commit[n]
                if level - current >= last_raise:
                    last_raise = level - current
                current = level
                needs = {o for o in names if o != n and self.can_act(o)}
                self.emit(Event(n, street, 'bet' if to_call <= 0 else 'raise',
                                amt, level, before, max(0, to_call)))

    def normalize(self, n, action, s, current):
        """Map whatever the bot returned onto a legal action."""
        try:
            kind = action[0]
        except Exception:
            kind = None
        if kind == 'raise':
            try:
                to = int(action[1])
            except Exception:
                to = None
            if to is not None and s.max_raise_to > current:
                to = min(max(to, s.min_raise_to), s.max_raise_to)
                if to > current:
                    return 'raise', to
            kind = 'call'
        if kind == 'call':
            return ('call', None) if s.to_call > 0 else ('check', None)
        if kind == 'check':
            if s.to_call > 0:
                self.t.illegal[n] += 1
                return 'fold', None
            return 'check', None
        if kind == 'fold':
            return 'fold', None
        self.t.illegal[n] += 1
        return ('check', None) if s.to_call <= 0 else ('fold', None)
