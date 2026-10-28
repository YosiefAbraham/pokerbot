"""Decisions: a pure function of (game state, opponent profiles).

No hidden state lives here, so every rule can be unit-tested on a hand-built
GameState. All memory lives in profiles.py.
"""
import random
import zlib

from cards import (evaluate, full_deck, rank, suit, straight_high,
                   HIGH, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH)
from protocol import AGGRESSIVE, FOLD, CHECK, CALL, RAISE
from ranges import expand, hand_key, top, GROUP, PERCENTILE

# ---- Preflop: your philosophy, by position -----------------------------------
BASE = expand('22+, AT+')          # pocket pairs, AK/AQ/AJ/AT: 142 combos, ~10.7%
SMALL_PAIRS = expand('22-77')      # set-mining hands: see the flop cheaply, continue only with a set
EARLY_TIGHT = expand('88+, AQ+')
EARLY_WIDE = top(0.15) - SMALL_PAIRS              # when everyone behind folds to raises
EARLY_WIDE_FOLD = 0.75                            # ...more often than this
OPEN = {
    'early':  expand('88+, AJ+, KQs'),     # only with a station still to act, else EARLY_TIGHT
    'middle': expand('88+, AJ+'),
    'late':   BASE - SMALL_PAIRS,
    'sb':     BASE - SMALL_PAIRS,
    'bb':     BASE - SMALL_PAIRS,
}
STEAL = expand('KQ, KJs, QJs, A2s-A9s')
STEAL_WIDE = STEAL | expand('KTs, QTs, JTs, T9s, 98s, KJo, A7o-A9o')
VS_LOOSE = top(0.27)               # play ~27% of hands once a loose player is in the pot
STEAL_SEAT = {'CO': top(0.27), 'BTN': top(0.35)}   # folded to us: raise to take the blinds

THREE_BET = expand('QQ+, AK')
CALL_RAISE = expand('TT-JJ, AQ')
THREE_BET_VS_NIT = expand('KK+')
CALL_RAISE_VS_NIT = expand('TT-QQ, AK')
THREE_BET_VS_LOOSE = expand('99+, AJ+, KQs')      # vs maniacs, loose bots and tilters
CALL_RAISE_VS_LOOSE = VS_LOOSE - THREE_BET_VS_LOOSE
STACK_OFF_VS_LOOSE = expand('88+, AT+, KQs')      # when a loose raise is huge
BB_DEFEND = BASE | expand('A2s-A9s, KQ, KJs, QJs, JTs')
FOUR_BET = expand('KK+, AKs')
CALL_3BET = expand('QQ, AKo')
FOUR_BET_VS_LOOSE = expand('TT+, AQ+')
CALL_3BET_VS_LOOSE = expand('88-99, AJs, KQs')

POT_CAP = 2             # never add more than 2x the pot in one raise...
UNCAPPED = expand('AA, KK, AKs')   # ...unless re-raising with one of these

SET_MINE_MULT = 10     # call with a small pair only if the stack behind is 10x the call
BIG_RAISE_BB = 20       # a raise this big (in big blinds) is a stack-off decision


def decide(s, book):
    if s.street == 'preflop':
        return legalize(preflop(s, book), s)
    action = legalize(postflop(s, book), s)
    if goes_all_in(action, s) and not all_in_ok(s, book):
        if s.committed + s.to_call >= effective_to(s):
            return FOLD                              # calling would be all-in: let it go
        return CALL if s.to_call > 0 else CHECK
    return action


def effective_to(s):
    """The largest street total that can actually be called: ours, capped by the deepest
    opponent still in (their chips in this street plus behind). With unknown stacks, ours."""
    caps = [s.bets.get(n, 0) + s.stacks[n] for n in s.opponents if n in s.stacks]
    return min(s.max_raise_to, max(caps)) if caps else s.max_raise_to


def goes_all_in(a, s):
    """True if the action commits the effective stack: ours, or the deepest opponent's."""
    full = effective_to(s)
    return ((a[0] == 'raise' and a[1] >= full)
            or (a[0] == 'call' and s.committed + s.to_call >= full))


