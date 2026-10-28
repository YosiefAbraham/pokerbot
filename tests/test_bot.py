import random

from bot import NAME as ME, TightProfiler
from cards import evaluate, parse_card, FLUSH, STRAIGHT, FULL_HOUSE, TWO_PAIR
from engine import Table, award
from opponents import RandomBot, default_field
from protocol import GameState
from ranges import combos, expand, hand_key, position_name
from strategy import BASE, classify, decide


def cards(s):
    return s.split()


# ---- cards ---------------------------------------------------------------------
def test_parse_formats():
    assert parse_card('10h') == 'Th'
    assert parse_card('A♠︎') == 'As'
    assert parse_card('kD') == 'Kd'


def test_hand_order():
    flush = evaluate(cards('Ah 9h 5h 3h 2h Kd Qc'))
    straight = evaluate(cards('9c Td Jh Qs Kd 2c 3c'))
    boat = evaluate(cards('Kh Kd Ks 2c 2d 7h 9s'))
    assert flush[0] == FLUSH and straight[0] == STRAIGHT and boat[0] == FULL_HOUSE
    assert boat > flush > straight


def test_wheel_and_kickers():
    assert evaluate(cards('Ah 2d 3c 4s 5h 9d Kc')) == (STRAIGHT, 5)
    assert evaluate(cards('As Ks 7d 7c 2h 3h 9s')) > evaluate(cards('As Qs 7d 7c 2h 3h 9s'))
    assert evaluate(cards('Kh Kd 7s 7c 2h'))[0] == TWO_PAIR


# ---- ranges & positions -----------------------------------------------------------
def test_base_range_is_142_combos():
    assert combos(BASE) == 142                      # ~10.7% of 1,326


def test_range_parser():
    assert expand('A2s-A4s') == {'A2s', 'A3s', 'A4s'}
    assert expand('KQ') == {'KQs', 'KQo'}
    assert expand('TT+') == {'TT', 'JJ', 'QQ', 'KK', 'AA'}
    assert expand('AJ+') == {'AJs', 'AJo', 'AQs', 'AQo', 'AKs', 'AKo'}
    assert hand_key('Kd', 'As') == 'AKo' and hand_key('7h', '7c') == '77'


def test_positions():
    names8 = [position_name(i, 8) for i in range(8)]
    assert names8 == ['BTN', 'SB', 'BB', 'UTG', 'UTG1', 'MP', 'HJ', 'CO']
    assert [position_name(i, 6) for i in range(6)] == ['BTN', 'SB', 'BB', 'MP', 'HJ', 'CO']


# ---- buckets ---------------------------------------------------------------------
def test_classify():
    assert classify(cards('7h 7d'), cards('7s Kd 2c')) == 'monster'        # set
    assert classify(cards('Ah Kd'), cards('Kc 7s 2d')) == 'strong'         # top pair, good kicker
    assert classify(cards('Kh 4d'), cards('Kc 7s 2d')) == 'medium'         # top pair, weak kicker
    assert classify(cards('Qh Qd'), cards('Jc 7s 2d')) == 'strong'         # overpair
    assert classify(cards('Ah 5h'), cards('Kh 9h 2c')) == 'draw'           # flush draw
    assert classify(cards('9d 8c'), cards('7s 6h 2c')) == 'draw'           # open-ended
    assert classify(cards('Ah Kd'), cards('7s 6h 2c')) == 'air'
    assert classify(cards('Ah Kd'), cards('7s 7h 7c 7d 2c')) == 'air'      # board plays


# ---- decisions -----------------------------------------------------------------------
def state(hole, position='CO', to_call=100, committed=0, history=(), board=(), street='preflop'):
    return GameState(me=ME, hole=cards(hole), board=list(board), street=street, pot=150 + to_call,
                     to_call=to_call, min_raise_to=200, max_raise_to=10000, stack=10000 - committed,
                     committed=committed, big_blind=100, position=position,
                     positions={ME: position, 'X': 'BB'}, opponents=['X'], history=list(history))


