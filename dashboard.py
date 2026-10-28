"""Local dashboard: run the simulation from a browser and inspect every hand.

    python dashboard.py              # then open http://localhost:8000
    python dashboard.py --port 8080

GET /api/run?games=500&hands=20&seed=0&memory=1&hero=YOSIEF&bots=NIT,MANIAC runs
run_sim.run() and returns one JSON blob: per-bot totals, the hero's opponent reads
(if it keeps any), and a compact log of every hand that the page aggregates itself.
hero is a contestant (bots.CONTESTANTS, default bot.NAME); bots is optional (default:
opponents.DEFAULT_FIELD) and may mix practice bots and other contestants.
GET /api/match?seeds=8&... runs one seed per process and returns standings only.
GET /api/bots lists the roster; GET /api/abe feeds the transparency page.
"""
import argparse
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bot
import profiles
import strategy
from bots import CONTESTANTS, PROBLEMS, about
from opponents import DEFAULT_FIELD, MAX_OPPONENTS, ROSTER
from protocol import STREETS
from ranges import PERCENTILE, chen, combos, hand_key
from run_sim import STACK_MODES, run, seed_result
from strategy import classify

HERE = Path(__file__).parent
KIND_CODE = {'blind': 'b', 'fold': 'f', 'check': 'x', 'call': 'c', 'bet': 'B', 'raise': 'R'}
MAX_HANDS = 50_000          # one run sends every hand to the page, so keep it loadable
MAX_MATCH_HANDS = 200_000   # a match sends only standings; this just bounds the wait
MAX_SEEDS = 16


def _my_buckets(h, me):
    """The hero's postflop bucket on each street it was still in for."""
    folded_on = next((e.street for e in h.history if e.player == me and e.kind == 'fold'), None)
    out = []
    for street, n in (('flop', 3), ('turn', 4), ('river', 5)):
        if len(h.board) < n or (folded_on and STREETS.index(folded_on) < STREETS.index(street)):
            break
        out.append(classify(h.holes[me], h.board[:n]))
    return out


def abe_spec():
    """Everything the transparency page shows that lives in code: every named range
    and tunable constant, read from the modules so the page can't drift from the bot."""
    s = strategy
    named = {
        'BASE': s.BASE, 'SMALL_PAIRS': s.SMALL_PAIRS, 'EARLY_TIGHT': s.EARLY_TIGHT,
        'OPEN_EARLY': s.OPEN['early'], 'OPEN_MIDDLE': s.OPEN['middle'], 'OPEN_LATE': s.OPEN['late'],
        'EARLY_WIDE': s.EARLY_WIDE, 'STEAL_CO': s.STEAL_SEAT['CO'], 'STEAL_BTN': s.STEAL_SEAT['BTN'],
        'STEAL': s.STEAL, 'STEAL_WIDE': s.STEAL_WIDE, 'VS_LOOSE': s.VS_LOOSE,
        'THREE_BET': s.THREE_BET, 'CALL_RAISE': s.CALL_RAISE,
        'THREE_BET_VS_NIT': s.THREE_BET_VS_NIT, 'CALL_RAISE_VS_NIT': s.CALL_RAISE_VS_NIT,
        'THREE_BET_VS_LOOSE': s.THREE_BET_VS_LOOSE, 'CALL_RAISE_VS_LOOSE': s.CALL_RAISE_VS_LOOSE,
        'STACK_OFF_VS_LOOSE': s.STACK_OFF_VS_LOOSE, 'BB_DEFEND': s.BB_DEFEND,
        'FOUR_BET': s.FOUR_BET, 'CALL_3BET': s.CALL_3BET,
        'FOUR_BET_VS_LOOSE': s.FOUR_BET_VS_LOOSE, 'CALL_3BET_VS_LOOSE': s.CALL_3BET_VS_LOOSE,
        'UNCAPPED': s.UNCAPPED,
    }
    p = profiles
    return {
        'ranges': {k: {'hands': sorted(v), 'combos': combos(v)} for k, v in named.items()},
        'chen': {k: chen(k) for k in PERCENTILE},
        'percentile': PERCENTILE,
        'constants': {
            'POT_CAP': s.POT_CAP, 'SET_MINE_MULT': s.SET_MINE_MULT, 'BIG_RAISE_BB': s.BIG_RAISE_BB,
            'EARLY_WIDE_FOLD': s.EARLY_WIDE_FOLD, 'EQUITY_SAMPLES': s.EQUITY_SAMPLES,
            'MIN_RANGE': s.MIN_RANGE, 'EQUITY_MARGIN': s.EQUITY_MARGIN,
            'DECAY': p.DECAY, 'POP_DECAY': p.POP_DECAY, 'PRIOR_STRENGTH': p.PRIOR_STRENGTH,
            'MIN_HANDS_TO_LABEL': p.MIN_HANDS_TO_LABEL, 'TILT_WINDOW': p.TILT_WINDOW,
            'TILT_DELTA': p.TILT_DELTA, 'LOOSE_VPIP': p.LOOSE_VPIP, 'SWING_BB': bot.SWING_BB,
            'DEFAULTS': p.DEFAULTS,
        },
    }


