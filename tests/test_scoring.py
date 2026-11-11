"""Overtime scoring (needs Node.js; skipped without it).

The overtime winner of a tied game gets exactly one extra cap on air and in the
aggregate. Stored regulation scores never change, and the extra cap is derived,
so changing or clearing the winner can never double-count.
"""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import fixtures as fx  # noqa: E402

NODE = shutil.which('node')
RUNNER = r"""
globalThis.window = globalThis;
require(process.argv[1]);   // with node -e, argv[1] is the first argument
const C = window.CAPCAST;
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = input.map((d) => {
  const before = JSON.stringify(d);
  const games = d.games.slice(0, d.mode).map((g) => { const st = C.gameState(g); return [st.capsA, st.capsB, st.winner]; });
  const s = C.stats(d);
  return { games, stats: s, unchanged: JSON.stringify(d) === before };
});
process.stdout.write(JSON.stringify(out));
"""


def match(mode, games):
    d = fx.v2_state()
    d['mode'] = mode
    for i, g in enumerate(d['games']):
        a, b, ot, w = games[i] if i < len(games) else (None, None, False, None)
        g.update(scoreA=a, scoreB=b, ot=ot, otWinner=w)
    return d


@unittest.skipUnless(NODE, 'Node.js not installed')
class TestOvertimeScoring(unittest.TestCase):
    def run_js(self, states):
        r = subprocess.run([NODE, '-e', RUNNER, str(ROOT / 'shared.js')], input=json.dumps(states),
                           capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def test_ot_winner_gets_exactly_one_cap(self):
        a_wins = match(5, [(2, 2, True, 'A')])
        b_wins = match(5, [(2, 2, True, 'B')])
        ra, rb = self.run_js([a_wins, b_wins])
        self.assertEqual(ra['games'][0], [3, 2, 'A'])
        self.assertEqual(rb['games'][0], [2, 3, 'B'])
        self.assertEqual((ra['stats']['capsA'], ra['stats']['capsB'], ra['stats']['winsA'], ra['stats']['winsB']), (3, 2, 1, 0))
        self.assertEqual((rb['stats']['capsA'], rb['stats']['capsB'], rb['stats']['winsA'], rb['stats']['winsB']), (2, 3, 0, 1))
        self.assertTrue(ra['unchanged'] and rb['unchanged'])        # stored 2-2 is never rewritten

    def test_no_extra_cap_without_a_decided_overtime(self):
        cases = [match(5, [(2, 2, False, 'A')]),     # OT unticked: a stored winner is ignored
                 match(5, [(2, 2, True, None)]),     # OT ticked, no winner yet
                 match(5, [(3, 2, True, 'B')]),      # not tied: scores stand, no extra cap
                 match(5, [(0, 0, True, 'B')])]      # a 0-0 game decided in OT shows 0-1
        r = self.run_js(cases)
        self.assertEqual(r[0]['games'][0], [2, 2, None])
        self.assertEqual(r[1]['games'][0], [2, 2, None])
        self.assertEqual((r[0]['stats']['capsA'], r[0]['stats']['completed']), (0, 0))   # pending games add nothing
        self.assertEqual(r[2]['games'][0], [3, 2, 'A'])
        self.assertEqual((r[2]['stats']['capsA'], r[2]['stats']['capsB'], r[2]['stats']['winsA']), (3, 2, 1))
        self.assertEqual(r[3]['games'][0], [0, 1, 'B'])

    def test_changing_the_winner_recalculates_without_double_counting(self):
        d = match(5, [(2, 2, True, 'A')])
        flips = []
        for w in ('B', 'A', 'B', None):
            d = copy.deepcopy(d)
            d['games'][0]['otWinner'] = w
            flips.append(d)
        r = self.run_js(flips)
        self.assertEqual([(x['stats']['capsA'], x['stats']['capsB']) for x in r], [(2, 3), (3, 2), (2, 3), (0, 0)])

    def test_five_and_seven_game_aggregates(self):
        games = [(3, 1, False, None), (2, 2, True, 'B'), (1, 4, True, 'A'), (2, 2, True, 'A'), (0, 2, False, None),
                 (5, 5, True, 'B'), (1, 0, False, None)]
        five, seven = self.run_js([match(5, games), match(7, games)])
        # 5 games: regulation 8-11, plus OT caps for B (game 2) and A (game 4); games 6-7 are hidden and not counted
        self.assertEqual((five['stats']['capsA'], five['stats']['capsB']), (9, 12))
        self.assertEqual((five['stats']['winsA'], five['stats']['winsB'], five['stats']['completed']), (2, 3, 5))
        # 7 games: plus 5-6 (5-5 with B winning in OT) and 1-0
        self.assertEqual((seven['stats']['capsA'], seven['stats']['capsB']), (15, 18))
        self.assertEqual((seven['stats']['winsA'], seven['stats']['winsB'], seven['stats']['completed']), (3, 4, 7))


if __name__ == '__main__':
    unittest.main()
