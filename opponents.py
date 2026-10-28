"""Practice opponents, one per common player archetype: a calling station, a
never-folding caller, a nit, a maniac, a passive limper, a tilter, a solid
regular, and a random / buggy bot. Deliberately simple; they exist to check that profiling reads them
and that the strategy beats them."""
import random

from gto import GTOBot

from cards import rank, suit
from protocol import CALL, CHECK, FOLD, RAISE
from ranges import expand, hand_key
from strategy import classify


def pf_strength(hole):
    r1, r2 = sorted((rank(c) for c in hole), reverse=True)
    s = (r1 - 2) / 12 * 0.5 + (r2 - 2) / 12 * 0.3
    if r1 == r2:
        s += 0.35
    else:
        s -= max(0, r1 - r2 - 1) * 0.03
    if suit(hole[0]) == suit(hole[1]):
        s += 0.06
    return s


def check_or_fold(s):
    return CHECK if s.to_call == 0 else FOLD


def pot_bet(s, frac):
    return RAISE(s.committed + s.to_call + max(s.big_blind, int(frac * (s.pot + s.to_call))))


class Dummy:
    def __init__(self, name, seed=0):
        self.name = name
        self.rng = random.Random(seed)
        self.hole = []

    def new_hand(self, hand_id, positions, hole):
        self.hole = hole

    def observe(self, ev):
        pass

    def end_hand(self, nets, shown, board):
        pass

    def bucket(self, s):
        return classify(self.hole, s.board)


def station_act(b, s):
    if s.street == 'preflop':
        st = pf_strength(b.hole)
        if (s.to_call <= 5 * s.big_blind and st > 0.15) or st > 0.5:
            return CALL
        return check_or_fold(s)
    bucket = b.bucket(s)
    if bucket == 'monster' and b.rng.random() < 0.3:
        return pot_bet(s, 0.5)
    if s.to_call == 0:
        return CHECK
    if bucket == 'air' and s.to_call > 0.75 * s.pot and b.rng.random() < 0.5:
        return FOLD
    return CALL


PREMIUM = expand('QQ+, AK')
MONSTER_PF = expand('KK+')


def nit_act(b, s):
    if s.street == 'preflop':
        key = hand_key(*b.hole)
        if key in MONSTER_PF:
            return RAISE(max(3 * s.big_blind, 3 * (s.committed + s.to_call)))
        if key in PREMIUM:
            return CALL if s.to_call > 3 * s.big_blind else RAISE(3 * s.big_blind)
        return check_or_fold(s)
    bucket = b.bucket(s)
    if bucket in ('monster', 'strong'):
        if s.to_call == 0:
            return pot_bet(s, 0.6)
        return pot_bet(s, 1.0) if bucket == 'monster' else CALL
    if bucket == 'medium' and s.to_call <= 0.3 * s.pot:
        return CALL if s.to_call else CHECK
    return check_or_fold(s)


def maniac_act(b, s):
    r = b.rng.random()
    if s.street == 'preflop':
        if r < 0.45:
            return RAISE((s.committed + s.to_call) * b.rng.choice([2, 3, 4]) + s.big_blind)
        if r < 0.75 and s.to_call <= 4 * s.big_blind:
            return CALL
        return check_or_fold(s)
    if r < 0.6:
        return pot_bet(s, b.rng.uniform(0.5, 1.5))
    if s.to_call == 0:
        return CHECK
    if r < 0.85 or b.bucket(s) != 'air':
        return CALL
    return FOLD


def limper_act(b, s):
    if s.street == 'preflop':
        st = pf_strength(b.hole)
        if st > 0.85:
            return RAISE(3 * (s.committed + s.to_call))
        if s.to_call <= s.big_blind and st > 0.3:
            return CALL
        if s.to_call <= 4 * s.big_blind and st > 0.5:
            return CALL
        return check_or_fold(s)
    bucket = b.bucket(s)
    if bucket == 'monster':
        return pot_bet(s, 0.5) if s.to_call == 0 else CALL
    if s.to_call == 0:
        return CHECK
    if bucket in ('strong', 'medium') or (bucket == 'draw' and s.to_call <= 0.5 * s.pot):
        return CALL
    return FOLD


