"""Hand keys ('AKs', 'AKo', 'TT'), range strings, and seat positions."""
from cards import RANKS, RANK_VALUE, rank, suit


def _r(v):
    return RANKS[v - 2]


def hand_key(c1, c2):
    hi, lo = sorted((c1, c2), key=rank, reverse=True)
    if rank(hi) == rank(lo):
        return hi[0] * 2
    return hi[0] + lo[0] + ('s' if suit(hi) == suit(lo) else 'o')


def _add(out, key):
    if len(key) == 2:              # 'KQ' means both suited and offsuit
        out.update((key + 's', key + 'o'))
    else:
        out.add(key)


def expand(spec):
    """'88+, AQ+, A2s-A9s, KQ, KJs' -> set of hand keys."""
    out = set()
    for tok in (t.strip() for t in spec.split(',')):
        if not tok:
            continue
        plus = tok.endswith('+')
        tok = tok.rstrip('+')
        if '-' in tok:                                   # explicit span
            a, b = tok.split('-')
            if a[0] == a[1]:                             # '22-77'
                lo, hi = sorted((RANK_VALUE[a[0]], RANK_VALUE[b[0]]))
                out.update(_r(v) * 2 for v in range(lo, hi + 1))
            else:                                        # 'A2s-A9s'
                lo, hi = sorted((RANK_VALUE[a[1]], RANK_VALUE[b[1]]))
                for v in range(lo, hi + 1):
                    _add(out, a[0] + _r(v) + a[2:])
        elif tok[0] == tok[1]:                           # 'TT' or 'TT+'
            start = RANK_VALUE[tok[0]]
            out.update(_r(v) * 2 for v in range(start, 15 if plus else start + 1))
        else:                                            # 'AQ', 'AQs+', 'KJo'
            top, start = RANK_VALUE[tok[0]], RANK_VALUE[tok[1]]
            end = top - 1 if plus else start
            for v in range(start, end + 1):
                _add(out, tok[0] + _r(v) + tok[2:])
    return frozenset(out)


def combos(keys):
    return sum(6 if len(k) == 2 else 4 if k[2] == 's' else 12 for k in keys)


def chen(key):
    """Chen formula: a rough preflop strength score for a hand key."""
    hi, lo = RANK_VALUE[key[0]], RANK_VALUE[key[1]]
    score = {14: 10, 13: 8, 12: 7, 11: 6}.get(hi, hi / 2)
    if hi == lo:
        return max(5, score * 2)
    if key[2] == 's':
        score += 2
    gap = hi - lo - 1
    score -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and hi < 12:
        score += 1
    return score


def _all_keys():
    keys = []
    for i, a in enumerate(RANKS):
        for b in RANKS[:i + 1]:
            keys += [a + b] if a == b else [a + b + 's', a + b + 'o']
    return keys


def _percentiles():
    """Hand key -> share of all combos at least this strong (AA ~0.005, 72o 1.0)."""
    out, total = {}, 0
    for key in sorted(_all_keys(), key=chen, reverse=True):
        total += combos([key])
        out[key] = total / 1326
    return out


PERCENTILE = _percentiles()


def top(pct):
    """The strongest pct of starting hands, e.g. top(0.27)."""
    return frozenset(k for k, p in PERCENTILE.items() if p <= pct)


# ---- Positions -------------------------------------------------------------
# Offset 0 is the button. With fewer than 8 players, the earliest seats drop off.
_LATER = ['UTG', 'UTG1', 'MP', 'HJ', 'CO']
GROUP = {'UTG': 'early', 'UTG1': 'early', 'MP': 'middle', 'HJ': 'middle',
         'CO': 'late', 'BTN': 'late', 'SB': 'sb', 'BB': 'bb'}


def position_name(offset, n):
    if n == 2:
        return 'BTN' if offset == 0 else 'BB'
    if offset < 3:
        return ('BTN', 'SB', 'BB')[offset]
    names = _LATER[len(_LATER) - (n - 3):]
    return names[offset - 3]
