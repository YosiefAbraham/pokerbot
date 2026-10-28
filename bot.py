"""The bot: wires profiling (observe) to decisions (act) and never crashes.

Lifecycle the dealer/adapter drives, per hand:
    new_hand(hand_id, positions, hole)
    observe(event)            for every action at the table, including ours
    act(state) -> action      when it's our turn
    end_hand(nets, shown, board)
"""
import sys
import time
import traceback

from protocol import AGGRESSIVE, CHECK, FOLD
from profiles import TILT_WINDOW, ProfileBook
from ranges import GROUP
from strategy import classify, decide

NAME = 'YOSIEF'   # our bot's name at the table; change it here and everything follows
SWING_BB = 20     # a hand won or lost by this many big blinds counts as a "big swing"


class _HandLog:
    def __init__(self, positions=None, hole=None):
        self.positions = positions or {}
        self.hole = hole or []
        self.vpip, self.pfr, self.river_aggr = set(), set(), set()
        self.pf_raised = False
        self.max_frac = {}        # name -> biggest postflop bet (fraction of pot)


class TightProfiler:
    """Yosief: plays tight, profiles every opponent, and exploits loose ones."""

    def __init__(self, name=NAME, big_blind=100, verbose=False):
        self.name = name
        self.big_blind = big_blind
        self.book = ProfileBook()
        self.h = _HandLog()
        self.errors = 0
        self.max_ms = 0.0
        self.verbose = verbose

    # -- safety ------------------------------------------------------------------
    def _log(self):
        self.errors += 1
        if self.verbose:
            traceback.print_exc(file=sys.stderr)

    def act(self, state):
        t0 = time.perf_counter()
        try:
            return decide(state, self.book)
        except Exception:
            self._log()
            return CHECK if state.to_call == 0 else FOLD     # safe default, never crash
        finally:
            self.max_ms = max(self.max_ms, (time.perf_counter() - t0) * 1000)

    def new_hand(self, hand_id, positions, hole):
        try:
            self.book.hand_no += 1
            self.h = _HandLog(positions, hole)
        except Exception:
            self._log()

    def observe(self, ev):
        try:
            self._observe(ev)
        except Exception:
            self._log()

    def end_hand(self, nets, shown, board):
        try:
            self._end_hand(nets, shown, board)
        except Exception:
            self._log()

    # -- profiling -----------------------------------------------------------------
    def _observe(self, ev):
        h, bk = self.h, self.book
        aggressive = ev.kind in AGGRESSIVE
        if ev.kind == 'blind' or ev.player == self.name:
            if ev.street == 'preflop' and aggressive:
                h.pf_raised = True
            return
        name = ev.player
        if ev.street == 'preflop':
            if h.pf_raised:
                bk.record(name, 'fold_to_raise_pf', ev.kind == 'fold')
            if ev.kind in ('call', 'bet', 'raise'):
                h.vpip.add(name)
            if aggressive:
                h.pfr.add(name)
                h.pf_raised = True
            return
        if ev.to_call_before > 0:
            folded = ev.kind == 'fold'
            bk.record(name, 'fold_to_bet', folded)
            bk.record(name, 'fold_to_bet_' + ev.street, folded)
        bk.record(name, 'agg', aggressive)
        if aggressive:
            frac = (ev.amount - ev.to_call_before) / max(1, ev.pot_before + ev.to_call_before)
            h.max_frac[name] = max(h.max_frac.get(name, 0.0), frac)
            if ev.street == 'river':
                h.river_aggr.add(name)

    def _end_hand(self, nets, shown, board):
        h, bk, now = self.h, self.book, self.book.hand_no
        for name, pos in h.positions.items():
            if name == self.name:
                continue
            p = bk.get(name)
            p.hands += 1
            played = name in h.vpip
            bk.record(name, 'vpip', played)
            bk.record(name, 'vpip_' + GROUP[pos], played)
            bk.record(name, 'pfr', name in h.pfr)
            if 1 <= now - p.last_swing_hand <= TILT_WINDOW:
                bk.record(name, 'vpip_after_swing', played)

        for name, net in nets.items():
            if name != self.name and abs(net) >= SWING_BB * self.big_blind:
                bk.get(name).last_swing_hand = now

        if len(board) >= 3:
            for name, hole in shown.items():
                if name == self.name:
                    continue
                bucket = classify(hole, board)
                p = bk.get(name)
                if name in h.max_frac:
                    strong = bucket in ('monster', 'strong')
                    p.sizing['strong' if strong else 'weak'].append(h.max_frac[name])
                if name in h.river_aggr:
                    bk.record(name, 'river_bluff', bucket in ('air', 'draw'))