def all_in_ok(s, book):
    """Postflop all-in only with trips or better, or with one or two pair when every
    opponent left is loose (their ranges are wide, so a pair is often best)."""
    cat, bcat = evaluate(s.hole + s.board)[0], evaluate(s.board)[0]
    if cat <= bcat:
        return False                                 # the board plays
    if cat >= TRIPS:
        return True
    return all(book.get(n).is_loose(book.hand_no) for n in s.opponents)


def raise_cap(s):
    """Highest 'raise to' allowed: at most POT_CAP x the pot added in one raise,
    except an AA / KK / AKs re-raise (preflop 3-bet+, postflop raise of a raise)."""
    aggr = [e for e in s.history if e.street == s.street and e.kind in AGGRESSIVE]
    reraise = len(aggr) >= (1 if s.street == 'preflop' else 2)
    if reraise and hand_key(*s.hole) in UNCAPPED:
        return s.max_raise_to
    return min(s.max_raise_to, s.committed + POT_CAP * s.pot)


def legalize(a, s):
    """Turn any intended action into a legal one. Never fold for free."""
    kind = a[0]
    if kind == 'raise':
        full = effective_to(s)                  # raising past what anyone can call is pointless
        cap = min(raise_cap(s), full)
        if cap <= s.committed + s.to_call or cap < s.min_raise_to:   # no legal raise fits
            return CALL if s.to_call > 0 else CHECK
        to = min(max(a[1], s.min_raise_to), cap)
        if full - to < 0.3 * to and cap == full:                  # nearly all-in: shove
            to = full
        return RAISE(to)
    if kind == 'check' and s.to_call > 0:
        return FOLD
    if kind in ('call', 'fold') and s.to_call == 0:
        return CHECK
    return a


# ---- Preflop -------------------------------------------------------------------
PREFLOP_ORDER = ('UTG', 'UTG1', 'MP', 'HJ', 'CO', 'BTN', 'SB', 'BB')


def _behind(s):
    """Opponents still to act after us preflop, nearest first."""
    me = PREFLOP_ORDER.index(s.position)
    return sorted((n for n in s.opponents if PREFLOP_ORDER.index(s.positions[n]) > me),
                  key=lambda n: PREFLOP_ORDER.index(s.positions[n]))


def steal_range(s, book):
    blinds = [n for n, p in s.positions.items()
              if p in ('SB', 'BB') and n != s.me and n in s.opponents]
    profs = [book.get(n) for n in blinds]
    if any(p.kind() == 'station' for p in profs):
        return frozenset()                  # stations don't fold: don't steal from them
    if profs and all(p.value('fold_to_raise_pf') > 0.65 for p in profs):
        return STEAL_WIDE
    return STEAL


def preflop(s, book):
    key = hand_key(*s.hole)
    grp = GROUP[s.position]
    bb = s.big_blind
    acts = [e for e in s.history if e.street == 'preflop' and e.kind != 'blind']
    raises = [e for e in acts if e.kind in AGGRESSIVE]
    level = s.committed + s.to_call         # the bet we'd be matching

    if not raises:                           # unopened or limped pot
        limpers = [e.player for e in acts if e.kind == 'call']
        loose_limpers = sum(book.get(p).is_loose(book.hand_no) for p in limpers)
        size = bb * (4 + len(limpers) + loose_limpers)   # isolate: bigger vs more/looser limpers
        if grp == 'early':
            behind = _behind(s)
            kinds = [book.get(n).kind() for n in behind]
            maniac_next = bool(kinds) and kinds[0] == 'maniac'
            wide = 'station' in kinds and not maniac_next   # a caller behind pays off wider opens
            if key in (OPEN['early'] if wide else EARLY_TIGHT):
                return RAISE(size)
            if (not limpers and not maniac_next and behind and key in EARLY_WIDE and all(
                    book.get(n).value('fold_to_raise_pf') > EARLY_WIDE_FOLD for n in behind)):
                return RAISE(size)                          # everyone behind folds: open wider
        elif key in OPEN[grp]:
            return RAISE(size)
        if not limpers and key in STEAL_SEAT.get(s.position, ()):
            return RAISE(size)                      # last or second-to-last to act: steal
        if key in SMALL_PAIRS:
            return CALL                             # limp to set-mine, from any seat
        if loose_limpers and key in VS_LOOSE:
            return RAISE(size)                      # a donor is in: isolate it
        if not limpers and grp == 'sb' and key in steal_range(s, book):
            return RAISE(size)
        return CHECK if s.to_call == 0 else FOLD

    raiser = book.get(raises[-1].player)
    nit = raiser.kind() == 'nit'
    loose = raiser.kind() in ('maniac', 'loose') or raiser.is_tilting(book.hand_no)
    eff = effective_to(s)                    # what we can actually win or lose, not just our stack

    if key in SMALL_PAIRS:                   # set-mine any raise the stack behind can pay off
        return CALL if s.to_call * SET_MINE_MULT <= eff else FOLD

    if loose and s.to_call > BIG_RAISE_BB * bb:   # a loose shove: call with what beats junk
        return CALL if key in STACK_OFF_VS_LOOSE else FOLD

    if len(raises) == 1:                     # facing a single raise
        if nit:
            three, call = THREE_BET_VS_NIT, CALL_RAISE_VS_NIT
        elif loose:
            three, call = THREE_BET_VS_LOOSE, CALL_RAISE_VS_LOOSE
        else:
            three, call = THREE_BET, CALL_RAISE
        if key in three:
            return RAISE(level * (3.5 if grp in ('sb', 'bb') else 3))
        if key in call:
            return CALL
        if len(key) == 2 and s.to_call * SET_MINE_MULT <= eff:   # set-mine 88-99 too
            return CALL
        if s.position == 'BB' and key in BB_DEFEND and s.to_call <= 2.5 * bb:
            return CALL
        return FOLD

    # facing a re-raise
    four, call = (FOUR_BET_VS_LOOSE, CALL_3BET_VS_LOOSE) if loose else (FOUR_BET, CALL_3BET)
    if key in four:
        return RAISE(s.max_raise_to)
    if key in call and s.to_call <= 0.35 * eff:
        return CALL
    return FOLD


