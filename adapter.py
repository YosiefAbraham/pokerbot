"""Protocol adapter: the ONLY file that knows the dealer's message format.

To play through a different dealer program, fill in the four translate_*
functions below for its message format. Nothing else changes.

This example speaks JSON lines on stdin/stdout, to show the shape:
messages in -> Event / GameState, our tuple out -> reply.
"""
import json
import sys

from bot import NAME, TightProfiler
from cards import parse_card
from protocol import Event, GameState

BOT = TightProfiler(NAME)


def translate_new_hand(msg):
    # TODO: map the dealer's seat/button info to {name: 'BTN'|'SB'|'BB'|'UTG'|...}
    # ranges.position_name(offset_from_button, n_players) does the naming.
    positions = msg['positions']
    hole = [parse_card(c) for c in msg['hole']]
    return msg.get('hand_id', 0), positions, hole


def translate_event(msg):
    # TODO: the dealer may say 'raise 600' (to) or 'raise by 400' (increment): convert to ours.
    return Event(player=msg['player'], street=msg['street'], kind=msg['kind'],
                 amount=msg['amount'], to=msg['to'], pot_before=msg['pot_before'],
                 to_call_before=msg['to_call_before'])


def translate_state(msg, history):
    return GameState(
        me=BOT.name, hole=[parse_card(c) for c in msg['hole']],
        board=[parse_card(c) for c in msg['board']], street=msg['street'],
        pot=msg['pot'], to_call=msg['to_call'], min_raise_to=msg['min_raise_to'],
        max_raise_to=msg['max_raise_to'], stack=msg['stack'], committed=msg['committed'],
        big_blind=msg.get('big_blind', 100), position=msg['position'],
        positions=msg['positions'], opponents=msg['opponents'], history=history,
        stacks=msg.get('stacks', {}), bets=msg.get('bets', {}))


def translate_action(action):
    # TODO: our ('raise', to) is a street total; convert if the dealer wants an increment.
    if action[0] == 'raise':
        return {'action': 'raise', 'amount': action[1]}
    return {'action': action[0]}


def main():
    history = []
    for line in sys.stdin:
        msg = None        # never let a bad line fall back on the previous message
        try:
            msg = json.loads(line)
            t = msg.get('type')
            if t == 'new_hand':
                history = []
                BOT.new_hand(*translate_new_hand(msg))
            elif t == 'event':
                ev = translate_event(msg)
                history.append(ev)
                BOT.observe(ev)
            elif t == 'act':
                reply = translate_action(BOT.act(translate_state(msg, history)))
                print(json.dumps(reply), flush=True)
            elif t == 'end_hand':
                BOT.end_hand(msg['nets'], msg.get('shown', {}), msg.get('board', []))
        except Exception:
            # Never die on a bad message. If we were asked to act, still answer.
            if isinstance(msg, dict) and msg.get('type') == 'act':
                print(json.dumps({'action': 'check' if msg.get('to_call', 1) == 0 else 'fold'}),
                      flush=True)


if __name__ == '__main__':
    main()