def test_preflop_chart():
    bot = TightProfiler()
    assert decide(state('As Td', 'CO'), bot.book)[0] == 'raise'       # AT on the CO: open
    assert decide(state('As Td', 'UTG'), bot.book) == ('fold',)       # AT under the gun: fold
    assert decide(state('Kh 7c', 'BTN'), bot.book) == ('fold',)
    assert decide(state('Kh 7c', 'BB', to_call=0), bot.book) == ('check',)   # never fold free


def _book_with(name, vpip, hands=50, **stats):
    from profiles import ProfileBook
    book = ProfileBook()
    for i in range(hands):
        book.record(name, 'vpip', i < vpip * hands)
        for stat, rate in stats.items():
            book.record(name, stat, i < rate * hands)
    book.get(name).hands = hands
    return book


def test_loose_label():
    assert _book_with('X', 0.45, fold_to_bet=0.5, agg=0.2).get('X').kind() == 'loose'
    assert _book_with('X', 0.20, fold_to_bet=0.5, agg=0.2).get('X').kind() == 'regular'


def test_wider_vs_loose_raiser():
    from protocol import Event
    raise_ev = [Event('X', 'preflop', 'raise', 300, 300, 150, 100)]
    loose = _book_with('X', 0.45, fold_to_bet=0.5, agg=0.2)
    tight = _book_with('X', 0.20, fold_to_bet=0.5, agg=0.2)
    s = state('Ks Js', 'BTN', to_call=300, history=raise_ev)
    assert decide(s, tight) == ('fold',)
    assert decide(s, loose) == ('call',)
    s = state('9s 9d', 'BTN', to_call=300, history=raise_ev)
    assert decide(s, loose)[0] == 'raise'


def test_loose_shove_calls_only_with_strong_hands():
    from protocol import Event
    shove = [Event('X', 'preflop', 'raise', 10000, 10000, 150, 100)]
    loose = _book_with('X', 0.45, fold_to_bet=0.5, agg=0.2)
    assert decide(state('As Ts', 'BTN', to_call=10000, history=shove), loose) == ('call',)
    assert decide(state('5s 5d', 'BTN', to_call=10000, history=shove), loose) == ('fold',)


def test_isolate_loose_limper():
    from protocol import Event
    limp = [Event('X', 'preflop', 'call', 100, 100, 150, 100)]
    loose = _book_with('X', 0.45, fold_to_bet=0.5, agg=0.2)
    tight = _book_with('X', 0.20, fold_to_bet=0.5, agg=0.2)
    s = state('Kh Qd', 'CO', history=limp)
    assert decide(s, tight) == ('fold',)
    assert decide(s, loose)[0] == 'raise'


def test_equity_counts_their_outs():
    from strategy import equity
    flop = cards('7h 3d 7c')
    heads_up = equity(cards('5s 5d'), flop, [0.7])
    multiway = equity(cards('5s 5d'), flop, [0.7, 0.7, 0.7, 0.45])
    assert heads_up > 0.55 and multiway < 0.3          # every 7 or 3 out there beats us
    assert equity(cards('As Ad'), cards('Kh 7d 2c'), [1.0]) > 0.8
    assert equity(cards('5s 5d'), flop, [0.7]) == heads_up      # deterministic


def test_fold_underpair_multiway_on_paired_board():
    from protocol import Event
    from profiles import ProfileBook
    book = ProfileBook()
    s = state('5s 5d', 'CO', to_call=2300, board=cards('7h 3d 7c'), street='flop',
              history=[Event('M', 'flop', 'bet', 2300, 2300, 1500, 0)])
    s.pot, s.opponents = 3800, ['M', 'A', 'B', 'C']
    assert decide(s, book) == ('fold',)
    s.hole = cards('9s 9d')
    s.opponents = ['M']
    s.pot, s.to_call = 2000, 500                     # overpair, small bet, heads-up: call
    assert decide(s, book) == ('call',)


