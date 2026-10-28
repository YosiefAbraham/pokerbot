"""Opponent profiles that never stop adapting.

Three ideas carry the whole module:
  1. Shrinkage. Each stat is (hits + prior * K) / (opportunities + K). With no
     data you get the prior; with lots of data you get the player.
  2. A hierarchy of priors. A position stat (vpip_late) shrinks toward the
     player's overall stat (vpip), which shrinks toward the field average.
     Small samples borrow strength from the level above.
  3. Decay. Old observations fade (DECAY per update), so a bot that changes
     gears gets re-read.
"""
from collections import defaultdict, deque

DECAY = 0.997           # per-player memory: roughly the last ~330 observations
POP_DECAY = 0.9995      # field average moves more slowly
PRIOR_STRENGTH = 8.0    # how many observations the prior is "worth"
MIN_HANDS_TO_LABEL = 12
TILT_WINDOW = 6         # hands after a big swing that count as "post-swing"
TILT_DELTA = 0.12       # post-swing VPIP this much above normal = tilting
LOOSE_VPIP = 0.33       # plays more than this many hands = loose

# Starting guesses before the field average has any data.
DEFAULTS = {'vpip': 0.30, 'pfr': 0.12, 'fold_to_raise_pf': 0.60,
            'fold_to_bet': 0.50, 'agg': 0.30, 'river_bluff': 0.25}

PARENT = {
    'vpip_early': 'vpip', 'vpip_middle': 'vpip', 'vpip_late': 'vpip',
    'vpip_sb': 'vpip', 'vpip_bb': 'vpip', 'vpip_after_swing': 'vpip',
    'fold_to_bet_flop': 'fold_to_bet', 'fold_to_bet_turn': 'fold_to_bet',
    'fold_to_bet_river': 'fold_to_bet',
}


class Stat:
    __slots__ = ('hits', 'opps')

    def __init__(self):
        self.hits = 0.0
        self.opps = 0.0

    def add(self, hit, decay=DECAY):
        self.hits = self.hits * decay + (1.0 if hit else 0.0)
        self.opps = self.opps * decay + 1.0

    def shrunk(self, prior, k=PRIOR_STRENGTH):
        return (self.hits + prior * k) / (self.opps + k)


class Profile:
    def __init__(self, name, book):
        self.name = name
        self.book = book
        self.stats = defaultdict(Stat)
        self.hands = 0
        self.last_swing_hand = -10 ** 9
        # Bet sizes (fraction of pot) seen at showdown, split by what they held.
        self.sizing = {'strong': deque(maxlen=40), 'weak': deque(maxlen=40)}

    def value(self, stat):
        parent = PARENT.get(stat)
        prior = self.value(parent) if parent else self.book.population_mean(stat)
        return self.stats[stat].shrunk(prior)

    def kind(self):
        """Coarse label the strategy branches on."""
        if self.hands < MIN_HANDS_TO_LABEL:
            return 'unknown'
        vpip, ftb, agg = self.value('vpip'), self.value('fold_to_bet'), self.value('agg')
        if vpip < 0.12:
            return 'nit'
        if agg > 0.45 and vpip > 0.35:
            return 'maniac'
        if vpip > 0.40 and ftb < 0.35:
            return 'station'
        if ftb > 0.60:
            return 'folder'
        if vpip > LOOSE_VPIP:
            return 'loose'
        return 'regular'

    def is_loose(self, hand_no):
        return self.kind() in ('maniac', 'station', 'loose') or self.is_tilting(hand_no)

    def tilt_delta(self):
        return self.value('vpip_after_swing') - self.value('vpip')

    def is_tilting(self, hand_no):
        recent = 0 <= hand_no - self.last_swing_hand <= TILT_WINDOW
        return recent and self.tilt_delta() > TILT_DELTA

    def sizing_read(self, frac):
        """'strong' / 'weak' if this player's bet sizes have told on them before."""
        s, w = self.sizing['strong'], self.sizing['weak']
        if len(s) < 3 or len(w) < 3:
            return None
        ms, mw = sum(s) / len(s), sum(w) / len(w)
        if abs(ms - mw) < 0.25:
            return None
        return 'strong' if abs(frac - ms) < abs(frac - mw) else 'weak'


class ProfileBook:
    def __init__(self):
        self.players = {}
        self.population = defaultdict(Stat)
        self.hand_no = 0

    def get(self, name):
        if name not in self.players:
            self.players[name] = Profile(name, self)
        return self.players[name]

    def record(self, name, stat, hit):
        self.get(name).stats[stat].add(hit)
        self.population[stat].add(hit, POP_DECAY)

    def population_mean(self, stat):
        return self.population[stat].shrunk(DEFAULTS.get(stat, 0.5))

    def summary(self):
        rows = []
        for name, p in sorted(self.players.items()):
            rows.append({
                'name': name, 'kind': p.kind(), 'hands': p.hands,
                'vpip': p.value('vpip'), 'pfr': p.value('pfr'),
                'fold_to_bet': p.value('fold_to_bet'), 'agg': p.value('agg'),
                'river_bluff': p.value('river_bluff'), 'tilt_delta': p.tilt_delta(),
            })
        return rows
