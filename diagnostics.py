"""Health check for the whole bench. Run after any change, and before pushing.

    uv run python diagnostics.py           # full run (~1 min)
    uv run python diagnostics.py --quick   # fewer hands

Checks: unit tests, multi-seed results with profiles kept vs reset, legality
and crash-safety on fuzzed and malformed states, decision latency, the stdin
adapter end to end, and the dashboard API. Exits non-zero if anything fails.
"""
import argparse
import json
import math
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from bot import NAME, TightProfiler
from cards import full_deck
from protocol import STREETS, Event, GameState
from ranges import position_name
from run_sim import run

HERE = Path(__file__).parent
FAILS = []


def check(ok, label, detail=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}{"  " + detail if detail else ""}')
    if not ok:
        FAILS.append(label)


def section(title):
    print(f'\n== {title}')


# ---- 1. unit tests --------------------------------------------------------------
def unit_tests():
    section('Unit tests')
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=HERE, capture_output=True, text=True)
    check(r.returncode == 0, 'pytest', r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[-200:])


# ---- 2. results across seeds ------------------------------------------------------
def results(seeds, games, hands):
    section(f'Results: {len(seeds)} seeds x {games * hands:,} hands, profiles kept vs reset')
    print(f'  {"seed":>4} {"memory":>7} {"bb/100":>8} {"+/-95%":>7} {"rank":>5} {"errors":>7} {"max ms":>7}')
    by_mode = {True: [], False: []}
    for seed in seeds:
        for memory in (True, False):
            me, table, totals, mine = run(games, hands, seed, memory)    # asserts chip conservation
            n, bb = len(mine), 100
            mean = sum(mine) / n
            sd = statistics.stdev(mine)
            rank = sorted(totals, key=totals.get, reverse=True).index(me.name) + 1
            errs = me.errors + table.errors[me.name] + table.illegal[me.name]
            by_mode[memory].append(mean / bb * 100)
            print(f'  {seed:>4} {"kept" if memory else "reset":>7} {mean / bb * 100:>8.1f} '
                  f'{1.96 * sd / math.sqrt(n) / bb * 100:>7.1f} {rank:>5} {errs:>7} '
                  f'{table.max_ms[me.name]:>7.1f}')
            check(errs == 0, f'seed {seed} memory={memory}: no errors or illegal actions')
    kept, reset = (statistics.mean(by_mode[m]) for m in (True, False))
    print(f'  mean bb/100: kept {kept:.1f}, reset {reset:.1f}, difference {kept - reset:+.1f}')
    check(kept > 0 and reset > 0, 'profitable on average in both modes')


# ---- 3. fuzzed and malformed states -------------------------------------------------
def legal(a, s):
    if not isinstance(a, tuple) or not a:
        return False
    if a[0] == 'fold':
        return s.to_call > 0
    if a[0] == 'check':
        return s.to_call == 0
    if a[0] == 'call':
        return s.to_call > 0
    if a[0] == 'raise':
        return len(a) == 2 and (s.min_raise_to <= a[1] <= s.max_raise_to or a[1] == s.max_raise_to)
    return False


def random_state(rng, bot_name):
    n = rng.randint(2, 8)
    names = [bot_name] + [f'OPP{i}' for i in range(n - 1)]
    rng.shuffle(names)
    positions = {nm: position_name(off, n) for off, nm in enumerate(names)}
    deck = full_deck()
    rng.shuffle(deck)
    street = rng.choice(STREETS)
    board = [deck.pop() for _ in range({'preflop': 0, 'flop': 3, 'turn': 4, 'river': 5}[street])]
    hole = [deck.pop(), deck.pop()]
    history = []
    for _ in range(rng.randint(0, 12)):
        who = rng.choice(names)
        kind = rng.choice(['fold', 'check', 'call', 'bet', 'raise', 'blind'])
        st = rng.choice(STREETS[:STREETS.index(street) + 1])
        amt = rng.randint(0, 3000)
        history.append(Event(who, st, kind, amt, amt, rng.randint(0, 5000), rng.randint(0, 2000)))
    stack = rng.randint(1, 10000)
    committed = rng.randint(0, 2000)
    to_call = rng.choice([0, 0, rng.randint(1, stack)])
    max_to = committed + stack
    min_to = min(max_to, committed + to_call + rng.choice([100, 200, 600]))
    opps = [nm for nm in names if nm != bot_name and rng.random() < 0.7]
    return GameState(me=bot_name, hole=hole, board=board, street=street,
                     pot=rng.randint(150, 20000), to_call=to_call, min_raise_to=min_to,
                     max_raise_to=max_to, stack=stack, committed=committed, big_blind=100,
                     position=positions[bot_name], positions=positions, opponents=opps,
                     history=history)