def test_small_pairs_limp_except_when_stealing():
    from profiles import ProfileBook
    for pos in ('UTG', 'MP', 'HJ'):
        assert decide(state('6s 6d', pos), ProfileBook()) == ('call',)
    assert decide(state('2s 2d', 'SB', to_call=50, committed=50), ProfileBook()) == ('call',)
    for pos in ('CO', 'BTN'):                        # folded to the steal seats: raise
        assert decide(state('6s 6d', pos), ProfileBook())[0] == 'raise'


def test_steal_from_cutoff_and_button():
    from profiles import ProfileBook
    assert decide(state('Ks 8s', 'BTN'), ProfileBook())[0] == 'raise'     # top 35% on the button
    assert decide(state('Ks 8s', 'CO'), ProfileBook()) == ('fold',)       # not top 27%
    assert decide(state('Qs Jd', 'CO'), ProfileBook())[0] == 'raise'
    assert decide(state('Qs Jd', 'MP'), ProfileBook()) == ('fold',)       # too early to steal


def _early_seat(hole, book, behind):
    """We act first at UTG; behind maps name -> position for everyone still to act."""
    s = state(hole, 'UTG')
    s.pot = 150
    s.positions = {ME: 'UTG', **behind}
    s.opponents = list(behind)
    return s


def _book_ftr(rates, vpip=0.2, agg=0.2):
    from profiles import ProfileBook
    book = ProfileBook()
    for name, ftr in rates.items():
        for i in range(50):
            book.record(name, 'vpip', i < vpip * 50)
            book.record(name, 'agg', i < agg * 50)
            book.record(name, 'fold_to_bet', i < 25)
            book.record(name, 'fold_to_raise_pf', i < ftr * 50)
        book.get(name).hands = 50
    return book


def _add_station(book, name):
    for i in range(50):                          # plays 70%, never folds to bets
        book.record(name, 'vpip', i < 35)
        book.record(name, 'fold_to_bet', False)
    book.get(name).hands = 50
    assert book.get(name).kind() == 'station'


def test_wider_early_opens_only_with_a_station_behind():
    seats = {'A': 'UTG1', 'B': 'MP', 'S': 'BB'}
    book = _book_ftr({'A': 0.5, 'B': 0.5})
    _add_station(book, 'S')
    assert decide(_early_seat('As Jd', book, seats), book)[0] == 'raise'    # AJ opens
    assert decide(_early_seat('Ks Qs', book, seats), book)[0] == 'raise'    # so does KQs
    assert decide(_early_seat('Ks Jd', book, seats), book) == ('fold',)
    no_station = _book_ftr({'A': 0.5, 'B': 0.5, 'S': 0.5})
    assert decide(_early_seat('As Jd', no_station, seats), no_station) == ('fold',)   # old range
    assert decide(_early_seat('As Qd', no_station, seats), no_station)[0] == 'raise'


def test_early_tightens_when_maniac_is_next():
    seats = {'M': 'UTG1', 'B': 'MP', 'S': 'BB'}
    book = _book_ftr({'B': 0.5})
    _add_station(book, 'S')
    for i in range(50):                          # M: plays 70%, raises a lot
        book.record('M', 'vpip', i < 35)
        book.record('M', 'agg', i < 30)
    book.get('M').hands = 50
    assert book.get('M').kind() == 'maniac'
    assert decide(_early_seat('As Jd', book, seats), book) == ('fold',)    # station behind, but
    assert decide(_early_seat('As Qd', book, seats), book)[0] == 'raise'   # the maniac acts first


