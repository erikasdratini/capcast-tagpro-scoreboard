"""Dashboard/server agreement tests (needs Node.js; skipped without it).

The v1.1 'save poisoning' bug (F-01) came from the dashboard accepting data
the server refused. These tests run shared.js in Node and check that anything
the dashboard accepts, the server accepts too.
"""
import copy
import importlib.util
import json
import random
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

_spec = importlib.util.spec_from_file_location('capcast_server', str(ROOT / 'server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)
NODE = shutil.which('node')

RUNNER = r"""
globalThis.window = globalThis;
require(process.argv[2]);
const C = window.CAPCAST;
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {
  states: input.states.map((s) => { const e = C.validateState(s); return e ? e.path : null; }),
  logos: input.logos.map((l) => C.checkLogo(l)),
  clean: input.clean.map(([s, max, upper]) => C.cleanText(s, max, upper)),
  info: input.info.map((b64) => C.imageInfo(Buffer.from(b64, 'base64'))),
  sizes: input.sizes.map((s) => ({ measure: C.measure(s), ok: C.validateState(s) === null })),
};
process.stdout.write(JSON.stringify(out));
"""


def server_accepts(state):
    try:
        srv.validate_strict(state)
        return True
    except srv.BadRequest:
        return False


def server_text_ok(s, limit):
    try:
        srv._text(s, 'x', limit)
        return True
    except srv.BadRequest:
        return False


@unittest.skipUnless(NODE, 'Node.js not installed')
class TestParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = fx.v2_state()
        logo_ok = fx.data_url('png', fx.png_bytes(64, 64))
        muts = {
            'valid': lambda d: None,
            'mode-float-like': lambda d: d.update(mode=7),
            'ctrl-name': lambda d: d['teams'][0].update(name='A\x1fB'),
            'bom-only-name': lambda d: d['teams'][0].update(name='﻿'),
            'ideographic-space-name': lambda d: d['teams'][0].update(name='　'),
            'nbsp-name': lambda d: d['teams'][0].update(name=' '),
            'x85-name': lambda d: d['teams'][0].update(name='a\x85'),
            'emoji-110': lambda d: d['teams'][0].update(name='😀' * 110),
            'emoji-111': lambda d: d['teams'][0].update(name='😀' * 111),
            'combining-12': lambda d: d['teams'][0].update(code='É' * 6),
            'combining-13': lambda d: d['teams'][0].update(code='É' * 6 + 'X'),
            'lone-surrogate': lambda d: d['teams'][0].update(name='a\ud83d'),
            'extra-top': lambda d: d.update(extra=1),
            'extra-team': lambda d: d['teams'][0].update(extra=1),
            'missing-era': lambda d: d['teams'][0].pop('era'),
            'logo-ok': lambda d: d['teams'][0].update(logo=logo_ok),
            'logo-bad': lambda d: d['teams'][0].update(logo='data:image/png;base64,QQ'),
            'ot-hidden': lambda d: d['games'][0].update(scoreA=1, scoreB=1, ot=False, otWinner='A'),
            'ot-ok': lambda d: d['games'][0].update(scoreA=1, scoreB=1, ot=True, otWinner='A'),
            'score-float': lambda d: d['games'][0].update(scoreA=1.5),
            'score-neg': lambda d: d['games'][0].update(scoreA=-1),
            'scale-str': lambda d: d['meta'].update(scale='1'),
            'scale-low': lambda d: d['meta'].update(scale=0.59),
            'preset-dup': lambda d: d['presets'].append(copy.deepcopy(d['presets'][0])),
            'preset-6maps': lambda d: d['presets'][0]['maps'].pop(),
            'team-a-missing': lambda d: d.update(teamA='nope'),
            'schema-1': lambda d: d.update(schemaVersion=1),
            'no-teams': lambda d: d.update(teams=[]),
            'hex-lower': lambda d: d['teams'][0].update(primary='#abcdef'),
            'hex-short': lambda d: d['teams'][0].update(primary='#abc'),
            'map-71': lambda d: d['games'][0].update(map='m' * 71),
            'label-100-cjk': lambda d: d['meta'].update(label='漢' * 100),
        }
        cls.names = list(muts)
        cls.states = [fx.mutate(base, f) for f in muts.values()]
        big = copy.deepcopy(base)
        filler = fx.data_url('png', fx.png_bytes(8, 8) + b'\x00' * 520000)
        for i in range(5):
            big['teams'][i]['logo'] = filler
        while len(srv.dump_state(big)) < srv.LIMITS['stateBytes'] - 2000:
            big['teams'][5 + len(cls.names) % 20]['era'] = 'x'
            break
        cls.names.append('near-budget')
        cls.states.append(big)
        cls.logos = [logo_ok, fx.data_url('jpeg', fx.jpeg_header(10, 10)), fx.data_url('webp', fx.webp_vp8x(3000, 3)),
                     fx.data_url('png', fx.png_bomb(16000, 16000)), 'data:image/png;base64,QQ', 'data:image/png;base64,QQ==',
                     'data:image/png;base64,', 'data:image/gif;base64,R0lGODlh', fx.data_url('jpeg', fx.png_bytes(4, 4)),
                     'data:image/png;base64,' + 'A' * 800000, fx.data_url('webp', fx.webp_vp8l(2048, 2048)),
                     fx.data_url('webp', fx.webp_vp8l(2049, 1))]
        rnd = random.Random(1663)
        alphabet = ['a', 'Z', ' ', '\t', '\n', '\x00', '\x1f', '\x7f', '\x85', ' ', '　', '﻿', 'ß', 'ŉ', 'ﬃ',
                    'İ', 'ǰ', '😀', '\ud83d', '\ude00', 'é', '漢', '"', '<', '&']
        cls.clean = []
        for _ in range(600):
            s = ''.join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 30)))
            limit = rnd.choice([3, 12, 70, 110])
            cls.clean.append([s, limit, rnd.random() < 0.5])
        import base64
        cls.info_src = [fx.png_bytes(3, 4), fx.jpeg_header(640, 480, exif=True), fx.webp_vp8x(100, 7), fx.webp_vp8l(9, 9),
                        fx.webp_vp8(12, 34), b'\xff\xd8\xff\xda\x00\x02', b'garbage' * 10]
        # States straddling the 3.5 MB budget, with assorted rev/savedAt values and a float scale.
        import struct as _s, zlib as _z

        def padded(n):
            b = fx.png_bytes(8, 8)
            chunk = b'tEXt' + b'c\x00' + b'x' * n
            return b[:-12] + _s.pack('>I', len(chunk) - 4) + chunk + _s.pack('>I', _z.crc32(chunk) & 0xFFFFFFFF) + b[-12:]

        def fit(target):
            d = copy.deepcopy(base)
            for i in range(4):
                d['teams'][i]['logo'] = fx.data_url('png', padded(550000))
            d['teams'][10]['era'] = ''
            lo, hi = 0, 590000
            while lo < hi:                      # largest logo that keeps us at or under target - 40
                mid = (lo + hi + 1) // 2
                d['teams'][12]['logo'] = fx.data_url('png', padded(mid))
                if srv.measure(d) <= target - 40:
                    lo = mid
                else:
                    hi = mid - 1
            d['teams'][12]['logo'] = fx.data_url('png', padded(lo))
            d['teams'][10]['era'] = 'e' * (target - srv.measure(d))
            assert srv.measure(d) == target, (srv.measure(d), target)
            return d
        cls.sizes = []
        budget = srv.LIMITS['stateBytes']
        for delta in (-3, -1, 0, 1, 2, 5):
            d = fit(budget + delta)
            d['rev'], d['savedAt'] = (delta + 10) * 99991, (None if delta % 2 else '2026-11-11T21:11:44Z')
            d['meta']['scale'] = 1.15
            cls.sizes.append(d)
        payload = {'states': cls.states, 'logos': cls.logos, 'clean': cls.clean,
                   'info': [base64.b64encode(b).decode() for b in cls.info_src], 'sizes': cls.sizes}
        runner = HERE / '_parity_runner.js'
        runner.write_text(RUNNER, encoding='utf-8')
        try:
            p = subprocess.run([NODE, str(runner), str(ROOT / 'shared.js')], input=json.dumps(payload),
                               capture_output=True, text=True, timeout=120)
        finally:
            runner.unlink()
        if p.returncode:
            raise RuntimeError(p.stderr)
        cls.js = json.loads(p.stdout)

    def test_states_agree(self):
        mismatches = []
        for name, st, js_path in zip(self.names, self.states, self.js['states']):
            py_ok = server_accepts(st)
            js_ok = js_path is None
            if js_ok and not py_ok:
                mismatches.append('%s: dashboard accepts but server refuses (poison risk)' % name)
            elif py_ok != js_ok and name != 'near-budget':
                mismatches.append('%s: server %s, dashboard %s (%s)' % (name, py_ok, js_ok, js_path))
        self.assertEqual(mismatches, [])

    def test_logos_agree(self):
        for url, js_reason in zip(self.logos, self.js['logos']):
            self.assertEqual(srv.check_logo(url) is None, js_reason is None, (url[:60], js_reason))

    def test_clean_text_always_acceptable(self):
        for (s, limit, upper), out in zip(self.clean, self.js['clean']):
            self.assertTrue(server_text_ok(out, limit), (s, limit, upper, out))

    def test_size_budget_agrees_exactly(self):
        budget = srv.LIMITS['stateBytes']
        seen = set()
        for st, js in zip(self.sizes, self.js['sizes']):
            py_measure = srv.measure(st)
            self.assertEqual(js['measure'], py_measure)
            seen.add(py_measure - budget)
            self.assertEqual(js['ok'], server_accepts(st), py_measure - budget)
            self.assertEqual(js['ok'], py_measure <= budget)
        self.assertTrue(any(x <= 0 for x in seen) and any(x > 0 for x in seen), seen)

    def test_image_info_agrees(self):
        for raw, js in zip(self.info_src, self.js['info']):
            py = srv.image_info(raw)
            self.assertEqual(None if py is None else {'type': py[0], 'width': py[1], 'height': py[2]}, js)


if __name__ == '__main__':
    unittest.main()