def fuzz(n):
    section(f'Fuzz: {n:,} random states, plus malformed input')
    rng = random.Random(1)
    bot = TightProfiler(NAME)
    bad, times = 0, []
    for _ in range(n):
        s = random_state(rng, NAME)
        t0 = time.perf_counter()
        a = bot.act(s)
        times.append((time.perf_counter() - t0) * 1000)
        bad += not legal(a, s)
    check(bad == 0, 'every fuzzed decision is legal', f'{bad} illegal')
    check(bot.errors == 0, 'no exceptions caught on well-formed states', f'{bot.errors} caught')
    times.sort()
    p99, worst = times[int(len(times) * 0.99)], times[-1]
    check(worst < 50, 'decision latency', f'p99 {p99:.2f} ms, worst {worst:.2f} ms')

    # Malformed states: the bot must still answer with check or fold, never raise.
    broken = random_state(rng, NAME)
    broken.hole = ['Xx', None]
    out = bot.act(broken)
    check(out in (('check',), ('fold',)), 'garbage hole cards fall back to check/fold', str(out))
    for method, args in (('observe', (None,)), ('end_hand', (None, None, None)),
                         ('new_hand', (None, None, None))):
        try:
            getattr(bot, method)(*args)
            ok = True
        except Exception as e:      # noqa: BLE001
            ok, out = False, repr(e)
        check(ok, f'{method}() survives garbage input')


def contestants(n):
    """Every bot in bots/: loads, answers fuzzed states legally and quickly, and plays a
    real session without errors. Run this before pushing a new or changed bot."""
    from bots import CONTESTANTS, PROBLEMS
    section(f'Contestant bots: {", ".join(CONTESTANTS)}')
    for f, e in PROBLEMS:
        check(False, f'{f} loads', e)
    if not PROBLEMS:
        check(True, 'every file in bots/ loads')
    for name, cls in CONTESTANTS.items():
        if name == NAME:
            continue                                # covered in depth by fuzz() above
        rng, contestant = random.Random(7), cls(name)
        bad, crashed, times = 0, 0, []
        for _ in range(n):
            s = random_state(rng, name)
            t0 = time.perf_counter()
            try:
                bad += not legal(contestant.act(s), s)
            except Exception:                       # noqa: BLE001
                crashed += 1
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        check(crashed == 0 and bad == 0, f'{name}: legal on {n:,} fuzzed states',
              f'{crashed} exceptions, {bad} illegal')
        check(times[-1] < 50, f'{name}: decision latency', f'p99 {times[int(len(times) * 0.99)]:.2f} ms, worst {times[-1]:.2f} ms')
        _, table, totals, _ = run(50, 20, 11, True, hero=name)
        check(table.errors[name] == 0 and table.illegal[name] == 0, f'{name}: 1,000-hand session clean',
              f'{table.errors[name]} errors, {table.illegal[name]} illegal')


# ---- 4. adapter end to end ------------------------------------------------------------
def adapter():
    section('Adapter: JSON lines over stdin/stdout')
    positions = {NAME: 'BTN', 'V1': 'SB', 'V2': 'BB'}
    act = {'type': 'act', 'hole': ['As', 'Ad'], 'board': [], 'street': 'preflop', 'pot': 150,
           'to_call': 100, 'min_raise_to': 200, 'max_raise_to': 10000, 'stack': 10000,
           'committed': 0, 'position': 'BTN', 'positions': positions, 'opponents': ['V1', 'V2']}
    lines = [
        {'type': 'new_hand', 'hand_id': 1, 'positions': positions, 'hole': ['As', 'Ad']},
        {'type': 'event', 'player': 'V1', 'street': 'preflop', 'kind': 'blind', 'amount': 50,
         'to': 50, 'pot_before': 0, 'to_call_before': 0},
        act,
        'this is not json',
        {'type': 'act', 'to_call': 0},                       # malformed: missing fields
        {'type': 'end_hand', 'nets': {NAME: 150}, 'shown': {}, 'board': []},
    ]
    feed = '\n'.join(x if isinstance(x, str) else json.dumps(x) for x in lines) + '\n'
    r = subprocess.run([sys.executable, 'adapter.py'], cwd=HERE, input=feed,
                       capture_output=True, text=True, timeout=20)
    replies = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    check(r.returncode == 0, 'adapter exits cleanly', r.stderr[-200:])
    check(len(replies) == 2, 'one reply per act request', f'{len(replies)} replies')
    if replies:
        check(replies[0].get('action') == 'raise', 'AA on the button raises', str(replies[0]))
    if len(replies) > 1:
        check(replies[1] == {'action': 'check'}, 'malformed act gets a safe check', str(replies[1]))