def test_early_widens_when_everyone_behind_folds():
    seats = {'A': 'UTG1', 'B': 'MP', 'C': 'BB'}
    folders = _book_ftr({'A': 0.9, 'B': 0.9, 'C': 0.9})
    mixed = _book_ftr({'A': 0.9, 'B': 0.9, 'C': 0.4})
    assert decide(_early_seat('Js Ts', folders, seats), folders)[0] == 'raise'
    assert decide(_early_seat('Js Ts', mixed, seats), mixed) == ('fold',)


def test_play_top_27_percent_vs_loose_limper():
    from protocol import Event
    limp = [Event('X', 'preflop', 'call', 100, 100, 150, 100)]
    loose = _book_with('X', 0.45, fold_to_bet=0.5, agg=0.2)
    tight = _book_with('X', 0.20, fold_to_bet=0.5, agg=0.2)
    assert decide(state('Ks 9s', 'UTG1', history=limp), loose)[0] == 'raise'
    assert decide(state('Ks 9s', 'UTG1', history=limp), tight) == ('fold',)
    assert decide(state('Ks 7d', 'UTG1', history=limp), loose) == ('fold',)    # outside 27%


def test_weak_hands_never_bet_and_call_only_with_outs():
    from protocol import Event
    from profiles import ProfileBook
    mid = state('Kh 9d', 'BTN', to_call=0, board=cards('Ac 9s 2d'), street='flop')
    assert decide(mid, ProfileBook()) == ('check',)       # middle pair never bets
    pot_bet = [Event('X', 'flop', 'bet', 1000, 1000, 1000, 0)]
    air = state('Kh 4d', 'BTN', to_call=1000, board=cards('Ac 9s 2d'), street='flop',
                history=pot_bet)
    air.pot = 2000
    assert decide(air, ProfileBook()) == ('fold',)        # king-high, pot-sized bet: no outs
    bet = [Event('X', 'flop', 'bet', 100, 100, 1000, 0)]
    fd = state('Ah 5h', 'BTN', to_call=100, board=cards('Kh 9h 2c'), street='flop', history=bet)
    fd.pot = 1100
    assert decide(fd, ProfileBook()) == ('call',)         # nut flush draw: has outs


def test_bet_bigger_vs_station_heads_up():
    station = _book_with('X', 0.7, fold_to_bet=0.02, agg=0.02)
    assert station.get('X').kind() == 'station'
    s = state('As Ad', 'BTN', to_call=0, board=cards('Ac 7d 2c'), street='flop')
    s.pot = 1000
    assert decide(s, station) == ('raise', 1500)          # set of aces: 1.5x pot


def test_small_pairs_set_mine_raises():
    from protocol import Event
    from profiles import ProfileBook
    raised = lambda to: [Event('X', 'preflop', 'raise', to, to, 150, 100)]
    assert decide(state('4s 4d', 'BTN', to_call=800, history=raised(800)), ProfileBook()) == ('call',)
    assert decide(state('4s 4d', 'BTN', to_call=1500, history=raised(1500)), ProfileBook()) == ('fold',)


def test_small_pairs_continue_only_with_a_set():
    from protocol import Event
    from profiles import ProfileBook
    bet = [Event('X', 'flop', 'bet', 100, 100, 600, 0)]
    miss = state('6s 6d', 'BTN', to_call=100, board=cards('Kh 8d 2c'), street='flop', history=bet)
    assert decide(miss, ProfileBook()) == ('fold',)               # cheap bet, but no set
    miss.to_call, miss.history = 0, []
    assert decide(miss, ProfileBook()) == ('check',)
    hit = state('6s 6d', 'BTN', to_call=100, board=cards('Kh 6c 2c'), street='flop', history=bet)
    assert decide(hit, ProfileBook())[0] == 'raise'