def roster():
    practice = [{'name': n, 'about': info, 'kind': 'practice', 'default': n in DEFAULT_FIELD}
                for n, (_, _, info) in ROSTER.items()]
    contestants = [{'name': n, 'about': about(cls), 'kind': 'contestant', 'default': False}
                   for n, cls in CONTESTANTS.items() if n not in ROSTER]   # GTO is listed once
    return practice + contestants


def parse_hero(q):
    hero = q.get('hero', bot.NAME).strip().upper()
    if hero not in CONTESTANTS:
        raise ValueError(f'unknown contestant: {hero}')
    return hero


def parse_bots(text, hero=bot.NAME):
    names = [n.strip().upper() for n in text.split(',') if n.strip()]
    unknown = [n for n in names if n not in ROSTER and n not in CONTESTANTS]
    if unknown:
        raise ValueError(f'unknown bot: {", ".join(unknown)}')
    if hero in names:
        raise ValueError(f'{hero} is already playing as you')
    if len(set(names)) != len(names):
        raise ValueError('a bot can only sit once')
    if not 1 <= len(names) <= MAX_OPPONENTS:
        raise ValueError(f'pick 1 to {MAX_OPPONENTS} opponents')
    return tuple(names)


def parse_common(q, match=False):
    games = max(1, int(q.get('games', 500)))
    hands = max(1, int(q.get('hands', 20)))
    if not match and games * hands > MAX_HANDS:
        raise ValueError(f'Games × hands per game is {games * hands:,}. A single run is capped at '
                         f'{MAX_HANDS:,} hands because the page loads every hand; lower Games or '
                         f'Hands per game, or use Run match for longer tests.')
    if match and games * hands > MAX_MATCH_HANDS:
        raise ValueError(f'Games × hands per game is {games * hands:,}. A match is capped at '
                         f'{MAX_MATCH_HANDS:,} hands per seed so it finishes in a few minutes; '
                         f'lower Games or Hands per game, or add seeds instead.')
    hero = parse_hero(q)
    bots = parse_bots(q['bots'], hero) if 'bots' in q else tuple(n for n in DEFAULT_FIELD if n != hero)
    stacks = q.get('stacks', 'carry')
    if stacks not in STACK_MODES:
        raise ValueError(f'stacks must be one of {", ".join(STACK_MODES)}')
    return games, hands, q.get('memory', '1') != '0', hero, bots, stacks


def simulate(games, hands, seed, memory, bots=DEFAULT_FIELD, hero=bot.NAME, stacks='carry'):
    log = []
    names = []

    def on_hand(game, table, nets):
        h = table.last_hand
        if not names:
            names.extend(sorted(h.names))
        idx = {n: i for i, n in enumerate(names)}
        log.append({
            'g': game,
            'seats': [idx[n] for n in h.names],
            'pos': [h.positions[n] for n in h.names],
            'hole': [''.join(h.holes[n]) for n in h.names],
            'board': ''.join(h.board),
            'acts': [[idx[e.player], STREETS.index(e.street), KIND_CODE[e.kind], e.amount, e.to,
                      e.pot_before] for e in h.history],
            'nets': [nets[n] for n in h.names],
            'stk': [h.start[n] for n in h.names],          # stacks at the start of the hand
            'sd': len(h.alive()) > 1,
            'key': hand_key(*h.holes[hero]),
            'bk': _my_buckets(h, hero),
        })

    t0 = time.perf_counter()
    me, table, totals, _ = run(games, hands, seed, memory, on_hand=on_hand, bots=bots, hero=hero,
                               stacks=stacks)
    book = getattr(me, 'book', None)                 # our bot keeps reads; other bots may not
    return {
        'params': {'games': games, 'hands': hands, 'seed': seed, 'memory': memory,
                   'bots': list(bots), 'hero': hero, 'stacks': stacks},
        'seconds': round(time.perf_counter() - t0, 2),
        'me': hero,
        'roster': list(ROSTER) + [n for n in CONTESTANTS if n not in ROSTER],   # fixed order: stable colors
        'big_blind': 100,
        'bots': [{'name': n, 'net': totals[n], 'rebuys': table.rebuys[n], 'errors': table.errors[n],
                  'illegal': table.illegal[n], 'max_ms': round(table.max_ms[n], 2)}
                 for n in names],
        'my_errors': getattr(me, 'errors', 0),
        'reads': [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
                  for r in book.summary()] if book else [],
        'hands': log,
    }


