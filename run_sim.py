"""Run the bot against the practice field and print results plus its opponent reads.

    python run_sim.py                     # 300 games x 20 hands
    python run_sim.py --games 1000 --hands 10 --seed 7
    python run_sim.py --no-memory         # wipe profiles between games
    python run_sim.py --stacks reset      # every stack back to 100bb each hand
"""
import argparse
import math
import random

from bot import NAME
from bots import CONTESTANTS
from engine import Table
from opponents import DEFAULT_FIELD, ROSTER


STACK_MODES = ('carry', 'reset')


def run(games=300, hands=20, seed=0, memory=True, on_hand=None, bots=DEFAULT_FIELD, hero=NAME,
        stacks='carry'):
    """on_hand(game, table, nets) is called after every hand, e.g. to log it.
    hero is the contestant whose results come back as my_hands (a key of bots.CONTESTANTS);
    bots names the opponents: practice bots (opponents.ROSTER) and other contestants.
    stacks='carry' keeps stacks between hands with a 100bb rebuy on busting; 'reset'
    starts every hand at 100bb."""
    if stacks not in STACK_MODES:
        raise ValueError(f'stacks must be one of {STACK_MODES}')
    rng = random.Random(seed)

    def make(name):                              # practice-roster bots get a per-run seed
        if name in ROSTER:
            cls, offset, _ = ROSTER[name]
            return cls(name, seed + offset)
        return CONTESTANTS[name](name)

    def contestants():                           # fresh instances of every contestant seated
        return {n: make(n) for n in (hero, *bots) if n in CONTESTANTS}

    live = contestants()
    field = [live[n] if n in CONTESTANTS else make(n) for n in bots]
    totals = {n: 0 for n in (hero, *bots)}
    my_hands = []
    table = Table([], seed=seed, carry=stacks == 'carry')
    hand_id = 0
    for game in range(games):
        if not memory and game:
            live = contestants()
            field = [live.get(b.name, b) for b in field]
        seats = [live[hero]] + field
        rng.shuffle(seats)
        table.bots = seats
        button = rng.randrange(len(seats))
        for h in range(hands):
            hand_id += 1
            nets = table.play_hand(hand_id, (button + h) % len(seats))
            assert sum(nets.values()) == 0, 'chips not conserved'
            for name, net in nets.items():
                totals[name] += net
            my_hands.append(nets[hero])
            if on_hand:
                on_hand(game, table, nets)
    return live[hero], table, totals, my_hands


def seed_result(job):
    """One seed of a match: every bot's bb/100, finishing place and robustness.
    Lives here (not in the dashboard script) so worker processes can import it."""
    games, hands, seed, memory, bots, hero, stacks = job
    _, table, totals, mine = run(games, hands, seed, memory, bots=bots, hero=hero, stacks=stacks)
    rates = {n: net / 100 / len(mine) * 100 for n, net in totals.items()}
    order = sorted(rates, key=lambda n: -rates[n])
    return {'seed': seed, 'rates': rates, 'place': {n: order.index(n) + 1 for n in rates},
            'max_ms': dict(table.max_ms), 'errors': dict(table.errors), 'illegal': dict(table.illegal),
            'rebuys': dict(table.rebuys)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--games', type=int, default=300)
    ap.add_argument('--hands', type=int, default=20)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--no-memory', action='store_true')
    ap.add_argument('--stacks', choices=STACK_MODES, default='carry')
    args = ap.parse_args()

    me, table, totals, my_hands = run(args.games, args.hands, args.seed, not args.no_memory,
                                      stacks=args.stacks)
    n = len(my_hands)
    bb = 100
    mean = sum(my_hands) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in my_hands) / (n - 1))
    print(f'\n{n} hands ({args.games} games x {args.hands}), seed {args.seed}\n')
    print(f'{"bot":10} {"net chips":>11} {"bb/100":>8} {"rebuys":>7} {"errors":>7} {"illegal":>8} {"max ms":>7}')
    for name, net in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f'{name:10} {net:>11,} {net / bb / n * 100:>8.1f} {table.rebuys[name]:>7} {table.errors[name]:>7} '
              f'{table.illegal[name]:>8} {table.max_ms[name]:>7.1f}')
    se = sd / math.sqrt(n) / bb * 100
    print(f'\n{NAME}: {mean / bb * 100:.1f} bb/100 +/- {1.96 * se:.1f} (95% CI). '
          f'Own caught errors: {me.errors}.')

    print(f'\nWhat {NAME} thinks of each opponent:')
    print(f'{"name":10} {"label":>9} {"vpip":>6} {"pfr":>6} {"f2bet":>6} {"agg":>6} '
          f'{"rvBluff":>8} {"tilt+":>6}')
    for r in me.book.summary():
        print(f'{r["name"]:10} {r["kind"]:>9} {r["vpip"]:>6.2f} {r["pfr"]:>6.2f} '
              f'{r["fold_to_bet"]:>6.2f} {r["agg"]:>6.2f} {r["river_bluff"]:>8.2f} '
              f'{r["tilt_delta"]:>+6.2f}')


if __name__ == '__main__':
    main()