def test_raise_cap_two_pots():
    from protocol import Event
    from profiles import ProfileBook
    s = state('As Ad', 'CO', to_call=100)
    s.pot = 150                                      # just the blinds
    assert decide(s, ProfileBook()) == ('raise', 300)    # 4bb open capped at 2 x 150
    bet = [Event('X', 'flop', 'bet', 500, 500, 1000, 0)]
    s = state('As Ad', 'BTN', to_call=500, board=cards('Ac 7d 2c'), street='flop', history=bet)
    s.pot = 1500
    to = decide(s, ProfileBook())[1]
    assert to - s.committed <= 2 * s.pot             # a set of aces, still capped postflop


def test_premium_reraise_is_uncapped():
    from protocol import Event
    three_bet = [Event('X', 'preflop', 'raise', 300, 300, 150, 100),
                 Event('Y', 'preflop', 'raise', 900, 900, 450, 300)]
    loose = _book_with('Y', 0.45, fold_to_bet=0.5, agg=0.2)
    for hole in ('As Ad', 'Kh Kd', 'As Ks'):
        assert decide(state(hole, 'BTN', to_call=900, history=three_bet), loose) == ('raise', 10000)
    s = state('Qs Qd', 'BTN', to_call=900, history=three_bet)    # QQ shoves vs loose, but capped
    assert decide(s, loose) == ('raise', 2 * s.pot)


def _facing_all_in(hole, board):
    from protocol import Event
    s = state(hole, 'BTN', to_call=10000, board=cards(board), street='flop',
              history=[Event('X', 'flop', 'bet', 10000, 10000, 1000, 0)])
    s.pot = 11000
    return s


def test_one_pair_all_in_only_vs_loose():
    from profiles import ProfileBook
    tight = _book_with('X', 0.15, fold_to_bet=0.5, agg=0.3)
    loose = _book_with('X', 0.7, fold_to_bet=0.1, agg=0.6)
    assert decide(_facing_all_in('As Kd', 'Ah 7c 2d'), tight) == ('fold',)     # top pair
    assert decide(_facing_all_in('Qs Qd', 'Jh 7c 2d'), tight) == ('fold',)     # overpair
    assert decide(_facing_all_in('As Kd', 'Ah 7c 2d'), loose) == ('call',)
    assert decide(_facing_all_in('Ks Qd', '7h 7c 2d'), loose) == ('fold',)     # board plays
    s = state('As Kd', 'BTN', to_call=0, board=cards('Ah 7c 2d'), street='flop')
    s.pot, s.stack, s.max_raise_to = 2000, 500, 500     # any bet would be all-in
    assert decide(s, ProfileBook()) == ('check',)


def test_all_in_with_trips_or_two_pair_vs_loose():
    from profiles import ProfileBook
    assert decide(_facing_all_in('7s 7d', 'Ah 7c 2d'), ProfileBook()) == ('call',)   # set
    tight = _book_with('X', 0.15, fold_to_bet=0.5, agg=0.3)
    loose = _book_with('X', 0.7, fold_to_bet=0.1, agg=0.6)
    assert decide(_facing_all_in('As 7d', 'Ah 7c 2d'), tight) == ('fold',)     # two pair, tight
    assert decide(_facing_all_in('As 7d', 'Ah 7c 2d'), loose) == ('call',)     # two pair, loose


def test_actions_always_legal_when_short():
    bot = TightProfiler()
    s = state('As Ad', 'CO')
    s.max_raise_to = 150                                              # can't make a full raise
    a = decide(s, bot.book)
    assert a == ('raise', 150) or a == ('call',)


# ---- engine ----------------------------------------------------------------------------
def test_side_pots():
    contrib = {'A': 1000, 'B': 3000, 'C': 3000}
    values = {'A': (9,), 'B': (5,), 'C': (1,)}
    pay = award(contrib, ['A', 'B', 'C'], values)
    assert pay == {'A': 3000, 'B': 4000}


def test_riverking_never_folds_or_raises():
    from opponents import RiverKing
    rk = RiverKing('RK')
    assert rk.act(state('7h 2c', to_call=0)) == ('check',)
    assert rk.act(state('7h 2c', to_call=9900)) == ('call',)
    assert rk.act(state('7h 2c', to_call=500, board=cards('Ah Kd Qc 9s 3h'), street='river')) == ('call',)


