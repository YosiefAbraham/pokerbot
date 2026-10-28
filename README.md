# Yosief: a no-limit hold'em bot, and a bench for racing bots

This README is the full handoff for this folder. If you're an agent or person arriving with no
context, read it top to bottom before changing anything. It explains why the project exists,
which design choices were deliberate, how the code fits together, what has been measured, and
what's still to do.

---

## 1. Purpose

### What this project is
A personal project with four parts:
1. **A bot, Yosief** (class `TightProfiler`), built on a simple poker philosophy (below).
2. **A local test bench:** a dealer, a field of practice bots, simulations, a browser dashboard,
   a transparency page and a diagnostics suite, so every strategy change can be measured.
3. **Competing bots** (§11): friends drop their own bot into `bots/` and race it against Yosief.
4. **An adapter seam:** if Yosief ever plays through another dealer program, only `adapter.py`
   has to learn that program's message format.

### The game
- No-limit Texas hold'em, up to **8 bots per table**, blinds **50/100**.
- Stacks start at **10,000 chips (100 big blinds)** and reset every hand.
- A run is a series of games (20 hands each by default). Seats are reshuffled and the button
  moves between games.

### The philosophy (the design brief)
- **Play tight.** The base range is any pocket pair plus AK, AQ, AJ, AT. That's 142 of 1,326
  combos, about 10.7%, so the bot folds about 89% of hands ("fold ~90%").
- **Widen or tighten by position and by who is at the table.**
- **Keep a profile of every opponent that never stops adapting.** Track how often they play,
  raise, fold and bluff, and whether they tilt.
- **Exploit, don't solve.** Against simple bots, punishing their habits beats game-theory-optimal
  play, which isn't practical to compute for 8-handed no-limit anyway.

Tilt is **measured, not assumed.** Bots only tilt if someone coded them to; the practice bot
TILTER does.

---

## 2. Quick start

