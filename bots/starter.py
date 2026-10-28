"""STARTER: a simple bot to copy when you build your own.

    cp bots/starter.py bots/yourname.py     then change NAME below and edit act()

The dashboard, simulator and diagnostics find every file in bots/ automatically.
Before you push, run `uv run python diagnostics.py`: it checks that every action
your bot returns is legal and quick.

The dealer calls four methods (all must return quickly; act() most of all):
    new_hand(hand_id, positions, hole)  hole like ['As', 'Kd']; positions {name: 'BTN', ...}
    observe(event)                      every action at the table, yours included (protocol.Event)
    act(state) -> action                state is a protocol.GameState; return FOLD, CHECK, CALL
                                        or RAISE(to), where `to` is your total bet for this street
    end_hand(nets, shown, board)        chips won or lost per bot; cards shown at showdown

Anything illegal (checking when you owe chips, a bad raise size, an exception) is
turned into a fold, or a check if it's free, and counted against you.

Helpers you can use: cards.evaluate / rank / suit, ranges.expand / hand_key / top,
protocol (Event, GameState, FOLD, CHECK, CALL, RAISE). Yosief's own code (strategy.py,
profiles.py) is open too, but writing your own ideas is the point.
"""
from cards import PAIR, TWO_PAIR, evaluate
from protocol import CALL, CHECK, FOLD, RAISE
from ranges import expand, hand_key

NAME = 'STARTER'

OPEN = expand('77+, ATs+, AJo+, KQs')


def raise_to(s, amount):
    """A legal raise as close to `amount` as allowed, or a call/check if raising isn't."""
    if s.max_raise_to <= s.committed + s.to_call:
        return CALL if s.to_call else CHECK
    return RAISE(min(max(int(amount), s.min_raise_to), s.max_raise_to))


class Bot:
    """Simple and tight: raises good hands, bets made hands, folds the rest."""

    def __init__(self, name):
        self.name = name

    def new_hand(self, hand_id, positions, hole):
        pass

    def observe(self, event):
        pass

    def end_hand(self, nets, shown, board):
        pass

    def act(self, s):
        if s.street == 'preflop':
            if hand_key(*s.hole) in OPEN:
                return raise_to(s, 3 * (s.committed + s.to_call))
            return CHECK if s.to_call == 0 else FOLD

        mine, board = evaluate(s.hole + s.board)[0], evaluate(s.board)[0]
        if mine >= TWO_PAIR and mine > board:                 # two pair or better, ours
            return raise_to(s, s.committed + s.to_call + 0.66 * s.pot)
        if mine == PAIR and mine > board:                     # one pair: call small bets
            if s.to_call == 0:
                return CHECK
            return CALL if s.to_call <= 0.5 * s.pot else FOLD
        return CHECK if s.to_call == 0 else FOLD