def test_stacks_carry_over_with_rebuys():
    from protocol import RAISE

    class Shover:
        def __init__(self, name): self.name = name
        def new_hand(self, *a): pass
        def observe(self, ev): pass
        def end_hand(self, *a): pass
        def act(self, s): return RAISE(s.max_raise_to)

    bots = [Shover('S1'), Shover('S2'), Shover('S3')]
    t = Table(bots, seed=4)                              # carry=True is the default
    for h in range(60):
        nets = t.play_hand(h, h % 3)
        assert sum(nets.values()) == 0
        assert all(v >= 0 for v in t.stacks.values())
    rebuys = sum(t.rebuys.values())
    assert rebuys > 0                                     # all-in every hand: someone busts
    assert sum(t.stacks.values()) == 3 * 10000 + rebuys * 10000   # chips only enter by buying in
    reset = Table([Shover('A'), Shover('B')], seed=4, carry=False)
    reset.play_hand(1, 0)
    assert not reset.stacks and not reset.rebuys


def test_effective_stack_limits_set_mining():
    from protocol import Event
    from profiles import ProfileBook
    raised = [Event('X', 'preflop', 'raise', 800, 800, 150, 100)]
    s = state('4s 4d', 'BTN', to_call=800, history=raised)
    s.stack, s.max_raise_to = 50000, 50000              # we're deep...
    s.stacks, s.bets = {ME: 50000, 'X': 500}, {'X': 800}  # ...but the raiser has 13bb in total
    assert decide(s, ProfileBook()) == ('fold',)          # 800 x 10 > 1,300 to win: no set-mine
    s.stacks['X'] = 40000
    assert decide(s, ProfileBook()) == ('call',)


def test_calling_a_short_all_in_counts_as_all_in():
    from protocol import Event
    tight = _book_with('X', 0.15, fold_to_bet=0.5, agg=0.3)
    s = state('As Kd', 'BTN', to_call=6000, board=cards('Ah 7c 2d'), street='flop',
              history=[Event('X', 'flop', 'bet', 6000, 6000, 1000, 0)])
    s.pot, s.stack, s.max_raise_to = 7000, 50000, 50000
    s.stacks, s.bets = {ME: 50000, 'X': 0}, {'X': 6000}  # X is all-in for 60bb; we cover it
    assert decide(s, tight) == ('fold',)                 # top pair vs a tight all-in: no
    s.hole = cards('7s 7d')
    assert decide(s, tight) == ('call',)                 # a set: yes


def test_chips_conserved_and_bot_never_errors():
    me = TightProfiler(ME)
    bots = [me] + [RandomBot(f'R{i}', i) for i in range(7)]
    t = Table(bots, seed=1)
    for h in range(400):
        nets = t.play_hand(h, h % 8)
        assert sum(nets.values()) == 0
    assert me.errors == 0 and t.errors[ME] == 0 and t.illegal[ME] == 0


def test_profiles_read_the_field():
    me = TightProfiler(ME)
    bots = [me] + default_field(5)
    t = Table(bots, seed=5)
    rng = random.Random(5)
    for h in range(1500):
        if h % 20 == 0:
            rng.shuffle(bots)
        t.play_hand(h, h % 8)
    kinds = {r['name']: r['kind'] for r in me.book.summary()}
    assert kinds['RIVERKING'] == 'station'
    assert kinds['NIT'] == 'nit'
    assert kinds['MANIAC'] == 'maniac'