This is a standalone [uv](https://docs.astral.sh/uv/) project on Python 3.12. It has **no runtime
dependencies**, only the standard library; pytest is the one dev dependency.

```
uv sync
uv run pytest -q                                   # 34 unit tests, ~1 s
uv run python run_sim.py --games 500 --hands 20    # 10,000 hands vs the practice field, ~7 s
uv run python run_sim.py --no-memory --seed 3      # wipe profiles between games
uv run python diagnostics.py                       # full health check, ~110 s (--quick is faster)
uv run python dashboard.py                         # browser dashboard: http://localhost:8000
                                                   # how Yosief decides: http://localhost:8000/transparency
```

`diagnostics.py` exits non-zero if any check fails. **Run it after every change.**

---

## 3. Folder layout

```
pokerbot/
├── README.md            this file
├── pyproject.toml       uv project (no runtime deps; pytest in the dev group, pytest config)
├── uv.lock, .python-version, .gitignore
│
├── protocol.py          shared types: Event, GameState, action tuples   ─┐
├── cards.py             card parsing + direct 5–7 card evaluator (~10 µs) │ THE BOT
├── ranges.py            range strings, hand keys, positions, PERCENTILE  │
├── profiles.py          opponent modeling (shrinkage, priors, decay)     │
├── strategy.py          decisions: pure function of state + profiles     │
├── bot.py               TightProfiler + NAME ('YOSIEF'): lifecycle, profiling, crash-proof ┘
├── adapter.py           the ONLY file that knows the dealer's wire format (template)
│
├── bots/                contestant bots (§11): auto-discovered, one file per person
│   ├── __init__.py      CONTESTANTS registry (YOSIEF + every bots/*.py), PROBLEMS
│   └── starter.py       STARTER: a simple working bot to copy
│
├── engine.py            local dealer (side pots, legality, timing)       ─┐
├── opponents.py         practice field: 7 archetype bots (incl. RIVERKING)│ TEST HARNESS
├── run_sim.py           simulation runner + CLI report                   │
├── dashboard.py         stdlib HTTP server + /api/run, /api/match, /api/bots, /api/abe│
├── dashboard.html       single-file dashboard UI (no external deps)      │
├── transparency.html    "how Yosief decides" page (live ranges + constants) │
├── diagnostics.py       health check across everything                   │
└── tests/test_bot.py    unit tests                                       ┘
```

All imports are flat (`from strategy import decide`), so run everything from inside
`pokerbot/`. pytest's `pythonpath = ["."]` handles the tests.

History: this folder used to sit inside an unrelated research repo, now
`../wiki-attention-volatility/`, and was split out on 2026-10-27. Nothing here depends on that
repo. `~/Downloads/pokerbot.zip` is an older snapshot of this folder and can be ignored.
Our bot was called ABE until 2026-10-27 and is now **Yosief** (`bot.NAME = 'YOSIEF'`). Older notes
and measurements that mention ABE refer to the same bot.

---

## 4. Architecture

```
dealer ──► adapter.py ──► bot.py (TightProfiler) ──┬─► profiles.py  (memory: who plays how)
           translate        new_hand / observe /    └─► strategy.py  (pure decision)
           in and out       act / end_hand                 ├─ ranges.py
                                                           └─ cards.py
```

### The four decisions that matter most (keep them)
1. **One file knows the protocol.** `adapter.py` turns dealer messages into `Event` and
   `GameState` objects, and our action tuples back into replies. To play through a different
   dealer program, fill in its four `translate_*` functions. Nothing else changes.
2. **Decisions are a pure function.** `strategy.decide(state, book)` keeps no hidden state, so
   any situation can be tested from a hand-built `GameState` in one line (see
   `tests/test_bot.py::state`). All memory lives in `profiles.ProfileBook`.
3. **The bot can't crash or act illegally.**
   - Every public method of `TightProfiler` is wrapped in try/except. A failed `act()` returns
     check, or fold if checking isn't allowed.
   - `strategy.legalize()` maps any intended action onto a legal one: raises are clamped to the
     legal range, the bot never folds when checking is free, and a raise that would leave less
     than 30% behind becomes a shove.
   - **Raise cap** (`raise_cap`, `POT_CAP = 2`): no raise adds more than 2× the pot, even with
     AA. The only exception is a re-raise (a preflop 3-bet or more, or a postflop raise of a
     raise) with `AA, KK, AKs`. An unraised preflop pot is 1.5bb, so a standard open is 3bb.
   - **All-in rule** (`decide`, `all_in_ok`): after the flop, Yosief only goes all-in, by raising
     or by calling, with trips or better. With one or two pair, it goes all-in only when every
     opponent left in the hand is loose (`Profile.is_loose`). It never goes all-in when the
     board plays. Otherwise it calls or checks instead, or folds if calling would itself be
     all-in.
   - Profiling bugs can't break decisions. Against weak bots, a timeout or crash costs more than
     any exploit gains.
4. **Profiles use shrinkage, a prior hierarchy and decay.** That's how "constantly adapting"
   works here (details in §5.3). Profiles are keyed by **bot name, not seat**, so reads carry
   across games.

### Conventions every module shares (`protocol.py`)
- **Cards** are 2-character strings: rank `23456789TJQKA` plus suit `cdhs`, e.g. `'As'`, `'Td'`.
  `cards.parse_card` normalizes dealer formats such as `'10h'`, `'TH'` and `'A♠︎'`.
- **Actions** are tuples: `('fold',)`, `('check',)`, `('call',)`, `('raise', to)`.
  **Raise amounts are "raise TO" street totals,** not increments. If the dealer wants
  increments, convert in `adapter.translate_action`.
- **`Event`** is one action anyone took: `player, street, kind` (blind, fold, check, call, bet or
  raise), `amount` (chips this action added), `to` (the player's street total afterwards),
  `pot_before` and `to_call_before`. Every bot sees every event, including its own.
- **`GameState`** is everything one decision needs:
  - `pot` includes the current street's bets.
  - `to_call`, plus `min_raise_to` and `max_raise_to` (both street totals; `max` means all-in).
  - `stack` is chips behind; `committed` is this street's chips in.
  - `position`, `positions` (name to position), `opponents` (still in the hand) and `history`
    (this hand's events).
- **Positions** for 8 players run UTG, UTG1, MP, HJ, CO, BTN, SB, BB. With fewer players the
  earliest seats drop off (`ranges.position_name`). `ranges.GROUP` maps them to `early`,
  `middle`, `late`, `sb` and `bb`.
- **Hand keys:** `'AKs'`, `'AKo'`, `'TT'`. Range strings: `'88+, AQ+, A2s-A9s, KQ, KJs'`
  (`ranges.expand`). `'KQ'` means both suited and offsuit.
- **Big blind = 100.** Results are reported in big blinds per 100 hands (bb/100).

---

## 5. The strategy in detail

### 5.1 Preflop (`strategy.preflop`)

| Situation | Rule |
|---|---|
| Small pairs `22–77` (`SMALL_PAIRS`), any seat | **Set-mining hands.** Limp (call 1bb) unopened or behind limpers; never isolate with them. The one exception is a steal: folded to the CO or button, they raise like any steal hand. Facing any raise or re-raise: call if the stack behind is at least 10× the call (`SET_MINE_MULT`), else fold. After the flop they continue only with a set or better (see §5.2) |
| Early (UTG, UTG1) | Raise `88+, AQ+` (`EARLY_TIGHT`). **Wider, `88+, AJ+, KQs` (about 7%), only when a calling station is still to act** and a maniac doesn't act right after us: a caller behind pays off the wider opens. **If every opponent still to act folds to preflop raises over 75%** (`EARLY_WIDE_FOLD`): open the top 15% minus small pairs (`EARLY_WIDE`). This never triggers in the practice roster, because a full table always has a loose bot behind; it's meant for tighter opponents and is unmeasured |
| Unopened, middle (MP, HJ) | Raise `88+, AJ+` |
| Unopened, late, SB, BB | Raise `88+, AT+` |
| Folded to us on the CO or button (second to last and last to act before the blinds) | **Raise to steal** (`STEAL_SEAT`): the top 27% of hands on the CO and the top 35% on the button, by `ranges.top()` (Chen ranking). This triggers rarely in the practice field, because a loose bot has usually limped first, and it measured about neutral |
| Folded to us in the SB | Also steal with `KQ, KJs, QJs, A2s–A9s`. Widen (`STEAL_WIDE`) if the big blind folds to raises more than 65% of the time. **Never** steal when a calling station is in the big blind |
| Open size | 4bb, plus 1bb per limper, plus 1bb per loose player among the limpers, then capped at 2× the pot (so 3bb unopened) |
| A loose player limped (station, maniac, `loose`, or tilting) | **Play the top 27% of hands** (`VS_LOOSE = top(0.27)`): raise to isolate from any seat |
| Facing one raise | Re-raise `QQ+, AK` and call `TT–JJ, AQ`. Against a nit: re-raise `KK+`, call `TT–QQ, AK`. Against a maniac, a `loose` bot or a tilting raiser: **play the top 27%**, re-raising `99+, AJ+, KQs` and calling the rest |
| A loose player's raise is over 20bb | Stack-off decision: call with `88+, AT+, KQs` (`STACK_OFF_VS_LOOSE`), fold the rest (small pairs follow the set-mining rule) |
| Set mining with `88–99` | Call a raise if 10 × the call ≤ effective stack (a set flops about 1 time in 8.5) |
| BB defense | Call a raise of 2.5bb or less with `BB_DEFEND` |
| Facing a re-raise | Shove `KK+, AKs`; call `QQ, AKo` if the call is at most 35% of the effective stack; fold the rest. If the re-raiser is loose: shove `TT+, AQ+`, call `88–99, AJs, KQs`. Shoves of anything but `AA, KK, AKs` are held to the 2× pot cap |

### 5.2 Postflop (`strategy.classify` and `strategy.postflop`)

`classify(hole, board)` sorts a hand into one of five buckets, counting **only what the hole
cards add** beyond the board:

- **monster:** straight or better, a set from a pocket pair, or two pair using both hole cards
  with top pair.
- **strong:** an overpair, top pair with a ten-or-better kicker, trips (not a set), or two pair
  that doesn't include top pair.
- **medium:** other pairs (an underpair, middle pair, top pair with a weak kicker), or an
  overpair on a paired board.
- **draw:** a 4-card flush draw or an 8-out straight draw, on the flop or turn.
- **air:** everything else, including hands where the board plays.

**Not facing a bet**, the bucket decides:

| Bucket | Not facing a bet |
|---|---|
| monster | Bet 75% of the pot. With a station in: 150% heads-up or 3-way, 100% with more players. Heads-up against a maniac before the river: check and let it bet |
| strong | Bet 60%. With a station in: 100% heads-up or 3-way, 75% with more players. Against a maniac: check |
| medium | **Never bets.** Check |
| draw | Semi-bluff 50% if bluffing is allowed (it has outs) |
| air | **Never bets.** Check |

**Small pairs (`22–77`) override the buckets:** without a set or better (the `monster` bucket),
they check and fold to any bet on every street, however small the bet.

**Facing a bet:**
- **monster:** raise to 3×.
- **Against a nit's (or sizing-read "strong") bet of 75%+ of the pot on the turn or river:** fold.
- **Everything else is an equity decision** (`strategy.equity`). Deal every opponent still in
  the hand a hand from the top `vpip` share of starting hands (ranked by the Chen formula in
  `ranges.PERCENTILE`, at least the top 5%), run out the board 300 times, and measure the share
  of the pot Yosief wins. That counts Yosief's own outs *and* every card that helps them, such as a 7
  or 3 filling someone's full house on a 7-3-7 board. **Call if equity ≥ pot odds**, plus 5
  points unless the bettor looks bluffy. Pot odds are `to_call / (pot + to_call)`, so a
  pot-sized bet needs 33%.
- Example: 55 on 7♥3♦7♣ has about 65% equity heads-up against a loose bot, but about 22% against
  four opponents, so it folds multiway.
- The simulation is seeded from the cards, so `decide` stays deterministic. It takes 4–20 ms
  per decision (diagnostics p99 about 20 ms, worst about 35 ms).

**Bluffing is allowed** only when no calling station is in the pot, there are at most 2
opponents, and the least-folding opponent folds to bets more than 55% of the time (65% on the
river). A bettor counts as **"bluffy"** if it's a maniac, has a river-bluff rate above 35%, is
tilting, or has a sizing tell that reads "weak".

### 5.3 Opponent profiles (`profiles.py`; recorded in `bot._observe` and `bot._end_hand`)

- **Shrinkage.** Each stat is `(hits + prior·K) / (opportunities + K)` with `K = 8`. With no data
  the bot assumes an average opponent and gradually trusts the real numbers.
- **A prior hierarchy.** `vpip_late` shrinks toward the player's `vpip`, which shrinks toward the
  **field average** (`ProfileBook.population`), which starts from `DEFAULTS`. Small samples
  borrow strength from the level above.
- **Decay.** Each update multiplies old counts by `DECAY = 0.997`, roughly the last 330
  observations. The field average uses `0.9995`. A bot that changes gears gets re-read.
- **Stats:**
  - `vpip` (plays a hand), split by position group.
  - `pfr` (raises preflop).
  - `fold_to_raise_pf`.
  - `fold_to_bet`, split by street.
  - `agg` (share of postflop actions that are bets or raises).
  - `river_bluff` (river aggression that showed down air or a draw).
  - `vpip_after_swing` (play rate in the 6 hands after a ±20bb pot).
  - Bet-sizing samples split by what was shown.
- **Labels** (`Profile.kind()`, only after 12 hands), checked in this order:
  - `nit`: plays under 12% of hands.
  - `maniac`: aggression above 45% and plays over 35%.
  - `station`: plays over 40% and folds to bets under 35%.
  - `folder`: folds to bets over 60%.
  - `loose`: plays over 33% of hands (`LOOSE_VPIP`). This catches loose bots that aren't
    stations or maniacs. Before this label existed, RANDOM, the field's biggest loser, was read
    as a `regular`.
  - otherwise `regular`.
  - `Profile.is_loose()` is true for `maniac`, `station`, `loose` or a tilting player. The
    widened preflop rules key off it.
- **Tilt.** `is_tilting()` is true within 6 hands of a big swing, but **only if** this player's
  measured post-swing play rate exceeds its normal rate by more than 12 points. The bot measures
  tilt; it doesn't assume it.
- **Sizing tells.** `sizing_read(frac)` returns `strong` or `weak` once there are at least 3
  showdown samples of each and their average bet sizes differ by at least 25% of the pot.

---

## 6. The test harness

- **`engine.py`:** a local dealer with side pots (`award`), odd-chip rules, per-bot error,
  illegal-action and latency counters, and `Table.last_hand` for logging. A bot that throws or
  returns something illegal is folded (or checked, if free). Simplifications: stacks reset every
  hand, and an all-in smaller than a full raise still reopens the betting.
- **`opponents.py`:** the practice field, one bot per common player archetype:
  - `RIVERKING`: never folds and never raises. It checks whenever it can and calls whenever it
    must, every street through the river, whatever it holds.
  - `STATION`: calls almost everything. It's on the roster but benched by default, because
    tables seat 8 (Yosief plus 7).
  - **Roster** (`opponents.ROSTER`, `DEFAULT_FIELD`, `MAX_OPPONENTS = 7`): every bot has a fixed
    seed offset, so results stay reproducible whatever lineup is chosen. Run a lineup with
    `run(..., bots=('NIT', 'MANIAC'))`, or seat and bench bots from the dashboard.
  - `NIT`: plays only QQ+ and AK.
  - `MANIAC`: raises 45% of hands preflop and bets 60% of the time postflop.
  - `LIMPER`: loose and passive.
  - `TILTER`: plays like a regular, then like a maniac for 8 hands after losing 20bb.
  - `REGULAR`: a solid tight-aggressive bot.
  - `RANDOM`: random actions, plus garbage replies and exceptions, to test robustness.

  They're deliberately simple. **A good result against them doesn't mean the bot will do well
  against stronger or different bots.**
- **`run_sim.py`:** `run(games, hands, seed, memory, on_hand=None)` reshuffles seats each game,
  rotates the button and asserts that chips are conserved every hand. The CLI prints the
  leaderboard and Yosief's reads.
- **`dashboard.py` and `dashboard.html`:**
  - `GET /api/run?games=&hands=&seed=&memory=&bots=` returns per-bot totals, Yosief's reads and a
    compact log of every hand: all hole cards, board, actions as `[bot, street, kind, amount, to,
    pot_before]`, nets, and Yosief's postflop buckets. Runs are capped at 50,000 hands.
  - `bots` is an optional comma list of 1–7 roster names (default `DEFAULT_FIELD`). Unknown or
    duplicate names, or more than 7, return 400. `GET /api/bots` lists the roster with
    descriptions.
  - **"At the table" row:** click a bot to bench or seat it, then run. Each bot keeps a fixed
    color. The lineup is remembered in the browser (localStorage). With fewer than 7 opponents,
    the earliest seats (UTG, then UTG1) drop off and the position chart hides them.
  - The page shows summary tiles, a cumulative chart for all bots (click to open a hand), win
    rate by position with a 95% range per seat (whiskers and ±, so one-seed noise is visible),
    outcome and bucket breakdowns, a 13×13 starting-hand grid, the leaderboard
    beside Yosief's reads, and a hand browser with a step-by-step replay (arrow keys work;
    `#hand=N` links to a hand).
  - Light and dark themes are tokenized. There are no external dependencies.
- **`transparency.html`** (`/transparency`, linked from the dashboard header as "Learn more about
  Yosief"): every rule, formula and threshold Yosief uses, in the order it applies them, plus the design
  decisions and their measured effects.
  - Every hand range (as a 13×13 grid), the Chen hand ranking and every tunable constant are
    loaded from `GET /api/abe` (`dashboard.abe_spec()`), which reads them from `strategy`,
    `profiles` and `bot`. Changing a range or constant in code updates the page automatically.
  - The explanations, the label thresholds and the bet fractions are written into the page. **If
    you change that logic, update the page too.** The "Design decisions" table is a dated
    snapshot of measurements.
  - Diagnostics check that the page serves and that `/api/abe` matches the code.
- **`diagnostics.py`** checks:
  - the unit tests;
  - 4 seeds × 10,000 hands with profiles kept and reset;
  - 20,000 fuzzed `GameState`s (every action must be legal, no exceptions, latency);
  - malformed input;
  - the adapter end to end over stdin/stdout;
  - the dashboard API (chips conserved, no duplicate cards, log matches totals, custom lineups
    seated, bad requests and bad lineups rejected).

---

## 7. What has been measured (as of 2026-10-27)

### Latest: loosening against loose bots, plus equity-based calls
Measured on 8 seeds (0–7) × 10,000 hands each, with profiles kept:

| Version | Mean bb/100 | Std. error | Finish |
|---|---|---|---|
| Before (tight only) | +173.3 | 7.9 | 4th on 6 of 8 seeds |
| + `loose` label and wider preflop vs loose players | +269.2 | 7.7 | 2nd–3rd |
| + equity vs pot odds when facing a bet | +300.0 | 12.3 | 2nd on 6 of 8, 3rd on 2 |
| + small pairs limp and set-mine, fold unless set | +300.4 | 11.4 | 2nd–3rd (one 4th) |
| + raise cap at 2× pot | +311.2 | 11.0 | **1st on 2**, 2nd on 4, 3rd on 2 |
| + all-in only with trips+, two pair only vs loose (strict) | +188.4 | 11.5 | 4th on 6 of 8 |
| … variant A: never *shove* below trips, but calling all-in is left to equity | +307.1 | 14.3 | |
| … variant B as first measured: anything may go all-in when every opponent left is loose | +308.5 | 12.4 | |
| Trips+ always; one or two pair only vs all-loose; never when the board plays | +278.1 | 7.6 | 2nd on 5 of 8, 3rd on 3 |
| + top 27% vs loose, CO/button steals, bigger bets vs stations, weak hands never bet | **+347.6** | 10.0 | |

**New field (RIVERKING replaces STATION):**

| Version | Mean bb/100 | Std. error | Finish |
|---|---|---|---|
| Previous Yosief (+278 above) | +420.6 | 10.5 | |
| **Current** | **+492.0** | 14.0 | **1st on 7 of 8**, 2nd on 1 |

- **Breakdown of the latest batch on the old field.** Each change was switched off one at a time
  from the combined version:
  - Top 27% against loose players: +22 bb/100.
  - CO/button steals: about 0. They rarely trigger, because a loose bot has usually limped first.
  - The strict "continue only with a strong hand or outs" rule: about −77 bb/100. Folding every
    medium hand to any bet loses cheap, winning calls against MANIAC and RANDOM.
- **What shipped instead:** medium hands and air never bet, but facing a bet they continue only
  if `equity()` says their outs beat the pot odds.
- Yosief now plays 18.6% of hands, up from 12%. It plays about 27% when a loose player is in.
- **Read the new field honestly.** Much of the jump to +492 is RIVERKING being a bigger donor
  than STATION: the old Yosief gains 140 bb/100 from the field change alone. The strategy changes
  are worth about +70 on either field.
- Diagnostics on the new field (4 seeds): profiles kept versus reset averaged 468 vs 296 bb/100
  (+172).
- **Wider early opens** (default field). The three pieces were measured separately: wider opens
  (A), wider vs folders (B), and tight when a maniac is next (C).

  | Version | Seeds | Overall bb/100 | UTG+UTG1 bb/100 |
  |---|---|---|---|
  | Before | 0–7 | +492.0 | +367 ± 48 |
  | A only | 0–7 | +477.6 | +424 ± 51 |
  | A + C (current; B never triggers) | 0–7 | +489.6 | +446 ± 52 |
  | Before | 8–23 | +464.5 | +332 ± 33 |
  | A + C (current) | 8–23 | +485.9 | +390 ± 35 |

  Over all 24 seeds, that's about +13 bb/100 overall and about +60 in the early seats. Yosief plays
  13% of hands there, up from 12%. Diagnostics: profiles kept versus reset 490 vs 286 (+203).
- **Without RIVERKING** (6 bots, 7-handed, so UTG1 is the earliest seat), seeds 0–7:
  - overall: +205.8 before, +202.0 after;
  - UTG1: +165 ± 37 before, +127 ± 36 after.

  So the gain comes from having a never-folding caller behind: wider opens get paid by worse
  hands. Without one, the change is neutral to slightly negative, within noise.
- **So the wide early range now needs a station still to act (current).** Measured on the same
  seeds:
  - with RIVERKING: identical to the unconditional version on all 24 seeds (overall +489.6,
    +492.3 and +479.5 on seeds 0–7, 8–15 and 16–23; early seats +446, +384 and +397);
  - without RIVERKING: identical to the original (+205.8 overall, +165 at UTG1), so the small
    loss is gone.

  Diagnostics: profiles kept versus reset 490 vs 273 (+216). One diagnostics run showed a
  single 239 ms decision. Three reruns of the same fixed-seed states measured 25–27 ms worst,
  so it was a system stall.
- **By seat, before the early-open change** (8 seeds, about 10,000 hands each, 95% ±70–90):
  - UTG +390, UTG1 +344, MP +488, HJ +473, CO +496, BTN +561, SB +506, BB +678.
  - The early seats trail, as expected out of position. UTG1 isn't worse than UTG; a single
    seed's UTG1 at +206 was noise.
  - At UTG1, every action Yosief takes wins. Open-raising first in earns about +44 bb a time, and
    iso-raising a limper about +16. It folds 84% of hands there, and that 84% is the real cost.

- **Small pairs** earned +14.8 bb per hand dealt, up from +11.1 (+32k vs +24k bb over 8 seeds).
  They saw more flops (1,709 vs 1,296) and flopped more sets (230 vs 162). A 15× set-mining
  multiple measured +303.7, the same as 10× within noise.
- **The all-in rule as written costs about 120 bb/100.** Before it, Yosief went all-in after the
  flop with one pair 711 times over the 8 seeds and won +48k bb, mostly calling the junk shoves
  of MANIAC and RANDOM. Two-pair all-ins fell from 506 (+37k bb) to 114 (+11k bb).
- **Variant B was chosen, limited to one pair or better.** The roughly 30 bb/100 gap to the
  first-measured variant B is calling loose players' all-ins with hands where the board plays
  (e.g. ace-high on a paired board), plus noise. Those calls are left out on purpose.

- The equity margin was tested at 0, 0.05 and 0.10 (289, 300 and 298 bb/100). The differences
  are noise, so 0.05 was kept, with no further tuning to avoid overfitting the practice field.
- **Reads now matter:** across the 4 diagnostic seeds, profiles kept versus reset averaged
  309 vs 230 bb/100 (+79). Before these changes the difference was +10, within noise.
- **LIMPER still finishes first** (about +320 to +420). It plays about 35% of hands; Yosief plays
  noticeably fewer.
- **Robustness:** 0 errors and 0 illegal actions. Decision latency is now p99 about 20 ms and
  worst about 35 ms, up from under 2 ms, because of the equity simulation. If decisions ever
  need to be faster, lower `EQUITY_SAMPLES`.

### Earlier baseline (tight only)
- **Win rate:** +157 to +211 bb/100 across 4 seeds × 10,000 hands, each with a 95% range of
  about ±47.
- **Finish:** Yosief was **4th of 8 every time.** LIMPER, REGULAR and TILTER finished ahead of it,
  because they play more hands against the two donors (MANIAC and RANDOM, at about −500 bb/100
  each).
- **Robustness:** 0 errors and 0 illegal actions. The slowest decision took under 2 ms.
- **Reads worked but barely moved results.** Profiles kept versus reset averaged 183 vs 173
  bb/100, which is within noise. The labels were correct (station, nit and maniac were
  identified), and the tilter's measured post-swing shift was about +12 points while every
  other bot stayed near 0.
- **Where the money comes from** (seed 0):
  - Showdowns won: +29.8k bb.
  - Monster and strong hands: about +21k bb.
  - Folding 90.6% of hands preflop costs only −3.4k bb.
  - Medium hands, draws and air each lose slightly.

  The tight core (chart, value betting, discipline) is nearly all of the edge.
- **Bug found by diagnostics and fixed:** an unparseable dealer line after an `act` used to make
  the adapter send an extra fold, i.e. an action out of turn. Regression test:
  `test_adapter_bad_line_after_act_sends_nothing`.

---

## 8. Known gaps and future plans (in priority order)

1. **Board texture when betting and raising.** Calls now use equity, which sees board texture.
   But bets and the monster raise still use `classify`, which doesn't: a set on a
   four-to-a-flush board is still a "monster" and gets raised 3×. A next step is to use
   `equity()` for the monster raise and for value bets.
2. **Multiway betting.** Facing a bet, equity accounts for the number of opponents. When Yosief
   bets first, though, top pair counts as "strong" against one opponent or four. Keep bluffing
   near zero multiway.
3. **Sharper opponent ranges.** `equity()` uses each opponent's preflop `vpip` range and ignores
   what they did after the flop, so a regular who bets gets the same range as one who checks.
   Narrowing the bettor's range by its `agg` would replace the flat 5-point margin.
4. **Widen against donors further.** The loose rules closed most of the gap, but LIMPER still
   wins more by playing more hands. Measure any change across **several seeds**, never just one.
5. **Steal frequency and image.** A small late-position steal frequency keeps Yosief from being a
   pure, readable nit if opponents profile it.
6. **Against tighter bots,** value from premiums shrinks and stealing is worth more, so widen
   `STEAL` and let fold-to-raise reads choose targets.
7. **Housekeeping:** keep the repo's `main` branch passing `diagnostics.py`.

When changing strategy: write a unit test on a hand-built `GameState`, run `diagnostics.py`, and
compare bb/100 across at least 4 seeds before believing an improvement.

---

## 9. Building a bot from scratch: a suggested order

Measured on this bench, this order gets the most value earliest. Keep a working bot after every
step.

1. **Safety.** Legal-action clamping and a check-or-fold fallback on any error. A bot that only
   checks or folds is a valid, if losing, starting point.
2. **Preflop.** A base range, opening ranges by position, raise sizing, and "facing a raise"
   ranges. This alone is a winning tight bot.
3. **Postflop.** Hand-strength buckets and a default action for each. This needs a hand
   evaluator (`cards.evaluate`, or a stripped-down ranker for pairs, two pair, trips and so on).
4. **Profiles.** VPIP, fold-to-raise, fold-to-bet and aggression, plus station, nit, maniac and
   loose labels and their exploit rules. The loose-player preflop ranges were worth about
   +95 bb/100 here, the biggest single gain measured.
   - Then the equity call/fold (`strategy.equity`), worth about +30 bb/100. It needs a fast
     7-card evaluator.
5. **Refinements.** Position-split stats, tilt detection, sizing tells. The measurements say
   this layer adds the least.

---

## 10. Rules for whoever works on this next

- Don't let any module except `adapter.py` learn the dealer's format.
- Don't add hidden state to `strategy.py`. Memory belongs in `profiles.py`.
- Every new code path in `bot.py` must stay inside the existing try/except wrappers, and every
  action must go through `legalize()`.
- Keep the project free of runtime dependencies, so anyone can run it with just Python and uv.
- Treat the practice field as a sanity check, not as proof the bot beats stronger opponents.

---

## 11. Competing with a friend

Two (or more) people each build a bot and race them on the same bench. Everyone works in one
shared GitHub repo and edits **only their own file in `bots/`**. The simulator, the dashboard and
the diagnostics pick up every bot automatically.

### Setup (once, for a new player)
1. Install [uv](https://docs.astral.sh/uv/) and git, and accept the GitHub invite to the repo.
2. Get the code and run it:
   ```
   git clone https://github.com/YosiefAbraham/pokerbot.git
   cd pokerbot
   uv sync
   uv run python dashboard.py        # open http://localhost:8000
   ```

### Making your bot
1. Copy the starter bot: `cp bots/starter.py bots/<yourname>.py`.
2. Change `NAME` to your bot's name: 2–16 capital letters, digits or `_`, and not already taken.
3. Edit `act()`. The starter's docstring explains the four methods the dealer calls, what a
   `GameState` holds, and which helpers you can use. Raise amounts are "raise to" totals for the
   street.
4. Check it: `uv run python diagnostics.py`. The "Contestant bots" section fuzzes your bot on
   20,000 random states (every action must be legal, no exceptions, under 50 ms) and plays a
   1,000-hand session.
5. On the dashboard, pick your bot under **Playing as** to see its stats, or seat it under
   **Contestants** to play against it.

A file that fails to load (a syntax error, a missing `NAME`, a taken name) is skipped with a red
note on the dashboard instead of breaking everyone's bench. The engine folds any bot that
throws an error or returns an illegal action, so a buggy bot only hurts itself.

### Racing
- Seat both contestants plus practice bots (8 seats max), then press
  **Run match**.
- A match plays the lineup on several seeds at once and ranks every bot by its mean bb/100 with a
  95% range. The head-to-head line compares the two contestants seed by seed: "X is ahead" only
  when the gap is bigger than its ± range, otherwise "too close to call".
- From code: `dashboard.match(seeds, games, hands, memory, bots, hero)`, or
  `run(..., hero='STARTER', bots=(...))` for a single run. `GET /api/match?seeds=8&hero=&bots=`
  does the same over HTTP.

### Day to day
```
git pull                          # get everyone's latest bots
uv run python diagnostics.py      # make sure everything still passes
git add bots/<yourname>.py && git commit -m "..." && git push
```

### House rules
- Don't edit anyone else's bot file. Changes to shared files (engine, dashboard, practice bots)
  should be agreed, since they change everyone's results.
- Keep decisions under about 50 ms, so matches stay quick.
- Judge bots by matches (8+ seeds), not single runs. One run swings about ±45 bb/100 from luck.
- Bots must be Python for now. Another language would need a small wrapper that runs it as a
  separate program speaking JSON over stdin/stdout, as `adapter.py` does. That isn't built yet.

First measured match (8 seeds × 10,000 hands, YOSIEF vs STARTER plus 6 practice bots):
- with RIVERKING seated, YOSIEF is ahead by +131 ± 33 bb/100;
- without RIVERKING, +9 ± 23, too close to call.

So the starter is a real opponent to build from.