# ---- 5. dashboard API -------------------------------------------------------------------
def dashboard():
    section('Dashboard API')
    import dashboard as dash
    server = ThreadingHTTPServer(('127.0.0.1', 0), dash.Handler)
    server.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    code, body = get('/')
    check(code == 200 and b'Pokerbot Test Bench' in body, 'page serves')
    code, body = get('/api/run?games=20&hands=10&seed=3')
    d = json.loads(body) if code == 200 else {}
    hands = d.get('hands', [])
    check(code == 200 and len(hands) == 200, 'run returns every hand', f'{len(hands)} hands')
    check(all(sum(h['nets']) == 0 for h in hands), 'chips conserved in every logged hand')
    check(sum(b['net'] for b in d.get('bots', [])) == 0, 'bot totals sum to zero')
    def dealt(h):
        s = ''.join(h['hole']) + h['board']
        return [s[i:i + 2] for i in range(0, len(s), 2)]
    check(all(len(set(dealt(h))) == len(dealt(h)) for h in hands), 'no card dealt twice')
    names = [b['name'] for b in d.get('bots', [])]
    me_idx = names.index(d['me']) if d.get('me') in names else -1
    logged = sum(h['nets'][h['seats'].index(me_idx)] for h in hands)
    check(logged == d['bots'][me_idx]['net'] if me_idx >= 0 else False,
          'per-hand log matches the hero total')
    code, body = get('/transparency')
    check(code == 200 and b'how Yosief decides' in body, 'transparency page serves')
    code, body = get('/api/abe')
    spec = json.loads(body) if code == 200 else {}
    import strategy
    check(spec.get('constants', {}).get('POT_CAP') == strategy.POT_CAP
          and sorted(spec.get('ranges', {}).get('BASE', {}).get('hands', [])) == sorted(strategy.BASE)
          and len(spec.get('chen', {})) == 169, 'transparency data matches the code')
    code, body = get('/api/bots')
    roster = [b['name'] for b in json.loads(body)['roster']] if code == 200 else []
    check('RIVERKING' in roster and 'STATION' in roster, 'roster lists every bot', ','.join(roster))
    code, body = get('/api/run?games=5&hands=10&seed=1&bots=NIT,MANIAC,station')
    d = json.loads(body) if code == 200 else {}
    seated = sorted(b['name'] for b in d.get('bots', []))
    check(seated == sorted([NAME, 'MANIAC', 'NIT', 'STATION'])
          and all(len(h['seats']) == 4 for h in d.get('hands', [])), 'custom lineup is seated', str(seated))
    code, body = get(f'/api/run?games=5&hands=10&seed=2&hero=starter&bots=NIT,{NAME},MANIAC')
    d = json.loads(body) if code == 200 else {}
    check(d.get('me') == 'STARTER' and len(d.get('hands', [])) == 50, 'a contestant can play as you')
    code, body = get(f'/api/match?seeds=2&games=5&hands=10&bots=STARTER,NIT,MANIAC')
    d = json.loads(body) if code == 200 else {}
    check(len(d.get('standings', [])) == 4 and len(d.get('head_to_head', [])) == 1
          and all(len(s['places']) == 2 for s in d.get('standings', [])), 'match returns standings and a verdict')
    check(get('/api/match?seeds=99')[0] == 400, 'too many match seeds -> 400')
    check(get('/api/run?bots=NIT,NOBODY')[0] == 400, 'unknown bot -> 400')
    check(get('/api/run?bots=' + ','.join(roster))[0] == 400, 'more than 7 opponents -> 400')
    check(get('/api/run?bots=NIT,NIT')[0] == 400, 'duplicate bot -> 400')
    check(get('/api/run?games=abc')[0] == 400, 'bad parameter -> 400')
    check(get('/api/run?games=1000&hands=1000')[0] == 400, 'oversized run -> 400')
    check(get('/nope')[0] == 404, 'unknown path -> 404')
    server.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    args = ap.parse_args()
    t0 = time.perf_counter()
    unit_tests()
    if args.quick:
        results([0, 1], 100, 20)
        fuzz(2000)
        contestants(2000)
    else:
        results([0, 1, 2, 3], 500, 20)
        fuzz(20000)
        contestants(20000)
    adapter()
    dashboard()
    print(f'\n{"ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS)}'
          f'  ({time.perf_counter() - t0:.0f}s)')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