# ---- contestant bots -----------------------------------------------------------------------
def test_discovery_finds_bots_and_reports_problems(tmp_path, monkeypatch):
    import sys
    from bots import CONTESTANTS, PROBLEMS, discover
    assert ME in CONTESTANTS and 'STARTER' in CONTESTANTS and not PROBLEMS
    pkg = tmp_path / 'tmpbots'
    pkg.mkdir()
    (pkg / '__init__.py').write_text('')
    body = 'NAME = {!r}\nclass Bot:\n    def __init__(self, name): self.name = name\n'
    (pkg / 'good.py').write_text(body.format('GOODBOT'))
    (pkg / 'clash.py').write_text(body.format('NIT'))
    (pkg / 'lower.py').write_text(body.format('lowercase'))
    (pkg / 'broken.py').write_text('def oops(:\n')
    (pkg / '_helper.py').write_text('raise RuntimeError("never imported")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    found, problems = discover(pkg, 'tmpbots')
    assert set(found) == {ME, 'GOODBOT'}
    assert sorted(f for f, _ in problems) == ['bots/broken.py', 'bots/clash.py', 'bots/lower.py']
    for mod in [m for m in sys.modules if m.startswith('tmpbots')]:
        del sys.modules[mod]


def test_gto_is_both_a_practice_bot_and_a_contestant():
    from bots import CONTESTANTS, PROBLEMS
    from dashboard import roster
    from opponents import ROSTER
    assert CONTESTANTS['GTO'] is ROSTER['GTO'][0] and not PROBLEMS
    assert [b['name'] for b in roster()].count('GTO') == 1     # listed once, with the practice bots


def test_gto_is_mixed_but_reproducible():
    from gto import GTOBot
    from protocol import Event
    raised = [Event('X', 'preflop', 'raise', 250, 250, 150, 100)]
    hands = ['As Ks', '7h 6h', 'Qd Jc', '5s 4s', 'Ah 4h', 'Td 9d', '2c 2d', 'Kh Th'] * 5
    def play(seed):
        bot = GTOBot('GTO', seed)
        return [bot.act(state(h, 'BTN', to_call=250, history=raised)) for h in hands]
    first = play(3)
    assert first == play(3)                                   # same seed, same decisions
    assert {a[0] for a in first} >= {'raise', 'call', 'fold'}  # a real mix of actions
    assert ('raise',) not in first and all(a[0] in ('raise', 'call', 'fold') for a in first)


def test_starter_can_be_the_hero():
    from run_sim import run
    lineup = (ME, 'NIT', 'MANIAC', 'LIMPER', 'REGULAR', 'RANDOM', 'RIVERKING')
    hero, table, totals, mine = run(20, 10, 3, True, bots=lineup, hero='STARTER')
    assert hero.name == 'STARTER' and len(mine) == 200 and sum(totals.values()) == 0
    assert table.errors['STARTER'] == 0 and table.illegal['STARTER'] == 0


def test_lineup_validation():
    import pytest
    from dashboard import parse_bots
    assert parse_bots('nit, starter', hero=ME) == ('NIT', 'STARTER')
    with pytest.raises(ValueError):
        parse_bots(f'NIT,{ME}', hero=ME)                 # the hero can't also be an opponent
    with pytest.raises(ValueError):
        parse_bots('NIT,NOBODY', hero=ME)


# ---- adapter ---------------------------------------------------------------------------
def test_adapter_bad_line_after_act_sends_nothing():
    import json, subprocess, sys
    from pathlib import Path
    act = {'type': 'act', 'hole': ['As', 'Ad'], 'board': [], 'street': 'preflop', 'pot': 150,
           'to_call': 100, 'min_raise_to': 200, 'max_raise_to': 10000, 'stack': 10000,
           'committed': 0, 'position': 'BTN', 'positions': {ME: 'BTN', 'V': 'BB'},
           'opponents': ['V']}
    feed = json.dumps(act) + '\nnot json\n'
    out = subprocess.run([sys.executable, 'adapter.py'], cwd=Path(__file__).parent.parent,
                         input=feed, capture_output=True, text=True, timeout=20).stdout
    assert len(out.splitlines()) == 1      # one act, one reply; the garbage line gets none
