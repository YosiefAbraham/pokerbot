"""GTO: a balanced, unexploitative bot in the style of solver play.

An approximation; true GTO for 8-handed no-limit can't be computed. It ignores who
it's playing (no reads) and aims to be hard to exploit instead:
  - Preflop: position-based opening ranges, polarized 3-bets (value plus a few
    suited-ace / suited-connector bluffs), and mixed play at the edge of each range.
  - Postflop: estimates equity against the ranges opponents' preflop actions imply,
    bets for value above a threshold, bluffs the weakest hands at the frequency that
    balances its bet size, and defends a bet when its equity beats the price.
Randomness is seeded, so runs are reproducible.
"""
import random
import zlib

from protocol import AGGRESSIVE, CALL, CHECK, FOLD, RAISE
from ranges import PERCENTILE, hand_key
from strategy import equity

RFI = {'UTG': 0.12, 'UTG1': 0.14, 'MP': 0.17, 'HJ': 0.21, 'CO': 0.28, 'BTN': 0.45, 'SB': 0.40}
POSTFLOP_ORDER = ('SB', 'BB', 'UTG', 'UTG1', 'MP', 'HJ', 'CO', 'BTN')   # last acts last
MIX = 0.03                 # hands this close to a range's edge are played half the time
BLUFF_3BET = {'A5s', 'A4s', 'A3s', 'A2s', 'K9s', 'QTs', 'J9s', 'T8s', '97s', '86s', '76s', '65s', '54s'}
SIZES = {'flop': 0.33, 'turn': 0.66, 'river': 0.75}    # bet sizes, fraction of the pot
SAMPLES = 150              # equity simulation runs per decision