def _ci(xs):
    """95% half-width of the mean of xs; None with fewer than two values."""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return 1.96 * math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) / math.sqrt(len(xs))


def match(seeds, games, hands, memory, bots, hero, first_seed=0, stacks='carry'):
    """Standings over several seeds (one process per seed) and hero-vs-contestant verdicts."""
    t0 = time.perf_counter()
    jobs = [(games, hands, s, memory, bots, hero, stacks) for s in range(first_seed, first_seed + seeds)]
    with ProcessPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 1)) as pool:
        results = list(pool.map(seed_result, jobs))
    r1 = lambda v: None if v is None else round(v, 1)
    standings = []
    for n in (hero, *bots):
        xs = [r['rates'][n] for r in results]
        places = [r['place'][n] for r in results]
        standings.append({
            'name': n, 'kind': 'contestant' if n in CONTESTANTS else 'practice',
            'mean': r1(sum(xs) / len(xs)), 'ci': r1(_ci(xs)), 'places': places,
            'wins': places.count(1), 'max_ms': r1(max(r['max_ms'].get(n, 0) for r in results)),
            'rebuys': sum(r['rebuys'].get(n, 0) for r in results),
            'errors': sum(r['errors'].get(n, 0) for r in results),
            'illegal': sum(r['illegal'].get(n, 0) for r in results),
        })
    standings.sort(key=lambda s: -s['mean'])
    head_to_head = []
    for other in (b for b in bots if b in CONTESTANTS):
        diffs = [r['rates'][hero] - r['rates'][other] for r in results]
        d, ci = sum(diffs) / len(diffs), _ci(diffs)
        ahead = hero if d > 0 else other
        verdict = (f'{ahead} is ahead' if ci is not None and abs(d) > ci
                   else 'too close to call: run more seeds')
        head_to_head.append({'a': hero, 'b': other, 'diff': r1(d), 'ci': r1(ci), 'verdict': verdict})
    return {
        'params': {'seeds': seeds, 'first_seed': first_seed, 'games': games, 'hands': hands,
                   'memory': memory, 'bots': list(bots), 'hero': hero, 'stacks': stacks},
        'seconds': round(time.perf_counter() - t0, 2),
        'roster': list(ROSTER) + [n for n in CONTESTANTS if n not in ROSTER],
        'standings': standings,
        'head_to_head': head_to_head,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, separators=(',', ':')).encode(), 'application/json')

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ('/', '/index.html'):
            self._send(200, (HERE / 'dashboard.html').read_bytes(), 'text/html; charset=utf-8')
        elif url.path in ('/transparency', '/transparency.html'):
            self._send(200, (HERE / 'transparency.html').read_bytes(), 'text/html; charset=utf-8')
        elif url.path == '/api/abe':
            self._json(200, abe_spec())
        elif url.path == '/api/bots':
            self._json(200, {'roster': roster(), 'heroes': list(CONTESTANTS), 'default_hero': bot.NAME, 'max': MAX_OPPONENTS,
                             'problems': [{'file': f, 'error': e} for f, e in PROBLEMS]})
        elif url.path in ('/api/run', '/api/match'):
            try:
                games, hands, memory, hero, bots, stacks = parse_common(q, match=url.path == '/api/match')
                if url.path == '/api/run':
                    self._json(200, simulate(games, hands, int(q.get('seed', 0)), memory, bots, hero, stacks))
                else:
                    seeds = int(q.get('seeds', 8))
                    if not 1 <= seeds <= MAX_SEEDS:
                        raise ValueError(f'pick 1 to {MAX_SEEDS} seeds')
                    self._json(200, match(seeds, games, hands, memory, bots, hero, int(q.get('seed', 0)), stacks))
            except ValueError as e:
                self._json(400, {'error': str(e)})
        else:
            self._send(404, b'not found', 'text/plain')

    def log_message(self, fmt, *args):
        print(f'{self.address_string()} {fmt % args}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8000)
    args = ap.parse_args()
    for f, e in PROBLEMS:
        print(f'skipped {f}: {e}')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Dashboard on http://localhost:{args.port}  (Ctrl+C to stop)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