# ---- Postflop hand buckets -----------------------------------------------------
def _one_pair_bucket(pair_rank, hole_ranks, top, pocket):
    if pocket:
        return 'strong' if pair_rank > top else 'medium'          # overpair vs underpair
    if pair_rank == top:
        kicker = hole_ranks[1] if hole_ranks[0] == pair_rank else hole_ranks[0]
        return 'strong' if kicker >= 10 else 'medium'             # top pair, good kicker
    return 'medium'


def _flush_draw(hole, board):
    for st in {suit(c) for c in hole}:
        if sum(suit(c) == st for c in hole + board) == 4:
            return True
    return False


def _open_ended(hole, board):
    ranks = {rank(c) for c in hole + board}
    hole_ranks = {rank(c) for c in hole}
    if 14 in ranks:
        ranks.add(1)
    if 14 in hole_ranks:
        hole_ranks.add(1)
    outs = set()
    for low in range(1, 11):
        window = set(range(low, low + 5))
        present = window & ranks
        if len(present) == 4 and present & hole_ranks:
            outs |= window - present
    return len(outs) >= 2          # two different completing ranks: 8 outs


def classify(hole, board):
    """monster | strong | medium | draw | air. Only counts what the hole cards add."""
    mine, board_val = evaluate(hole + board), evaluate(board)
    cat, bcat = mine[0], board_val[0]
    hr = sorted((rank(c) for c in hole), reverse=True)
    top = max(rank(c) for c in board)
    pocket = hr[0] == hr[1]

    if cat > bcat:
        if cat >= STRAIGHT:
            return 'monster'
        if cat == TRIPS:
            return 'monster' if pocket else 'strong'               # set vs trips
        if cat == TWO_PAIR and bcat == HIGH:
            return 'monster' if hr[0] == top else 'strong'          # both hole cards used
        if cat == TWO_PAIR:                                        # our pair + board pair
            our = mine[1] if mine[2] == board_val[1] else mine[2]
            return _one_pair_bucket(our, hr, top, pocket)
        if cat == PAIR:
            return _one_pair_bucket(mine[1], hr, top, pocket)
    elif pocket and hr[0] > top and mine > board_val:
        return 'medium'                                            # overpair on a paired board

    if len(board) < 5 and cat < STRAIGHT and (_flush_draw(hole, board) or _open_ended(hole, board)):
        return 'draw'
    return 'air'


# ---- Equity: our outs vs. theirs, by simulation -----------------------------------
EQUITY_SAMPLES = 300
MIN_RANGE = 0.05        # even a nit's range is at least the top 5% of hands
EQUITY_MARGIN = 0.05    # extra equity wanted vs a bettor who isn't known to bluff