class GTOBot:
    """Balanced, solver-style play: mixed ranges, balanced bluffs, no reads."""

    def __init__(self, name, seed=None):
        self.name = name
        self.rng = random.Random(zlib.crc32(name.encode()) if seed is None else seed)

    def new_hand(self, hand_id, positions, hole):
        pass

    def observe(self, event):
        pass

    def end_hand(self, nets, shown, board):
        pass

    def act(self, s):
        a = self._preflop(s) if s.street == 'preflop' else self._postflop(s)
        if a[0] in ('fold', 'call') and s.to_call == 0:
            return CHECK                                      # never fold, or "call" nothing, for free
        if a[0] == 'check' and s.to_call > 0:
            return FOLD
        return a

    # -- helpers -------------------------------------------------------------------
    def _in(self, key, width):
        """In the top `width` of hands, mixing 50/50 near the edge."""
        p = PERCENTILE[key]
        if p <= width - MIX:
            return True
        return p <= width and self.rng.random() < 0.5

    def _raise(self, s, to):
        if s.max_raise_to <= s.committed + s.to_call:
            return CALL if s.to_call else CHECK
        to = min(max(int(to), s.min_raise_to), s.max_raise_to)
        if s.max_raise_to - to < 0.25 * to:
            to = s.max_raise_to
        return RAISE(to)

    def _bet(self, s, frac):
        return self._raise(s, s.committed + max(s.big_blind, frac * s.pot))

    def _in_position(self, s, other):
        order = POSTFLOP_ORDER
        mine, theirs = s.position, s.positions.get(other)
        return theirs in order and mine in order and order.index(mine) > order.index(theirs)

    def _ranges(self, s):
        """Each opponent's range width, from their preflop actions only (no reads)."""
        pf = [e for e in s.history if e.street == 'preflop' and e.kind != 'blind']
        out = []
        for n in s.opponents:
            acts = [e for e in pf if e.player == n]
            raises = sum(e.kind in AGGRESSIVE for e in acts)
            if raises >= 2 or (raises and any(e.kind in AGGRESSIVE for e in pf[:pf.index(acts[0])])):
                out.append(0.06)                              # re-raised: a narrow range
            elif raises:
                out.append(RFI.get(s.positions.get(n), 0.2))  # opened from its seat
            elif acts:
                out.append(0.30)                              # called
            else:
                out.append(0.45)                              # checked its big blind
        return out

    # -- preflop -------------------------------------------------------------------
    def _preflop(self, s):
        key, bb, pos = hand_key(*s.hole), s.big_blind, s.position
        acts = [e for e in s.history if e.street == 'preflop' and e.kind != 'blind']
        raises = [e for e in acts if e.kind in AGGRESSIVE]
        limpers = [e for e in acts if e.kind == 'call']
        level = s.committed + s.to_call

        if not raises:
            if s.to_call == 0:                                # limped around to the big blind
                return self._raise(s, bb * (3 + len(limpers))) if self._in(key, 0.15) else CHECK
            width = RFI.get(pos, 0.2) * (0.75 if limpers else 1)
            if self._in(key, width):
                return self._raise(s, (3 if pos == 'SB' else 2.5) * bb + bb * len(limpers))
            return FOLD

        opener = raises[0].player
        w = RFI.get(s.positions.get(opener), 0.2)
        if s.to_call > 30 * bb:                               # shove-sized: call on equity alone
            eq = equity(s.hole, s.board, [0.08 if len(raises) > 1 else w * 0.5], SAMPLES)
            return CALL if eq >= s.to_call / (s.pot + s.to_call) else FOLD

        if len(raises) == 1:
            ip = self._in_position(s, opener)
            if self._in(key, w * 0.22) or (key in BLUFF_3BET and self.rng.random() < 0.35):
                return self._raise(s, level * (3 if ip else 4))
            call_w = w * (0.75 if ip else 0.4)
            if pos == 'BB':                                   # the big blind gets a price to defend wide
                call_w = max(call_w, 0.35 if w >= 0.28 else 0.25)
            return CALL if self._in(key, call_w) else FOLD

        if len(raises) == 2 and opener == s.me:               # our open got 3-bet
            if self._in(key, 0.035) or (key in ('A5s', 'A4s') and self.rng.random() < 0.3):
                return self._raise(s, level * 2.3)
            return CALL if self._in(key, RFI.get(pos, 0.2) * 0.4) else FOLD

        if self._in(key, 0.02):                               # 4-bets and beyond: premiums only
            return self._raise(s, s.max_raise_to)
        return CALL if self._in(key, 0.035) else FOLD

    # -- postflop ------------------------------------------------------------------
    def _postflop(self, s):
        ranges = self._ranges(s)
        heads_up = len(s.opponents) == 1
        frac = SIZES.get(s.street, 0.66)

        if s.to_call == 0:
            eq = equity(s.hole, s.board, ranges, SAMPLES)
            if eq >= (0.62 if heads_up else 0.72):
                if eq > 0.85 and s.street != 'river' and self.rng.random() < 0.15:
                    return CHECK                              # sometimes slow-play the nuts
                return self._bet(s, frac)
            if heads_up and eq < 0.25 and self.rng.random() < 0.85 * frac / (1 + frac):
                return self._bet(s, frac)                     # bluff at the balancing frequency
            if heads_up and s.street != 'river' and 0.3 <= eq < 0.45 and self.rng.random() < 0.3:
                return self._bet(s, frac)                     # semi-bluff some draws
            return CHECK

        bet_frac = s.to_call / max(1, s.pot - s.to_call)
        narrowed = [r * (0.55 if bet_frac <= 0.5 else 0.45) for r in ranges]   # bettors are stronger
        eq = equity(s.hole, s.board, narrowed, SAMPLES)
        price = s.to_call / (s.pot + s.to_call)
        if eq >= 0.8 or (eq >= 0.7 and self.rng.random() < 0.5):
            return self._raise(s, 3 * (s.committed + s.to_call))
        if eq >= price + MIX or (eq >= price - MIX and self.rng.random() < 0.5):
            return CALL
        return FOLD
