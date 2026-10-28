"""Cards and hand evaluation. A card is a 2-char string: 'As', 'Td', '9c'."""
from collections import Counter

RANKS = '23456789TJQKA'
SUITS = 'cdhs'
RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}

(HIGH, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH,
 FULL_HOUSE, QUADS, STRAIGHT_FLUSH) = range(9)

_SUIT_ALIASES = {'︎': '', '️': '', '♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}


def parse_card(text):
    """Normalize dealer formats: '10h', 'TH', 'A♠︎' -> 'Th', 'Th', 'As'."""
    t = text.strip()
    for k, v in _SUIT_ALIASES.items():
        t = t.replace(k, v)
    t = t.replace('10', 'T')
    r, s = t[0].upper(), t[1].lower()
    if r not in RANK_VALUE or s not in SUITS:
        raise ValueError(f'bad card: {text!r}')
    return r + s


def rank(card):
    return RANK_VALUE[card[0]]


def suit(card):
    return card[1]


def full_deck():
    return [r + s for r in RANKS for s in SUITS]


def straight_high(ranks):
    rs = set(ranks)
    if 14 in rs:
        rs.add(1)  # wheel
    for high in range(14, 4, -1):
        if all(r in rs for r in range(high - 4, high + 1)):
            return high
    return 0


def _groups(ranks):
    counts = Counter(ranks)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    return [g[1] for g in groups], [g[0] for g in groups]


def _best_five(cards):
    ranks = sorted((rank(c) for c in cards), reverse=True)
    by_suit = {}
    for c in cards:
        by_suit.setdefault(suit(c), []).append(rank(c))
    flush = next((sorted(rs, reverse=True) for rs in by_suit.values() if len(rs) >= 5), None)
    if flush and straight_high(flush):
        return (STRAIGHT_FLUSH, straight_high(flush))
    shape, order = _groups(ranks)
    top = order[0]
    if shape[0] == 4:
        return (QUADS, top, max(r for r in ranks if r != top))
    if shape[0] == 3 and shape[1] >= 2:
        return (FULL_HOUSE, top, order[1])
    if flush:
        return (FLUSH, *flush[:5])
    if straight_high(ranks):
        return (STRAIGHT, straight_high(ranks))
    if shape[0] == 3:
        return (TRIPS, top, *[r for r in ranks if r != top][:2])
    if shape[1] == 2:
        p1, p2 = order[0], order[1]
        return (TWO_PAIR, p1, p2, max(r for r in ranks if r not in (p1, p2)))
    if shape[0] == 2:
        return (PAIR, top, *[r for r in ranks if r != top][:3])
    return (HIGH, *ranks[:5])


def evaluate(cards):
    """Hand value as a comparable tuple; first element is the category.
    5-7 cards: best five. Fewer (a 3- or 4-card board): pairs/trips/quads only."""
    cards = list(cards)
    if len(cards) >= 5:
        return _best_five(cards)
    if not cards:
        return (HIGH,)
    shape, order = _groups(rank(c) for c in cards)
    if shape[0] == 4:
        return (QUADS, *order)
    if shape[0] == 3:
        return (TRIPS, *order)
    if shape[:2] == [2, 2]:
        return (TWO_PAIR, *order)
    if shape[0] == 2:
        return (PAIR, *order)
    return (HIGH, *order)
