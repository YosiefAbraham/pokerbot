"""The shapes every other module agrees on.

Keep any dealer's message format OUT of the strategy: one adapter (adapter.py)
turns dealer messages into these objects and turns our action tuples back into
whatever the dealer expects.
"""
from dataclasses import dataclass, field

STREETS = ('preflop', 'flop', 'turn', 'river')
AGGRESSIVE = ('bet', 'raise')

# Actions are plain tuples. Raise amounts are "raise TO" street totals.
FOLD = ('fold',)
CHECK = ('check',)
CALL = ('call',)


def RAISE(to):
    return ('raise', int(to))


@dataclass
class Event:
    """One action someone took. Every bot sees every event, including its own."""
    player: str
    street: str
    kind: str            # blind | fold | check | call | bet | raise
    amount: int          # chips this action added to the pot
    to: int              # player's total chips in on this street afterwards
    pot_before: int      # pot before this action
    to_call_before: int  # what the player owed before acting


@dataclass
class GameState:
    """Everything the bot needs to make one decision."""
    me: str
    hole: list
    board: list
    street: str
    pot: int             # total pot, including bets on this street
    to_call: int
    min_raise_to: int    # street totals ("raise to"), not increments
    max_raise_to: int    # all-in
    stack: int           # chips behind
    committed: int       # my chips in on this street
    big_blind: int
    position: str
    positions: dict      # name -> position for everyone dealt in
    opponents: list      # opponents still in the hand
    history: list = field(default_factory=list)  # Events so far this hand
    hand_id: int = 0
