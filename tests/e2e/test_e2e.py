"""CAPCAST v1.1.1 browser tests (Playwright + Chromium). Development only.

    pip install playwright && python -m playwright install chromium
    python3 -B -m unittest tests.e2e.test_e2e -v

Set CAPCAST_CHROME to use an installed Chrome instead of Playwright's Chromium.
Each test starts its own server (python3 -I server.py) on a free port with an
empty data folder. Test names carry the regression IDs from the v1.1 review.
"""
import base64
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import fixtures as fx  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

CHROME = os.environ.get('CAPCAST_CHROME')
PY = os.environ.get('CAPCAST_PYTHON', sys.executable)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_until(fn, timeout=8.0, interval=0.1, message='condition'):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(interval)
    raise AssertionError('timed out waiting for %s (last: %r)' % (message, last))


class Server:
    def __init__(self, prepare=None, port=None, data=None):
        self.tmp = tempfile.mkdtemp(prefix='capcast-e2e-')
        self.data = Path(data) if data else Path(self.tmp) / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        if prepare:
            prepare(self.data)
        self.port = port or free_port()
        self.base = 'http://127.0.0.1:%d' % self.port
        self.start()

    def start(self):
        self.proc = subprocess.Popen([PY, '-I', '-B', str(ROOT / 'server.py'), '--port', str(self.port), '--no-browser',
                                      '--data-dir', str(self.data)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        wait_until(lambda: self._up(), 15, message='server start')

    def _up(self):
        try:
            socket.create_connection(('127.0.0.1', self.port), timeout=0.2).close()
            return True
        except OSError:
            return False

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def close(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def disk(self):
        return json.loads((self.data / 'state.json').read_text(encoding='utf-8'))


@unittest.skipUnless(sync_playwright, 'Playwright not installed')
class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        args = ['--host-resolver-rules=MAP evil.test 127.0.0.1, MAP rebind.test 127.0.0.1']
        kw = {'executable_path': CHROME} if CHROME else {}
        cls.browser = cls.pw.chromium.launch(headless=True, args=args, **kw)
        cls.files = Path(tempfile.mkdtemp(prefix='capcast-files-'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        shutil.rmtree(cls.files, ignore_errors=True)

    def setUp(self):
        self.server = None
        self.contexts = []
        self.console = []

    def tearDown(self):
        for c in self.contexts:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        if self.server:
            self.server.close()

    # helpers --------------------------------------------------------------
    def start(self, prepare=None):
        self.server = Server(prepare)
        return self.server

    def ctx(self, **kw):
        c = self.browser.new_context(viewport={'width': 1600, 'height': 1000}, accept_downloads=True, **kw)
        self.contexts.append(c)
        return c

    def watch(self, page, tag):
        page.on('console', lambda m: self.console.append((tag, m.type, m.text)))
        page.on('pageerror', lambda e: self.console.append((tag, 'pageerror', str(e))))
        page.on('dialog', lambda d: d.accept())
        return page

    def dashboard(self, ctx=None):
        c = ctx or self.ctx()
        d = self.watch(c.new_page(), 'dash')
        d.goto(self.server.base + '/')
        d.wait_for_function("() => /REV \\d+/.test(document.getElementById('save-status').textContent)", timeout=10000)
        return d

    def overlay(self, ctx=None, obs=True):
        c = ctx or self.ctx()
        o = self.watch(c.new_page(), 'obs' if obs else 'ov')
        if obs:
            o.add_init_script('window.obsstudio = { pluginVersion: "test" };')
        o.goto(self.server.base + '/overlay.html')
        o.wait_for_function("() => document.querySelectorAll('.score-row').length === 2", timeout=10000)
        return o

    def no_errors(self):
        bad = [c for c in self.console if c[1] in ('error', 'pageerror') and 'favicon' not in c[2]]
        bad += [c for c in self.console if 'Content Security Policy' in c[2]]
        self.assertEqual(bad, [])

    @staticmethod
    def type_score(page, game, side, value):
        sel = '[data-score-%s="%d"]' % (side, game)
        page.fill(sel, '')
        page.type(sel, value, delay=20)

    @staticmethod
    def rows(ov):
        return ov.evaluate("""() => [...document.querySelectorAll('.score-row')].map(r => ({
            code: r.querySelector('.team-code').textContent,
            boxes: [...r.querySelectorAll('.score-box')].map(b => ({v: b.querySelector('.score-value').textContent,
                win: b.classList.contains('game-winner'), pending: b.classList.contains('ot-pending'),
                mark: (b.querySelector('.overtime-mark') || {}).textContent || ''})),
            total: r.querySelector('.total-box').textContent, agg: r.querySelector('.agg-box').textContent }))""")

    def saved(self, d, timeout=8):
        wait_until(lambda: d.text_content('#save-status').startswith('SAVED'), timeout, message='SAVED status')

    def write_file(self, name, data):
        p = self.files / name
        p.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
        return str(p)

    # =================================================================== R-01
    def test_R01a_too_many_teams_refused_and_saving_continues(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        csv = 'code,name,primary,secondary,era\n' + ''.join('T%d,Team %d,#112233,#ffffff,Test\n' % (i, i) for i in range(701))
        d.click('[data-tab="teams"]')
        d.set_input_files('#import-library', self.write_file('t701.csv', csv))
        wait_until(lambda: 'Import failed' in d.text_content('#toast'))
        self.assertIn('more than 700', d.text_content('#toast'))
        self.assertIn('Nothing was changed', d.text_content('#toast'))
        self.assertEqual(d.evaluate('state.teams.length'), 30)
        d.click('[data-tab="match"]')
        self.type_score(d, 0, 'a', '4'); self.type_score(d, 0, 'b', '2')
        wait_until(lambda: self.rows(ov)[0]['boxes'][0]['v'] == '4', 4, message='overlay shows 4')
        self.saved(d)
        self.no_errors()

    def test_R01b_cumulative_size_budget_enforced_before_merge(self):
        self.start()
        d = self.dashboard()
        # 300x300 noise PNGs re-encode to about 349 KB each; 11 of them exceed the 3.5 MB budget.
        logos = [fx.data_url('png', fx.png_bytes(300, 300, noise=True)) for _ in range(11)]
        lib1 = {'teams': [{'code': 'L%d' % i, 'name': 'Logo %d' % i, 'logo': logos[i]} for i in range(5)]}
        lib2 = {'teams': [{'code': 'M%d' % i, 'name': 'More %d' % i, 'logo': logos[5 + i]} for i in range(6)]}
        d.click('[data-tab="teams"]')
        d.set_input_files('#import-library', self.write_file('lib1.json', json.dumps(lib1)))
        wait_until(lambda: 'Imported 5 new' in d.text_content('#toast'), 30)
        self.saved(d)
        d.set_input_files('#import-library', self.write_file('lib2.json', json.dumps(lib2)))
        wait_until(lambda: 'Import failed' in d.text_content('#toast'), 30)
        self.assertIn('too large', d.text_content('#toast'))
        self.assertEqual(d.evaluate('state.teams.length'), 35)
        d.click('[data-tab="match"]')
        self.type_score(d, 1, 'a', '5')
        self.saved(d)
        self.assertEqual(self.server.disk()['games'][1]['scoreA'], 5)

    def test_R01c_R01d_unicode_import_never_poisons(self):
        self.start()
        d = self.dashboard()
        csv = 'code,name,primary,secondary,era\n' + 'ßßßßßßßßßßßß,Strasse FC,#112233,#ffffff,Test\n' + 'EMO,' + 'a' * 109 + '😀,#112233,#ffffff,Test\n'
        d.click('[data-tab="teams"]')
        d.set_input_files('#import-library', self.write_file('uni.csv', csv))
        wait_until(lambda: 'Imported 2 new' in d.text_content('#toast'))
        self.saved(d)
        teams = self.server.disk()['teams']
        strasse = next(t for t in teams if t['name'] == 'Strasse FC')
        self.assertEqual(strasse['code'], 'S' * 12)
        emo = next(t for t in teams if t['code'] == 'EMO')
        self.assertEqual(len(emo['name']), 110)
        self.assertNotIn('\ud83d', emo['name'])

    def test_R01e_rejected_save_blocks_loudly_without_loop(self):
        self.start()
        d = self.dashboard()
        posts = []

        def reject(route):
            posts.append(time.time())
            route.fulfill(status=400, content_type='application/json', body=json.dumps({'error': 'test rejection', 'path': 'state.teams[1].code'}))
        d.route('**/api/state', lambda route: reject(route) if route.request.method == 'POST' else route.continue_())
        self.type_score(d, 0, 'a', '7')
        wait_until(lambda: not d.locator('#sync-banner').is_hidden(), 5)
        self.assertIn('OVERLAY NOT UPDATING', d.text_content('#sync-banner'))
        self.assertIn('test rejection', d.text_content('#sync-banner'))
        self.assertEqual(d.text_content('#save-status'), 'SAVE BLOCKED')
        n = len(posts)
        time.sleep(5)
        self.assertLessEqual(len(posts) - n, 0)
        self.assertNotIn('IN SYNC', d.text_content('#preview-status-text'))
        self.assertTrue(d.title().startswith('⚠'))
        d.unroute('**/api/state')
        d.click('#sync-banner-actions >> text=REVERT TO LAST SAVED')
        self.saved(d)
        self.assertEqual(d.input_value('[data-score-a="0"]'), '')

    # =================================================================== R-02
    def test_R02_second_dashboard_cannot_silently_overwrite(self):
        self.start()
        ov = self.overlay()
        d1 = self.dashboard()
        d2 = self.dashboard()
        # A clean second window follows the first one.
        self.type_score(d1, 0, 'a', '3'); self.type_score(d1, 0, 'b', '0')
        self.saved(d1)
        wait_until(lambda: d2.input_value('[data-score-a="0"]') == '3', 6, message='d2 auto-update')
        # Now freeze d2's status polling so it cannot see the next change, and edit in both.
        d2.route('**/api/status', lambda route: route.abort())
        self.type_score(d1, 1, 'a', '2'); self.type_score(d1, 1, 'b', '1')
        self.saved(d1)
        d2.fill('[data-game-map="0"]', 'Pilot'); d2.press('[data-game-map="0"]', 'Tab')
        wait_until(lambda: 'CONFLICT' in d2.text_content('#save-status'), 6)
        self.assertIn('CHANGED IN ANOTHER WINDOW', d2.text_content('#sync-banner'))
        disk = self.server.disk()
        self.assertEqual((disk['games'][1]['scoreA'], disk['games'][1]['scoreB']), (2, 1))
        self.assertNotEqual(disk['games'][0]['map'], 'Pilot')
        wait_until(lambda: self.rows(ov)[0]['boxes'][1]['v'] == '2', 4)
        d2.unroute('**/api/status')
        d2.click('#sync-banner-actions >> text=LOAD LATEST')
        wait_until(lambda: d2.input_value('[data-score-a="1"]') == '2', 4)
        self.saved(d2)
        wait_until(lambda: 'DASHBOARDS ARE OPEN' in d2.text_content('#sync-banner'), 6)

    # =================================================================== R-03
    def test_R03a_large_csv_refused_quickly(self):
        self.start()
        d = self.dashboard()
        big = 'code,name\n' + ''.join('%x,%x\n' % (i, i) for i in range(330000))
        path = self.write_file('big.csv', big[:2990000])
        d.click('[data-tab="teams"]')
        t0 = time.time()
        d.set_input_files('#import-library', path)
        wait_until(lambda: 'Import failed' in d.text_content('#toast'), 5)
        self.assertLess(time.time() - t0, 2.0)
        self.assertIn('too large', d.text_content('#toast'))

    def test_R03b_R03d_imported_logos_checked_and_reencoded(self):
        self.start()
        d = self.dashboard()
        jpeg_url = d.evaluate("""() => { const c = document.createElement('canvas'); c.width = 400; c.height = 300;
            const g = c.getContext('2d'); g.fillStyle = '#c33'; g.fillRect(0, 0, 400, 300); g.fillStyle = '#fff'; g.fillRect(50, 50, 100, 100);
            return c.toDataURL('image/jpeg', 0.9); }""")
        raw = base64.b64decode(jpeg_url.split(',', 1)[1])
        exif = b'Exif\x00\x00' + b'GPS-SECRET-42.4' * 10
        raw = raw[:2] + b'\xff\xe1' + (len(exif) + 2).to_bytes(2, 'big') + exif + raw[2:]
        lib = {'teams': [
            {'code': 'EXF', 'name': 'Exif Team', 'logo': fx.data_url('jpeg', raw)},
            {'code': 'BMB', 'name': 'Bomb Team', 'logo': fx.data_url('png', fx.png_bomb(16000, 16000))},
            {'code': 'MIS', 'name': 'Mismatch Team', 'logo': fx.data_url('jpeg', fx.png_bytes(10, 10))},
        ]}
        d.click('[data-tab="teams"]')
        d.set_input_files('#import-library', self.write_file('logos.json', json.dumps(lib)))
        wait_until(lambda: 'Imported 3 new' in d.text_content('#toast'), 20)
        self.assertIn('2 logos could not be used', d.text_content('#toast'))
        self.saved(d)
        teams = {t['code']: t for t in self.server.disk()['teams']}
        logo = teams['EXF']['logo']
        self.assertTrue(logo.startswith('data:image/png;base64,'))
        png = base64.b64decode(logo.split(',', 1)[1])
        self.assertNotIn(b'GPS-SECRET', png)
        w, h = int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')
        self.assertEqual((w, h), (256, 192))
        self.assertIsNone(teams['BMB']['logo'])
        self.assertIsNone(teams['MIS']['logo'])

    # =================================================================== R-04
    def test_R04_score_boxes_ignore_wheel_and_flag_invalid_input(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        self.type_score(d, 0, 'a', '3'); self.type_score(d, 0, 'b', '1')
        self.saved(d)
        box = d.locator('[data-score-a="0"]')
        box.focus()
        bb = box.bounding_box()
        d.mouse.move(bb['x'] + 5, bb['y'] + 5)
        d.mouse.wheel(0, -120); d.mouse.wheel(0, -120)
        d.keyboard.press('ArrowUp'); d.keyboard.press('ArrowDown'); d.keyboard.press('ArrowUp')
        time.sleep(1.2)
        self.assertEqual(box.input_value(), '3')
        self.assertEqual(self.server.disk()['games'][0]['scoreA'], 3)
        self.type_score(d, 0, 'a', '1000')
        self.assertEqual(box.input_value(), '100')                     # maxlength: the box shows what airs
        wait_until(lambda: self.rows(ov)[0]['boxes'][0]['v'] == '100', 4)
        for bad in ['3.5', '-2', '2e1', '1 0']:
            box.fill(bad)
            time.sleep(0.6)
            self.assertEqual(box.get_attribute('aria-invalid'), 'true', bad)
            self.assertIn('NOT PUBLISHED', d.text_content('#sync-banner'))
            self.assertEqual(d.text_content('#save-status'), 'NOT PUBLISHED')
            self.assertNotIn('IN SYNC', d.text_content('#obs-status'))
            self.assertEqual(self.rows(ov)[0]['boxes'][0]['v'], '100')
        self.type_score(d, 0, 'a', '７')                                # full-width digit
        wait_until(lambda: self.rows(ov)[0]['boxes'][0]['v'] == '7', 4)
        self.saved(d)
        self.assertEqual(d.get_attribute('[data-score-a="0"]', 'type'), 'text')

    # =================================================================== R-05
    def test_R05a_tie_needs_ot_winner(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        for i, (a, b) in enumerate([(3, 1), (1, 3), (2, 0), (0, 2), (2, 2)]):
            self.type_score(d, i, 'a', str(a)); self.type_score(d, i, 'b', str(b))
        self.saved(d)
        text = d.text_content('#completed-text')
        self.assertIn('4 of 5 games completed', text)
        self.assertNotIn('Series complete', text)
        self.assertIn('waiting for an OT winner', text)
        wait_until(lambda: self.rows(ov)[0]['boxes'][4]['pending'], 4)
        self.assertIn('4 / 5 GAMES COMPLETED', ov.text_content('.broadcast-bottom'))
        self.assertEqual(d.text_content('[data-ot-note="4"]'), 'TIED: TICK OT GAME')
        d.check('[data-ot="4"]')
        wait_until(lambda: d.text_content('[data-ot-note="4"]') == 'PICK OT WINNER')
        d.select_option('[data-ot-winner="4"]', 'B')
        self.saved(d)
        wait_until(lambda: self.rows(ov)[1]['boxes'][4]['mark'] == 'OT WIN', 4)
        r = self.rows(ov)
        self.assertEqual((r[0]['total'], r[1]['total']), ('2', '3'))
        # The OT winner gets one extra cap on air and in the aggregate; the stored 2-2 stays.
        self.assertEqual((r[0]['boxes'][4]['v'], r[1]['boxes'][4]['v']), ('2', '3'))
        self.assertEqual((r[0]['agg'], r[1]['agg']), ('8', '9'))
        self.assertIn('Series complete', d.text_content('#completed-text'))
        self.assertEqual((d.input_value('[data-score-a="4"]'), d.input_value('[data-score-b="4"]')), ('2', '2'))
        disk = self.server.disk()['games'][4]
        self.assertEqual((disk['scoreA'], disk['scoreB'], disk['otWinner']), (2, 2, 'B'))
        d.select_option('[data-ot-winner="4"]', 'A')                       # change winner: no double count
        self.saved(d)
        wait_until(lambda: self.rows(ov)[0]['boxes'][4]['v'] == '3', 4)
        r = self.rows(ov)
        self.assertEqual((r[1]['boxes'][4]['v'], r[0]['agg'], r[1]['agg']), ('2', '9', '8'))
        self.assertIn('9 total caps', d.text_content('#live-totals'))          # dashboard aggregate agrees
        d.uncheck('[data-ot="4"]')
        self.saved(d)
        self.assertIsNone(self.server.disk()['games'][4]['otWinner'])
        # Extra cap removed; the undecided tie is pending again, so (as before) it is not in the aggregate.
        wait_until(lambda: self.rows(ov)[0]['agg'] == '6', 4)
        r = self.rows(ov)
        self.assertEqual((r[0]['boxes'][4]['v'], r[1]['boxes'][4]['v'], r[1]['agg']), ('2', '2', '6'))

    # =================================================================== R-06
    def test_R06_crafted_state_file_never_renders(self):
        bad = fx.v11_state()
        bad['games'][0]['scoreA'] = ('"><b id=inj style="position:fixed;inset:0">FAKE</b><img src=x onerror="window.__x=1">'
                                     '<meta http-equiv="refresh" content="1;url=http://evil.test/phish">')
        self.start(lambda data: (data / 'state.json').write_text(json.dumps(bad), encoding='utf-8'))
        d = self.dashboard()
        time.sleep(2.5)
        self.assertTrue(d.url.startswith(self.server.base))
        self.assertFalse(d.evaluate("!!document.getElementById('inj') || !!window.__x"))
        wait_until(lambda: 'could not be loaded' in d.text_content('#notice-panel'), 5)
        self.assertEqual(len(list((self.server.data / 'quarantine').glob('state-invalid-*.json'))), 1)

    # =================================================================== R-07 / R-14
    def test_R07_R14_other_websites_blocked(self):
        self.start()
        evil_dir = Path(tempfile.mkdtemp())
        (evil_dir / 'evil.html').write_text('<!doctype html><title>evil</title><body>evil</body>')
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(evil_dir), **k)  # noqa: E731
        evil = socketserver.TCPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=evil.serve_forever, daemon=True).start()
        try:
            before = (self.server.data / 'state.json').read_bytes()
            target = self.server.base
            st = self.server.disk()
            body = json.dumps({'baseRev': st['rev'], 'state': st})
            for origin in ['http://127.0.0.1:%d' % evil.server_address[1], 'http://evil.test:%d' % evil.server_address[1]]:
                p = self.ctx().new_page()
                p.goto(origin + '/evil.html')
                res = p.evaluate("""async ([t, body]) => {
                  const o = {};
                  const load = (tag, attrs) => new Promise(r => { const el = document.createElement(tag); Object.assign(el, attrs);
                    el.onload = () => r('onload'); el.onerror = () => r('onerror'); document.head.appendChild(el); setTimeout(() => r('timeout'), 4000); });
                  o.script = await load('script', {src: t + '/app.js'});
                  o.style = await load('link', {rel: 'stylesheet', href: t + '/styles.css'});
                  o.ran = typeof CAPCAST !== 'undefined';
                  try { await fetch(t + '/api/state'); o.read = 'readable'; } catch (e) { o.read = 'blocked'; }
                  try { const r = await fetch(t + '/api/state', {method: 'POST', mode: 'no-cors', headers: {'Content-Type': 'text/plain'}, body}); o.post = r.type; } catch (e) { o.post = 'blocked'; }
                  try { await fetch(t + '/api/state', {method: 'POST', headers: {'Content-Type': 'application/json'}, body}); o.json = 'sent'; } catch (e) { o.json = 'blocked'; }
                  o.beacon = navigator.sendBeacon(t + '/api/state', new Blob([body], {type: 'text/plain'}));
                  await new Promise(r => { const f = document.createElement('iframe'); f.src = t + '/'; f.onload = r; document.body.appendChild(f); setTimeout(r, 3000); });
                  try { o.frame = document.querySelector('iframe').contentWindow.document.title; } catch (e) { o.frame = 'blocked'; }
                  const w = window.open(t + '/', '_blank');
                  await new Promise(r => setTimeout(r, 2500));
                  o.openerSevered = !w || w.closed;
                  return o; }""", [target, body])
                self.assertEqual(res['script'], 'onerror', origin)
                self.assertEqual(res['style'], 'onerror', origin)
                self.assertFalse(res['ran'])
                self.assertEqual(res['read'], 'blocked')
                self.assertEqual(res['json'], 'blocked')
                self.assertEqual(res['frame'], 'blocked')
                self.assertTrue(res['openerSevered'], res)
                time.sleep(0.5)
                self.assertEqual((self.server.data / 'state.json').read_bytes(), before)
            p = self.ctx().new_page()
            r = p.goto('http://rebind.test:%d/' % self.server.port)
            self.assertEqual(r.status, 403)
            self.assertEqual((self.server.data / 'state.json').read_bytes(), before)
        finally:
            evil.shutdown()
            evil.server_close()
            shutil.rmtree(evil_dir, ignore_errors=True)

    # =================================================================== R-10
    def test_R10_logo_upload_works_with_strict_csp(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        d.click('[data-tab="teams"]')
        d.click('[data-edit-team="nban"]')
        d.set_input_files('#edit-logo', self.write_file('logo.png', fx.png_bytes(512, 300)))
        d.click('#save-team')
        wait_until(lambda: 'Team saved' in d.text_content('#toast'), 10)
        self.saved(d)
        logo = next(t for t in self.server.disk()['teams'] if t['id'] == 'nban')['logo']
        png = base64.b64decode(logo.split(',', 1)[1])
        self.assertEqual((int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')), (256, 150))
        wait_until(lambda: ov.evaluate("!!document.querySelector('.team-logo-img') && document.querySelector('.team-logo-img').naturalWidth === 256"), 5)
        self.no_errors()

    # =================================================================== R-13
    def test_R13_overlay_fits_reported_size(self):
        self.start()
        d = self.dashboard()
        self.assertEqual(d.get_attribute('[data-ot-winner="0"]', 'aria-label'), 'Overtime winner for game 1')
        for mode in (5, 7):
            d.click('[data-mode="%d"]' % mode)
            for pct in (100, 125, 150):
                d.evaluate("v => { const el = document.getElementById('overlay-scale'); el.value = v; el.dispatchEvent(new Event('input')); }", pct)
                self.saved(d)
                w = int(re.match(r'(\d+)', d.text_content('#obs-width')).group(1))
                h = int(re.match(r'(\d+)', d.text_content('#obs-height')).group(1))
                c = self.ctx()
                p = c.new_page()
                p.set_viewport_size({'width': w, 'height': h})
                p.goto(self.server.base + '/overlay.html')
                p.wait_for_function("() => document.querySelectorAll('.score-row').length === 2")
                bw, bh = p.evaluate("(() => { const r = document.getElementById('broadcast').getBoundingClientRect(); return [Math.ceil(r.width), Math.ceil(r.height)]; })()")
                self.assertLessEqual(bw, w, (mode, pct))
                self.assertLessEqual(bh, h, (mode, pct))
                c.close()
        d.fill('#meta-label', 'GRAND FINAL')
        d.click('[data-mode="5"]'); d.click('[data-mode="7"]')
        self.assertEqual(d.input_value('#meta-label'), 'GRAND FINAL')

    # =================================================================== R-15
    def test_R15_xss_polyglot_everywhere(self):
        self.start()
        P = '"\'><img src=x onerror="window.__x=1"><svg onload=window.__x=1><b id=inj>B</b><style>*{}</style>${7*7}'
        d, ov = self.dashboard(), self.overlay()
        for sel in ['#meta-league', '#meta-event', '#meta-label']:
            d.fill(sel, P)
        d.fill('[data-game-map="0"]', P); d.press('[data-game-map="0"]', 'Tab')
        d.click('[data-tab="teams"]'); d.click('#new-team')
        d.fill('#edit-name', P); d.fill('#edit-code', P[:12]); d.fill('#edit-era', P); d.click('#save-team')
        d.click('[data-tab="maps"]'); d.fill('#edit-preset-name', P); d.fill('[data-preset-map="0"]', P); d.click('#save-map-preset')
        rows = [{'code': P[:12], 'name': P, 'era': P, 'primary': '#123456"><b id=inj2>', 'secondary': 'red;background:url(//evil.test/x)',
                 'logo': 'data:image/png;base64,AAAA"onerror="x'}]
        d.click('[data-tab="teams"]')
        d.set_input_files('#import-library', self.write_file('x.json', json.dumps({'teams': rows})))
        wait_until(lambda: 'Imported' in d.text_content('#toast'))
        tid = d.evaluate('id => state.teams.find(t => t.name.startsWith(id)).id', P[:20])
        d.evaluate('id => { state.teamA = id; markDirty(); renderMatch(); }', tid)
        d.click('[data-tab="match"]')
        self.saved(d)
        time.sleep(1.5)
        for page in (d, ov):
            r = page.evaluate("({x: window.__x || 0, inj: !!document.querySelector('#inj,#inj2'), img: document.querySelectorAll('img[src=\"x\"]').length, svg: document.querySelectorAll('svg').length})")
            self.assertEqual(r, {'x': 0, 'inj': False, 'img': 0, 'svg': 0})
        self.assertIn(P[:30], ov.text_content('#broadcast'))
        self.no_errors()

    # ============================================= sync indicators (req. 6)
    def test_sync_indicator_tracks_obs(self):
        self.start()
        d = self.dashboard()
        wait_until(lambda: d.text_content('#obs-status') == 'OBS NOT CONNECTED', 6)
        ov = self.overlay(obs=True)
        self.type_score(d, 0, 'a', '1'); self.type_score(d, 0, 'b', '0')
        self.saved(d)
        wait_until(lambda: d.text_content('#obs-status').startswith('OBS IN SYNC'), 6)
        rev = d.evaluate('sync.serverRev')
        self.assertEqual(d.text_content('#obs-status'), 'OBS IN SYNC · REV %d' % rev)
        # OBS stops receiving updates: the dashboard must say so, loudly, within seconds.
        ov.route('**/api/state', lambda route: route.abort())
        self.type_score(d, 1, 'a', '2'); self.type_score(d, 1, 'b', '0')
        self.saved(d)
        wait_until(lambda: d.text_content('#obs-status') in ('OBS SHOWING OLD SCORES', 'OBS ERROR'), 8)
        self.assertIn('sync-bad', d.get_attribute('#obs-status', 'class'))
        self.assertFalse(d.locator('#sync-banner').is_hidden())
        ov.unroute('**/api/state')
        wait_until(lambda: d.text_content('#obs-status').startswith('OBS IN SYNC'), 8)
        # Never "in sync" while the dashboard holds unpublished edits.
        d.route('**/api/state', lambda route: None if route.request.method == 'POST' else route.continue_())
        self.type_score(d, 2, 'a', '9')
        time.sleep(1.5)
        self.assertNotIn('IN SYNC', d.text_content('#obs-status'))
        self.assertNotIn('IN SYNC', d.text_content('#preview-status-text'))

    def test_server_restart_recovers_pending_edits(self):
        self.start()
        d = self.dashboard()
        self.server.stop()
        self.type_score(d, 0, 'a', '6')
        wait_until(lambda: 'SERVER NOT RESPONDING' in d.text_content('#sync-banner'), 8)
        self.assertIn('OFFLINE', d.text_content('.server-pill'))
        self.server.start()
        self.saved(d, 20)
        self.assertEqual(self.server.disk()['games'][0]['scoreA'], 6)

    # ============================== independent review findings (RV-xx)
    def test_RV01_restart_with_other_data_never_shows_false_sync(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        self.type_score(d, 0, 'a', '5'); self.type_score(d, 0, 'b', '0')
        self.saved(d)
        wait_until(lambda: d.text_content('#obs-status').startswith('OBS IN SYNC'), 6)
        port = self.server.port
        self.server.stop()
        second = Server(port=port)                    # same port, fresh data folder (rev 0, no scores)
        try:
            # The dashboard learns about outside changes by polling once a second, so it may lag for a moment;
            # what must never happen is a lasting false "IN SYNC". Allow at most 2.5 s of lag in total.
            false_since, worst = None, 0.0
            end = time.time() + 6
            while time.time() < end:
                pill = d.text_content('#obs-status')
                mine = d.evaluate('state.games[0].scoreA')
                on_air = self.rows(ov)[0]['boxes'][0]['v']
                if pill.startswith('OBS IN SYNC') and str(mine if mine is not None else '—') != on_air:
                    false_since = false_since or time.time()
                    worst = max(worst, time.time() - false_since)
                else:
                    false_since = None
                time.sleep(0.1)
            self.assertLess(worst, 2.5)
            wait_until(lambda: d.evaluate('state.games[0].scoreA') is None and d.text_content('#obs-status').startswith('OBS IN SYNC'), 8)
            # Same again with an unsaved edit in the dashboard, and the new server using other saved data
            # (here: rev 1 with a different savedAt). The dashboard must show a conflict, not overwrite it.
            second.stop()
            self.type_score(d, 1, 'a', '9')

            def other_data(folder):
                other = fx.v2_state()
                other['rev'] = 1
                other['savedAt'] = '2001-01-01T00:00:00Z'
                other['games'][1]['scoreA'] = 4
                (folder / 'state.json').write_text(json.dumps(other), encoding='utf-8')
            third = Server(port=port, prepare=other_data)
            try:
                wait_until(lambda: 'CAPCAST WAS RESTARTED' in d.text_content('#sync-banner'), 10)
                time.sleep(2.5)                         # give any retry time to (wrongly) overwrite
                self.assertEqual(third.disk()['games'][1]['scoreA'], 4)
                self.assertFalse(d.text_content('#obs-status').startswith('OBS IN SYNC'))
                # "Load saved" resolves it to what the server (and OBS) has.
                d.click('#sync-banner-actions button:has-text("LOAD LATEST")')
                wait_until(lambda: d.evaluate('state.games[1].scoreA') == 4, 5)
            finally:
                third.close()
        finally:
            second.close()

    def test_RV03_wide_or_long_csv_refused_quickly(self):
        self.start()
        d = self.dashboard()
        d.click('[data-tab="teams"]')
        wide = ',' * 499999 + 'name\n' + 'x\n' * 249000
        t0 = time.time()
        d.set_input_files('#import-library', self.write_file('wide.csv', wide[:999000]))
        wait_until(lambda: 'more than 64 columns' in d.text_content('#toast'), 5)
        self.assertLess(time.time() - t0, 3)
        d.evaluate("document.getElementById('toast').textContent = ''")
        d.set_input_files('#import-library', self.write_file('long.csv', 'code,name\n' + 'A,B\n' * 1401))
        wait_until(lambda: 'more than 1400 rows' in d.text_content('#toast'), 5)
        self.assertEqual(d.evaluate('state.teams.length'), 30)

    def test_RV05_team_editor_keeps_other_windows_changes(self):
        self.start()
        a, b = self.dashboard(), self.dashboard()
        for p in (a, b):
            p.click('[data-tab="teams"]'); p.click('[data-edit-team="mtpa"]')
        b.fill('#edit-primary', '#ff0000'); b.click('#save-team')
        self.saved(b)
        wait_until(lambda: a.evaluate("state.teams.find(t => t.id === 'mtpa').primary") == '#ff0000', 6)
        a.fill('#edit-name', 'Renamed In A'); a.click('#save-team')
        self.saved(a)
        team = next(t for t in self.server.disk()['teams'] if t['id'] == 'mtpa')
        self.assertEqual((team['name'], team['primary']), ('Renamed In A', '#ff0000'))
        # Both rename: the second save is refused with a clear message, the first rename is kept.
        wait_until(lambda: b.evaluate("state.teams.find(t => t.id === 'mtpa').name") == 'Renamed In A', 6)
        a.click('[data-edit-team="mtpa"]'); b.click('[data-edit-team="mtpa"]')
        b.fill('#edit-name', 'B Name'); b.click('#save-team')
        self.saved(b)
        wait_until(lambda: a.evaluate("state.teams.find(t => t.id === 'mtpa').name") == 'B Name', 6)
        a.fill('#edit-name', 'A Name'); a.click('#save-team')
        wait_until(lambda: 'Not saved: name also changed' in a.text_content('#toast'), 5)
        self.assertEqual(next(t for t in self.server.disk()['teams'] if t['id'] == 'mtpa')['name'], 'B Name')
        self.assertEqual(a.input_value('#edit-name'), 'B Name')

    def test_RV06_dock_with_obsstudio_is_not_an_obs_source(self):
        self.start()
        c = self.ctx()
        c.add_init_script('window.obsstudio = { pluginVersion: "dock" };')   # applies to every frame, like an OBS dock
        d = self.dashboard(c)
        time.sleep(4)
        self.assertEqual(d.text_content('#obs-status'), 'OBS NOT CONNECTED')

    def test_RV12_map_name_published_while_typing(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        d.click('[data-game-map="0"]')
        d.fill('[data-game-map="0"]', 'Typed Map')            # no blur
        wait_until(lambda: 'Typed Map' in ov.text_content('.heading-row'), 5)
        self.saved(d)
        self.assertEqual(self.server.disk()['games'][0]['map'], 'Typed Map')

    # ============================================== functional (req. 12)
    def test_functional_scores_swap_clear_mode(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        for i, (a, b) in enumerate([(3, 1), (1, 2), (2, 2)]):
            self.type_score(d, i, 'a', str(a)); self.type_score(d, i, 'b', str(b))
        d.check('[data-ot="2"]'); d.select_option('[data-ot-winner="2"]', 'A')
        self.saved(d)
        wait_until(lambda: self.rows(ov)[0]['total'] == '2', 4)
        r = self.rows(ov)
        self.assertEqual([b['v'] for b in r[0]['boxes']], ['3', '1', '3', '—', '—'])   # 2-2, A wins in OT
        self.assertEqual((r[0]['agg'], r[1]['agg']), ('7', '5'))
        self.assertEqual(r[0]['boxes'][2]['mark'], 'OT WIN')
        codes = (r[0]['code'], r[1]['code'])
        d.click('#swap-teams')
        self.saved(d)
        wait_until(lambda: self.rows(ov)[0]['code'] == codes[1], 4)
        r = self.rows(ov)
        self.assertEqual([b['v'] for b in r[0]['boxes'][:3]], ['1', '2', '2'])
        self.assertEqual(r[1]['boxes'][2]['mark'], 'OT WIN')
        d.fill('#meta-label', '')
        d.click('[data-mode="7"]')
        self.assertEqual(d.input_value('#meta-label'), 'PLAYOFFS')
        self.type_score(d, 6, 'a', '4'); self.type_score(d, 6, 'b', '4')
        self.saved(d)
        wait_until(lambda: len(self.rows(ov)[0]['boxes']) == 7, 4)
        d.click('[data-mode="5"]')
        self.saved(d)
        wait_until(lambda: len(self.rows(ov)[0]['boxes']) == 5, 4)
        self.assertEqual(self.server.disk()['games'][6]['scoreA'], 4)        # hidden games kept
        d.click('#clear-scores')
        self.saved(d)
        wait_until(lambda: all(b['v'] == '—' for b in self.rows(ov)[0]['boxes']), 4)
        self.assertTrue(list((self.server.data / 'backups').glob('before-clear-scores-*.json')))
        self.no_errors()

    def test_functional_team_library_and_presets(self):
        self.start()
        d, ov = self.dashboard(), self.overlay()
        d.click('[data-tab="teams"]'); d.click('#new-team')
        d.fill('#edit-name', 'Caplanders'); d.fill('#edit-code', 'cpl'); d.fill('#edit-era', 'S40 · Majors')
        d.fill('#edit-primary', '#aa3355'); d.fill('#edit-secondary', '#ffeedd')
        d.click('#save-team')
        wait_until(lambda: 'Team saved' in d.text_content('#toast'))
        tid = d.evaluate("state.teams.find(t => t.name === 'Caplanders').id")
        self.assertEqual(d.evaluate("state.teams.find(t => t.name === 'Caplanders').code"), 'CPL')
        d.click('[data-tab="match"]'); d.click('#choose-a')
        d.click('[data-pick-team="%s"]' % tid)
        wait_until(lambda: self.rows(ov)[0]['code'] == 'CPL', 5)
        d.click('[data-tab="teams"]'); d.click('[data-edit-team="%s"]' % tid)
        d.fill('#edit-name', 'Caplanders FC'); d.click('#save-team')
        wait_until(lambda: 'Caplanders FC' in ov.text_content('.score-row'), 5)
        d.click('[data-edit-team="nraa"]'); d.click('#delete-team')
        self.saved(d)
        self.assertNotIn('nraa', [t['id'] for t in self.server.disk()['teams']])
        csv = 'code,name,primary,secondary,era\nNEW,New Imports,#123456,#abcdef,S40\nMSNI,Snipe Hunt,#000000,#ffffff,Updated\n'
        d.set_input_files('#import-library', self.write_file('lib.csv', csv))
        wait_until(lambda: 'Imported 1 new and updated 1' in d.text_content('#toast'))
        with d.expect_download() as dl:
            d.click('#export-library')
        exported = json.loads(Path(dl.value.path()).read_text(encoding='utf-8'))
        self.assertIn('New Imports', [t['name'] for t in exported['teams']])
        d.click('[data-tab="maps"]'); d.click('#new-map-preset')
        d.fill('#edit-preset-name', 'Season 40 rotation')
        for i, m in enumerate(['Pilot', 'Constriction', 'Iron', 'EMERALD', 'Transilio', 'Cloud', 'Wormy']):
            d.fill('[data-preset-map="%d"]' % i, m)
        d.click('#save-map-preset')
        self.saved(d)
        d.click('[data-tab="match"]')
        pid = d.evaluate("state.presets.find(p => p.name === 'Season 40 rotation').id")
        d.select_option('#match-preset', pid); d.click('#apply-preset')
        self.saved(d)
        wait_until(lambda: 'PILOT' in ov.text_content('.heading-row').upper(), 5)
        d.click('[data-tab="maps"]'); d.select_option('#map-preset-select', pid); d.click('#delete-map-preset')
        self.saved(d)
        self.assertNotIn(pid, [p['id'] for p in self.server.disk()['presets']])
        self.no_errors()

    def test_functional_backup_restore_including_v11(self):
        self.start()
        d = self.dashboard()
        self.type_score(d, 0, 'a', '3'); self.type_score(d, 0, 'b', '2')
        self.saved(d)
        with d.expect_download() as dl:
            d.click('#export-backup')
        backup_path = dl.value.path()
        self.type_score(d, 0, 'a', '9')
        self.saved(d)
        d.evaluate("document.getElementById('toast').textContent = ''")
        d.set_input_files('#import-backup', str(backup_path))
        wait_until(lambda: 'Backup restored' in d.text_content('#toast'), 8)
        self.assertEqual(d.input_value('[data-score-a="0"]'), '3')
        v11 = fx.v11_state()
        v11['games'][3].update(scoreA=1, scoreB=4)
        d.evaluate("document.getElementById('toast').textContent = ''")
        d.set_input_files('#import-backup', self.write_file('v11-backup.json', json.dumps(v11, indent=2)))
        wait_until(lambda: 'Backup restored' in d.text_content('#toast'), 8)
        disk = self.server.disk()
        self.assertEqual(disk['schemaVersion'], 2)
        self.assertEqual((disk['games'][3]['scoreA'], disk['games'][3]['scoreB']), (1, 4))
        self.assertEqual(len(list((self.server.data / 'backups').glob('before-restore-*.json'))), 2)
        self.no_errors()


if __name__ == '__main__':
    unittest.main()
