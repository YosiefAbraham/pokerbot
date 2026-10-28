"""Contestant bots: Yosief (bot.NAME) plus every bots/*.py file that defines NAME and class Bot.

Drop a file in this folder (copy starter.py) and the simulator, dashboard and
diagnostics pick it up. A file that fails to load is skipped and listed in
PROBLEMS instead of breaking everything; diagnostics.py fails until it's fixed.
"""
import importlib
import pkgutil
import re
import traceback
from pathlib import Path

from bot import NAME, TightProfiler
from opponents import ROSTER

NAME_RULE = re.compile(r'^[A-Z][A-Z0-9_]{1,15}$')


def about(cls):
    """First line of a bot class's docstring, for the dashboard."""
    return ((cls.__doc__ or '').strip().splitlines() or [''])[0]


def discover(folder=Path(__file__).parent, package=__name__):
    found, problems = {NAME: TightProfiler}, []
    for info in sorted(pkgutil.iter_modules([str(folder)]), key=lambda m: m.name):
        if info.name.startswith('_'):
            continue
        where = f'bots/{info.name}.py'
        try:
            mod = importlib.import_module(f'{package}.{info.name}')
        except Exception:
            problems.append((where, traceback.format_exc(limit=3).strip().splitlines()[-1]))
            continue
        name, cls = getattr(mod, 'NAME', None), getattr(mod, 'Bot', None)
        if not isinstance(name, str) or cls is None:
            problems.append((where, 'needs NAME = "..." and class Bot'))
        elif not NAME_RULE.match(name):
            problems.append((where, f'NAME {name!r} must be 2-16 capital letters, digits or _'))
        elif name in found or (name in ROSTER and ROSTER[name][0] is not cls):
            problems.append((where, f'NAME {name!r} is already taken'))   # same bot in both is fine
        else:
            found[name] = cls
    return found, problems


CONTESTANTS, PROBLEMS = discover()