def _deal_from_range(rng, deck, used, pct):
    """Two cards from deck, not in used, drawn from the top pct of starting hands."""
    for _ in range(40):
        a, b = rng.sample(deck, 2)
        if a not in used and b not in used and PERCENTILE[hand_key(a, b)] <= pct:
            return a, b
    while True:                         # range too narrow for this board: take anything
        a, b = rng.sample(deck, 2)
        if a not in used and b not in used:
            return a, b


def equity(hole, board, ranges, samples=EQUITY_SAMPLES):
    """Share of the pot we'd win at showdown against opponents holding hands from
    their ranges (top-percentile of starting hands each), running out the board.
    Counts our improving cards and every card that improves them. Seeded by the
    cards, so it's deterministic."""
    if not ranges:
        return 1.0
    rng = random.Random(zlib.crc32(''.join(hole + board).encode()))
    deck = [c for c in full_deck() if c not in hole and c not in board]
    need = 5 - len(board)
    won = 0.0
    for _ in range(samples):
        used = set()
        opp_holes = []
        for pct in ranges:
            h = _deal_from_range(rng, deck, used, max(MIN_RANGE, pct))
            used.update(h)
            opp_holes.append(h)
        rest = [c for c in rng.sample(deck, need + 2 * len(ranges) + 2) if c not in used][:need]
        full = board + rest
        mine = evaluate(hole + full)
        best = max(evaluate(list(h) + full) for h in opp_holes)
        if mine > best:
            won += 1
        elif mine == best:
            ties = 1 + sum(evaluate(list(h) + full) == mine for h in opp_holes)
            won += 1 / ties
    return won / samples


# ---- Postflop decisions --------------------------------------------------------
def bet(s, frac):
    return RAISE(s.committed + max(s.big_blind, int(frac * s.pot)))


def postflop(s, book):
    b = classify(s.hole, s.board)
    if hand_key(*s.hole) in SMALL_PAIRS and b != 'monster':
        return CHECK if s.to_call == 0 else FOLD     # set-mining: no set, no chips
    street = s.street
    opps = [book.get(n) for n in s.opponents]
    stations = any(p.kind() == 'station' for p in opps)
    big = stations and len(opps) <= 2                # heads-up or 3-way with a station
    ftb = min((p.value('fold_to_bet_' + street) for p in opps), default=0.5)
    bluff_ok = (not stations and len(opps) <= 2
                and ftb > (0.65 if street == 'river' else 0.55))

    heads_up_vs = opps[0].kind() if len(opps) == 1 else None

    if s.to_call == 0:
        if b in ('monster', 'strong') and heads_up_vs == 'maniac' and street != 'river':
            return CHECK                                # let the maniac bet for us, then raise
        if b == 'monster':
            return bet(s, 1.5 if big else 1.0 if stations else 0.75)   # stations pay: bet bigger
        if b == 'strong':
            return bet(s, 1.0 if big else 0.75 if stations else 0.6)
        if b == 'draw' and bluff_ok:
            return bet(s, 0.5)                          # semi-bluff folders
        return CHECK                                    # medium hands and air never bet

    # Facing a bet, weaker hands continue only if their outs (equity) pay the price.

    bets = [e for e in s.history if e.street == street and e.kind in AGGRESSIVE]
    bettor = book.get(bets[-1].player) if bets else None
    bet_frac = s.to_call / max(1, s.pot - s.to_call)
    price = s.to_call / (s.pot + s.to_call)               # pot odds
    read = bettor.sizing_read(bet_frac) if bettor else None
    bluffy = bettor is not None and (
        bettor.kind() == 'maniac' or bettor.value('river_bluff') > 0.35
        or bettor.is_tilting(book.hand_no) or read == 'weak')
    tight = bettor is not None and (bettor.kind() == 'nit' or read == 'strong')

    if b == 'monster':
        return RAISE(3 * (s.committed + s.to_call))
    if tight and bet_frac >= 0.75 and street in ('turn', 'river'):
        return FOLD                                      # a nit's big late bet is real
    eq = equity(s.hole, s.board, [book.get(n).value('vpip') for n in s.opponents])
    return CALL if eq >= price + (0 if bluffy else EQUITY_MARGIN) else FOLD