def regular_act(b, s):
    bb = s.big_blind
    if s.street == 'preflop':
        st = pf_strength(b.hole)
        if s.to_call <= bb - s.committed or s.to_call <= bb:      # unraised
            return RAISE(3 * bb) if st > 0.55 else check_or_fold(s)
        if st > 0.8:
            return RAISE(3 * (s.committed + s.to_call))
        if st > 0.62 and s.to_call <= 0.08 * s.stack:
            return CALL
        return FOLD
    bucket = b.bucket(s)
    if s.to_call == 0:
        if bucket in ('monster', 'strong'):
            return pot_bet(s, 0.66)
        if s.street == 'flop' and b.rng.random() < 0.6:
            return pot_bet(s, 0.5)
        return CHECK
    if bucket == 'monster':
        return RAISE(3 * (s.committed + s.to_call))
    if bucket == 'strong':
        return CALL
    if bucket == 'medium' and s.to_call <= 0.4 * s.pot:
        return CALL
    if bucket == 'draw' and s.to_call <= 0.35 * s.pot:
        return CALL
    return FOLD


class Station(Dummy):
    act = station_act


class Nit(Dummy):
    act = nit_act


class Maniac(Dummy):
    act = maniac_act


class Limper(Dummy):
    act = limper_act


class Regular(Dummy):
    act = regular_act


class Tilter(Dummy):
    """Plays like a regular until it loses a big pot, then spews for 8 hands."""
    def __init__(self, name, seed=0):
        super().__init__(name, seed)
        self.tilt = 0

    def new_hand(self, hand_id, positions, hole):
        super().new_hand(hand_id, positions, hole)
        self.tilt = max(0, self.tilt - 1)

    def end_hand(self, nets, shown, board):
        if nets.get(self.name, 0) <= -2000 and not self.tilt:
            self.tilt = 8

    def act(self, s):
        return maniac_act(self, s) if self.tilt else regular_act(self, s)


class RiverKing(Dummy):
    """Never folds, never raises: checks whenever it can and calls whenever it must,
    every street through the river, whatever it holds."""
    def act(self, s):
        return CHECK if s.to_call == 0 else CALL


class RandomBot(Dummy):
    """Random legal-ish actions, plus garbage and exceptions, to test robustness."""
    def act(self, s):
        r = self.rng.random()
        if r < 0.02:
            raise RuntimeError('buggy bot')
        if r < 0.04:
            return ('banana',)
        if r < 0.30:
            return FOLD
        if r < 0.55:
            return CHECK
        if r < 0.85:
            return CALL
        return RAISE(self.rng.randint(0, s.max_raise_to + 500))


# Every practice bot: name -> (class, seed offset, one-line description).
ROSTER = {
    'RIVERKING': (RiverKing, 1, 'Never folds or raises: checks or calls through the river'),
    'NIT': (Nit, 2, 'Plays only QQ+ and AK'),
    'MANIAC': (Maniac, 3, 'Raises 45% preflop, bets 60% postflop'),
    'LIMPER': (Limper, 4, 'Loose and passive: limps and calls'),
    'TILTER': (Tilter, 5, 'A regular that spews for 8 hands after losing 20bb'),
    'REGULAR': (Regular, 6, 'Solid tight-aggressive'),
    'RANDOM': (RandomBot, 7, 'Random actions, plus garbage replies and exceptions'),
    'STATION': (Station, 8, 'Calls almost everything'),
    'GTO': (GTOBot, 9, 'Balanced, solver-style play: mixed ranges, balanced bluffs, no reads'),
}
MAX_OPPONENTS = 7                    # tables seat 8, our bot included
DEFAULT_FIELD = ('RIVERKING', 'NIT', 'MANIAC', 'LIMPER', 'TILTER', 'REGULAR', 'RANDOM')


def default_field(seed=0, names=DEFAULT_FIELD):
    return [ROSTER[n][0](n, seed + ROSTER[n][1]) for n in names]
