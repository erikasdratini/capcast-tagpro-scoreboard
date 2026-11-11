"""CAPCAST v1.1.1 server tests (standard library only).

Run from the project folder:  python3 -B -m unittest discover -s tests -v
Test names carry the regression IDs (R-xx) from the v1.1 security assessment.
"""
import copy
import errno
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
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
srv.say = lambda *a: None   # keep test output quiet


class Live:
    """An in-process CAPCAST server on a free port with its own data folder."""

    def __init__(self, data_dir=None, prepare=None):
        self.tmp = tempfile.mkdtemp(prefix='capcast-test-')
        self.data = Path(data_dir or os.path.join(self.tmp, 'data'))
        if prepare:
            self.data.mkdir(parents=True, exist_ok=True)
            prepare(self.data)
        self.store = srv.Store(self.data)
        self.store.load_initial()
        self.httpd = srv.LocalServer(('127.0.0.1', 0), srv.Handler, self.store, srv.load_static())
        self.port = self.httpd.server_port
        self.host = '127.0.0.1:%d' % self.port
        self.origin = 'http://' + self.host
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # raw HTTP so tests control every header ---------------------------------
    def raw(self, data, timeout=10):
        s = socket.create_connection(('127.0.0.1', self.port), timeout=timeout)
        try:
            s.sendall(data)
            buf = b''
            while True:
                try:
                    c = s.recv(65536)
                except (socket.timeout, ConnectionResetError):
                    break
                if not c:
                    break
                buf += c
            return buf
        finally:
            s.close()

    def request(self, method, path, headers=None, body=b'', host='default'):
        lines = ['%s %s HTTP/1.1' % (method, path)]
        if host == 'default':
            lines.append('Host: ' + self.host)
        elif host is not None:
            lines.append('Host: ' + host)
        hdrs = dict(headers or {})
        if body and 'Content-Length' not in hdrs:
            hdrs['Content-Length'] = str(len(body))
        for k, v in hdrs.items():
            if isinstance(v, list):
                lines.extend('%s: %s' % (k, x) for x in v)
            else:
                lines.append('%s: %s' % (k, v))
        lines.append('Connection: close')
        resp = self.raw(('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1') + body)
        head, _, rest = resp.partition(b'\r\n\r\n')
        status_line = head.split(b'\r\n', 1)[0].decode('latin-1')
        status = int(status_line.split()[1]) if status_line.startswith('HTTP/') else 0
        headers_out = {}
        for line in head.split(b'\r\n')[1:]:
            k, _, v = line.decode('latin-1').partition(':')
            headers_out.setdefault(k.strip().lower(), []).append(v.strip())
        return status, headers_out, rest

    def get(self, path, headers=None, host='default'):
        return self.request('GET', path, headers, host=host)

    def post(self, path, obj, origin='default', ctype='application/json', headers=None, host='default'):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode('utf-8')
        h = {'Content-Type': ctype} if ctype else {}
        if origin == 'default':
            h['Origin'] = self.origin
        elif origin is not None:
            h['Origin'] = origin
        h.update(headers or {})
        return self.request('POST', path, h, body, host=host)

    def state(self):
        status, _, body = self.get('/api/state')
        assert status == 200, status
        return json.loads(body)

    def save(self, state, base=None, checkpoint=None):
        body = {'baseRev': self.state()['rev'] if base is None else base, 'state': state}
        if checkpoint:
            body['checkpoint'] = checkpoint
        status, _, raw = self.post('/api/state', body)
        return status, (json.loads(raw) if raw[:1] == b'{' else raw)

    def file_hash(self):
        import hashlib
        return hashlib.sha256((self.data / 'state.json').read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.live = Live()

    def tearDown(self):
        self.live.close()


# --------------------------------------------------------------------- limits

class TestLimitsParity(Base):
    def test_shared_js_limits_match_server(self):
        js = (ROOT / 'shared.js').read_text(encoding='utf-8')
        block = re.search(r'LIMITS = Object\.freeze\(\{(.*?)\}\);', js, re.S).group(1)
        js_limits = {k: float(v) for k, v in re.findall(r'(\w+):\s*([0-9.]+)', block)}
        self.assertEqual(set(js_limits), set(srv.LIMITS))
        for k, v in srv.LIMITS.items():
            self.assertEqual(js_limits[k], float(v), k)


# ---------------------------------------------------------------- R-14 / R-16

class TestR14HostOriginCsrf(Base):
    def test_bad_hosts_rejected(self):
        p = self.live.port
        for host in ['localhost:%d' % p, 'evil.example:%d' % p, '127.0.0.1', '127.0.0.1:%d.evil' % p,
                     '[::1]:%d' % p, '0.0.0.0:%d' % p, '2130706433:%d' % p, 'LOCALHOST:%d' % p, 'rebind.test:%d' % p]:
            status, _, body = self.live.get('/', host=host)
            self.assertEqual(status, 403, host)
            self.assertIn(b'127.0.0.1:%d' % p, body)
        self.assertEqual(self.live.get('/', host=None)[0], 403)

    def test_duplicate_host_and_absolute_form_rejected(self):
        status, _, _ = self.live.request('GET', '/api/state', {'Host': [self.live.host, 'evil.example']}, host=None)
        self.assertEqual(status, 400)
        status, _, _ = self.live.request('GET', '/api/state', {'Host': ['evil.example', self.live.host]}, host=None)
        self.assertEqual(status, 400)
        self.assertEqual(self.live.get('http://evil.example/api/state')[0], 400)

    def test_post_origin_and_content_type(self):
        before = self.live.file_hash()
        st = self.live.state()
        body = {'baseRev': st['rev'], 'state': st}
        for origin in [None, 'https://evil.example', 'null', 'http://localhost:%d' % self.live.port, 'http://127.0.0.1:9000']:
            self.assertEqual(self.live.post('/api/state', body, origin=origin)[0], 403, origin)
        for ctype in ['text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x', None]:
            self.assertEqual(self.live.post('/api/state', body, ctype=ctype)[0], 415, ctype)
        self.assertEqual(self.live.post('/api/state', body, host='evil.example:%d' % self.live.port)[0], 403)
        self.assertEqual(self.live.post('/api/state', body, headers={'Origin': [self.live.origin, 'https://evil.example']}, origin=None)[0], 400)
        self.assertEqual(self.live.file_hash(), before)

    def test_preflight_and_other_methods_refused(self):
        for m in ['OPTIONS', 'PUT', 'DELETE', 'PATCH', 'TRACE', 'HEAD']:
            status, headers, body = self.live.request(m, '/api/state', {'Origin': self.live.origin})
            self.assertEqual(status, 501, m)
            self.assertNotIn(b'<', body)                       # no HTML error page
            self.assertIn('default-src \'none\'', headers.get('content-security-policy', [''])[0])


class TestR16Allowlist(Base):
    def test_only_listed_files(self):
        for path in ['/server.py', '/data/state.json', '/../server.py', '/%2e%2e/server.py', '/..%2fserver.py',
                     '/index.html/', '/./app.js', '/APP.JS', '/app.js%00', '/README.txt', '/start-windows.bat',
                     '/starter-state.json', '/api/state/', '/api/statex', '/%2fetc%2fpasswd', '/tests/test_server.py']:
            self.assertEqual(self.live.get(path)[0], 404, path)
        for path in ['//index.html', '//evil.example/']:
            self.assertEqual(self.live.get(path)[0], 400, path)
        for path in ['/', '/index.html', '/overlay.html', '/styles.css', '/shared.js', '/app.js', '/overlay.js', '/api/state', '/api/status', '/app.js?x=1']:
            self.assertEqual(self.live.get(path)[0], 200, path)
        self.assertEqual(self.live.get('/favicon.ico')[0], 204)


# ----------------------------------------------------------------------- R-07

class TestR07Headers(Base):
    def test_security_headers_on_every_response(self):
        for status_path in ['/', '/app.js', '/api/state', '/nope']:
            status, h, _ = self.live.get(status_path)
            csp = h['content-security-policy'][0]
            for token in ["default-src 'none'", "script-src 'self'", "style-src 'self'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'self'"]:
                self.assertIn(token, csp)
            self.assertNotIn('unsafe-inline', csp)
            self.assertEqual(h['cross-origin-resource-policy'], ['same-origin'])
            self.assertEqual(h['cross-origin-opener-policy'], ['same-origin'])
            self.assertEqual(h['x-content-type-options'], ['nosniff'])
            self.assertNotIn('access-control-allow-origin', h)
            self.assertEqual(h['server'], ['CAPCAST'])

    def test_fetch_metadata_gate(self):
        cross = lambda site, mode='no-cors', dest='script', path='/app.js': self.live.get(  # noqa: E731
            path, {'Sec-Fetch-Site': site, 'Sec-Fetch-Mode': mode, 'Sec-Fetch-Dest': dest})[0]
        self.assertEqual(cross('cross-site'), 403)
        self.assertEqual(cross('same-site'), 403)
        self.assertEqual(cross('cross-site', 'no-cors', 'style', '/styles.css'), 403)
        self.assertEqual(cross('cross-site', 'cors', 'empty', '/api/state'), 403)
        self.assertEqual(cross('cross-site', 'navigate', 'iframe', '/'), 403)
        self.assertEqual(cross('cross-site', 'navigate', 'document', '/api/state'), 403)
        self.assertEqual(cross('cross-site', 'navigate', 'document', '/'), 200)      # a link from another site
        self.assertEqual(cross('same-origin'), 200)
        self.assertEqual(cross('none', 'navigate', 'document', '/overlay.html'), 200)
        self.assertEqual(self.live.get('/app.js')[0], 200)                         # old clients send no header
        st = self.live.state()
        status, _, _ = self.live.post('/api/heartbeat', {'id': 'abcdef123456', 'kind': 'obs', 'rev': 0, 'ok': True},
                                      headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)
        self.assertEqual(self.live.state()['rev'], st['rev'])


# ----------------------------------------------------------------------- R-17

class TestR17Malformed(Base):
    def post_raw(self, raw, extra=None):
        return self.live.post('/api/state', raw, headers=extra)

    def test_malformed_bodies(self):
        before = self.live.file_hash()
        st = self.live.state()
        self.assertEqual(self.post_raw(b'', {'Content-Length': '0'})[0], 400)
        self.assertEqual(self.post_raw(b'{}', {'Content-Length': '-5'})[0], 400)
        self.assertEqual(self.post_raw(b'{}', {'Content-Length': 'abc'})[0], 400)
        self.assertEqual(self.post_raw(b'{nope')[0], 400)
        self.assertEqual(self.post_raw(b'{"a":"\xff\xfe"}')[0], 400)
        self.assertEqual(self.post_raw(b'[1,2,3]')[0], 400)
        self.assertEqual(self.post_raw(b'[' * 200000 + b']' * 200000)[0], 400)
        body = json.dumps({'baseRev': st['rev'], 'state': st}).replace('"scale": 1', '"scale": ' + '9' * 5000).encode()
        self.assertEqual(self.post_raw(body)[0], 400)
        body = json.dumps({'baseRev': st['rev'], 'state': st}).replace('"scale": 1', '"scale": NaN').encode()
        self.assertEqual(self.post_raw(body)[0], 400)
        body = json.dumps({'baseRev': st['rev'], 'state': st}).replace('MLTP \\u00b7 MINORS', '\\ud83d').encode()
        self.assertEqual(self.post_raw(body)[0], 400)
        status, _, _ = self.post_raw(b'{"a":1}', {'Transfer-Encoding': 'chunked'})
        self.assertEqual(status, 400)
        self.assertEqual(self.live.file_hash(), before)
        self.assertEqual(self.live.get('/api/state')[0], 200)

    def test_oversized_rejected_before_reading_body(self):
        s = socket.create_connection(('127.0.0.1', self.live.port), timeout=5)
        s.sendall(('POST /api/state HTTP/1.1\r\nHost: %s\r\nOrigin: %s\r\nContent-Type: application/json\r\n'
                   'Content-Length: %d\r\n\r\n' % (self.live.host, self.live.origin, srv.MAX_BODY + 1)).encode())
        resp = s.recv(4096)
        s.close()
        self.assertTrue(resp.startswith(b'HTTP/1.0 413'), resp[:40])


# ----------------------------------------------------------------------- R-18

class TestR18Concurrency(Base):
    def test_concurrent_writers_and_readers(self):
        st = self.live.state()
        base = st['rev']
        results, bad_reads = [], []

        def writer(n):
            d = copy.deepcopy(st)
            d['games'][0]['scoreA'] = n
            results.append(self.live.save(d, base=base)[0])

        def reader():
            for _ in range(20):
                status, _, body = self.live.get('/api/state')
                try:
                    json.loads(body)
                except ValueError:
                    bad_reads.append(body[:50])

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(40)] + [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(bad_reads, [])
        self.assertEqual(results.count(200), 1)         # exactly one writer based on rev N wins
        self.assertEqual(results.count(409), 39)        # every other stale write is refused, not lost silently
        self.assertEqual(self.live.state()['rev'], base + 1)
        self.assertEqual(list(self.live.data.glob('*.tmp')), [])


# --------------------------------------------------------------------- R-02

class TestR02Revisions(Base):
    def test_stale_write_refused_with_409(self):
        st = self.live.state()
        a = copy.deepcopy(st)
        a['games'][0].update(scoreA=3, scoreB=0)
        self.assertEqual(self.live.save(a, base=st['rev'])[0], 200)
        b = copy.deepcopy(st)
        b['games'][0]['map'] = 'Pilot'
        status, body = self.live.save(b, base=st['rev'])
        self.assertEqual(status, 409)
        self.assertEqual(body['rev'], st['rev'] + 1)
        self.assertEqual(self.live.state()['games'][0]['scoreA'], 3)

    def test_rev_and_saved_at_are_server_managed(self):
        st = self.live.state()
        st['rev'] = 999
        st['savedAt'] = 'forged'
        status, body = self.live.save(st, base=0)
        self.assertEqual(status, 200)
        now = self.live.state()
        self.assertEqual(now['rev'], 1)
        self.assertNotEqual(now['savedAt'], 'forged')


    def test_data_version_checked_not_just_rev(self):
        # RV-01: after a restart another data folder can be at the same rev; savedAt tells them apart.
        st = self.live.state()
        status, _, raw = self.live.post('/api/state', {'baseRev': st['rev'], 'baseSavedAt': '2001-01-01T00:00:00Z',
                                                         'baseInstance': 'not-this-server', 'state': st})
        self.assertEqual((status, json.loads(raw)['reason']), (409, 'restarted'))
        status, _, raw = self.live.post('/api/state', {'baseRev': st['rev'], 'baseSavedAt': '2001-01-01T00:00:00Z', 'state': st})
        self.assertEqual((status, json.loads(raw)['reason']), (409, 'changed'))
        self.assertEqual(self.live.state()['rev'], st['rev'])
        status, _, raw = self.live.post('/api/state', {'baseRev': st['rev'], 'baseSavedAt': 5, 'state': st})
        self.assertEqual(status, 400)
        # The matching version saves, even from a dashboard that last talked to an earlier server instance.
        status, _, raw = self.live.post('/api/state', {'baseRev': st['rev'], 'baseSavedAt': st['savedAt'],
                                                         'baseInstance': 'an-earlier-start', 'state': st})
        self.assertEqual(status, 200)


# ------------------------------------------------------------- R-01 / R-09

class TestR09Validation(Base):
    def check(self, fn, expect_status=400, path_contains=None):
        st = self.live.state()
        d = copy.deepcopy(st)
        fn(d)
        status, body = self.live.save(d, base=st['rev'])
        self.assertEqual(status, expect_status, body)
        if path_contains:
            self.assertIn(path_contains, body.get('path', ''))
        return body

    def test_rejections_name_the_field(self):
        self.check(lambda d: d.update(mode=5.0), path_contains='state.mode')
        self.check(lambda d: d.update(junk='x' * 1000), path_contains='state.junk')
        self.check(lambda d: d['teams'][0].update(evil='<img>'), path_contains='state.teams[0].evil')
        self.check(lambda d: d['presets'].append(dict(d['presets'][0])), path_contains='state.presets[2].id')
        self.check(lambda d: d['presets'][0].update(id='"><b>x</b>'), path_contains='state.presets[0].id')
        self.check(lambda d: d['teams'][1].update(code='ABCDEFGHIJKLM'), path_contains='state.teams[1].code')
        self.check(lambda d: d['teams'][1].update(name='a\tb'), path_contains='state.teams[1].name')
        self.check(lambda d: d['teams'][1].update(name='\ud83d'), path_contains='state.teams[1].name')
        self.check(lambda d: d['teams'][1].update(primary='#fff;x'), path_contains='state.teams[1].primary')
        self.check(lambda d: d['games'][0].update(scoreA='5'), path_contains='state.games[0].scoreA')
        self.check(lambda d: d['games'][0].update(scoreA=True), path_contains='state.games[0].scoreA')
        self.check(lambda d: d['games'][0].update(scoreA=1000), path_contains='state.games[0].scoreA')
        self.check(lambda d: d['meta'].update(scale=0.5), path_contains='state.meta.scale')
        self.check(lambda d: d['teams'].extend({**d['teams'][0], 'id': 'x%d' % i} for i in range(671)), path_contains='state.teams')
        self.check(lambda d: d.update(schemaVersion=1), path_contains='state.schemaVersion')

    def test_same_team_both_rows_allowed(self):
        # v1.1 allowed it and existing data may contain it; the dashboard warns instead.
        self.check(lambda d: d.update(teamB=d['teamA']), expect_status=200)

    def test_r05b_hidden_ot_winner_refused(self):
        self.check(lambda d: d['games'][0].update(scoreA=2, scoreB=2, ot=False, otWinner='A'), path_contains='state.games[0].otWinner')
        self.check(lambda d: d['games'][0].update(scoreA=2, scoreB=2, ot=True, otWinner='A'), expect_status=200)

    def test_r03_logo_checks(self):
        cases = {
            'bomb': (fx.data_url('png', fx.png_bomb(16000, 16000)), 'exceed'),
            'mismatch': (fx.data_url('jpeg', fx.png_bytes(8, 8)), 'contains PNG'),
            'badb64': ('data:image/png;base64,=====', 'logo'),
            'nopad': ('data:image/png;base64,QQ', 'base64'),
            'svg': ('data:image/svg+xml;base64,PHN2Zz4=', 'PNG, JPEG or WebP'),
            'jpeg-huge': (fx.data_url('jpeg', fx.jpeg_header(9000, 9000)), 'exceed'),
            'webp-huge': (fx.data_url('webp', fx.webp_vp8x(5000, 10)), 'exceed'),
            'too-many-bytes': (fx.data_url('png', fx.png_bytes(8, 8) + b'\x00' * 600001), 'too large'),
        }
        for name, (url, msg) in cases.items():
            body = self.check(lambda d, url=url: d['teams'][0].update(logo=url), path_contains='state.teams[0].logo')
            self.assertIn(msg, body['error'], name)
        for url in [fx.data_url('png', fx.png_bytes(256, 256)), fx.data_url('jpeg', fx.jpeg_header(300, 200)),
                    fx.data_url('webp', fx.webp_vp8l(64, 64)), fx.data_url('webp', fx.webp_vp8(64, 64))]:
            self.check(lambda d, url=url: d['teams'][0].update(logo=url), expect_status=200)

    def test_total_size_budget(self):
        logo = fx.data_url('png', fx.png_bytes(8, 8) + b'\x00' * 590000)

        def fill(d):
            for i in range(5):
                d['teams'][i]['logo'] = logo
        body = self.check(fill, expect_status=400)
        self.assertIn('too large', body['error'])

    def test_r09_missing_state_file_rewritten(self):
        (self.live.data / 'state.json').unlink()
        status, _, body = self.live.get('/api/state')
        self.assertEqual(status, 200)
        json.loads(body)
        self.assertTrue((self.live.data / 'state.json').exists())
        self.assertTrue(any('disappeared' in n['text'] for n in self.live.store.notices))


# ----------------------------------------------------------------------- R-08

class TestR08Writes(Base):
    def test_failed_replace_leaves_no_temp_files(self):
        real = srv.os.replace

        def deny(a, b):
            raise PermissionError(13, 'locked by another process')
        srv.os.replace = deny
        try:
            st = self.live.state()
            for _ in range(5):
                self.assertEqual(self.live.save(st, base=st['rev'])[0], 500)
        finally:
            srv.os.replace = real
        self.assertEqual(list(self.live.data.glob('*.tmp')) + list(self.live.data.glob('.*.tmp')), [])

    def test_replace_retried_on_transient_lock(self):
        real, calls = srv.os.replace, {'n': 0}

        def flaky(a, b):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise PermissionError(13, 'sharing violation')
            return real(a, b)
        srv.os.replace = flaky
        try:
            st = self.live.state()
            self.assertEqual(self.live.save(st, base=st['rev'])[0], 200)
        finally:
            srv.os.replace = real
        self.assertGreaterEqual(calls['n'], 3)   # 2 refused + 1 success (+ the automatic backup write)

    def test_disk_full_leaves_no_temp_and_keeps_state(self):
        real = srv.os.fsync

        def full(fd):
            raise OSError(errno.ENOSPC, 'No space left on device')
        before = self.live.file_hash()
        srv.os.fsync = full
        try:
            st = self.live.state()
            self.assertEqual(self.live.save(st, base=st['rev'])[0], 500)
        finally:
            srv.os.fsync = real
        self.assertEqual(self.live.file_hash(), before)
        self.assertEqual(list(self.live.data.glob('.*.tmp')), [])

    def test_fsync_called_and_checkpoint_backup(self):
        real, calls = srv.os.fsync, {'n': 0}

        def counting(fd):
            calls['n'] += 1
            return real(fd)
        srv.os.fsync = counting
        try:
            st = self.live.state()
            self.assertEqual(self.live.save(st, base=st['rev'], checkpoint='clear-scores')[0], 200)
        finally:
            srv.os.fsync = real
        self.assertGreaterEqual(calls['n'], 1)
        backups = list((self.live.data / 'backups').glob('before-clear-scores-*.json'))
        self.assertEqual(len(backups), 1)
        json.loads(backups[0].read_bytes())

    def test_backup_rotation_only_touches_own_files(self):
        b = self.live.data / 'backups'
        b.mkdir(exist_ok=True)
        (b / 'my-own-notes.json').write_text('{}')
        (b / 'pre-1.1.1-migration-20260101-000000-abcd.json').write_text('{}')
        st = self.live.state()
        for i in range(srv.KEEP_BACKUPS + 8):
            st = self.live.state()
            self.assertEqual(self.live.save(st, base=st['rev'], checkpoint='clear-scores')[0], 200)
        names = [p.name for p in b.iterdir()]
        self.assertIn('my-own-notes.json', names)
        self.assertIn('pre-1.1.1-migration-20260101-000000-abcd.json', names)
        self.assertLessEqual(len([n for n in names if srv.ROTATING_RE.fullmatch(n)]), srv.KEEP_BACKUPS)

    def test_stale_temp_files_removed_at_startup(self):
        live2 = Live(prepare=lambda d: [(d / n).write_bytes(b'x') for n in ('tmpab12cd.tmp', '.state-1-ff.tmp', 'notes.tmp')])
        try:
            names = sorted(p.name for p in live2.data.iterdir())
            self.assertNotIn('tmpab12cd.tmp', names)
            self.assertNotIn('.state-1-ff.tmp', names)
            self.assertIn('notes.tmp', names)        # unrelated files are never touched
        finally:
            live2.close()

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions')
    def test_files_private(self):
        mode = (self.live.data / 'state.json').stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


# ----------------------------------------------------------------------- R-06

CRAFTED = '"><b id=inj style=position:fixed>FAKE</b><img src=x onerror=alert(1)><meta http-equiv=refresh content="0;url=http://evil.test/">'


class TestR06DiskState(unittest.TestCase):
    def test_crafted_state_quarantined_at_startup(self):
        bad = fx.v11_state()
        bad['games'][0]['scoreA'] = CRAFTED
        live = Live(prepare=lambda d: (d / 'state.json').write_text(json.dumps(bad), encoding='utf-8'))
        try:
            st = live.state()
            self.assertIsNone(st['games'][0]['scoreA'])
            self.assertNotIn('onerror', json.dumps(st))
            q = list((live.data / 'quarantine').glob('state-invalid-*.json'))
            self.assertEqual(len(q), 1)
            self.assertEqual(json.loads(q[0].read_bytes())['games'][0]['scoreA'], CRAFTED)  # original kept
            self.assertTrue(any('could not be loaded' in n['text'] for n in live.store.notices))
        finally:
            live.close()

    def test_external_edit_while_running(self):
        live = Live()
        try:
            rev = live.state()['rev']
            bad = fx.v11_state()
            bad['games'][0]['scoreA'] = CRAFTED
            time.sleep(0.02)
            (live.data / 'state.json').write_text(json.dumps(bad), encoding='utf-8')
            st = live.state()
            self.assertEqual(st['rev'], rev)
            self.assertIsNone(st['games'][0]['scoreA'])
            good = fx.v11_state()
            good['games'][1].update(scoreA=4, scoreB=1)
            time.sleep(0.02)
            (live.data / 'state.json').write_text(json.dumps(good), encoding='utf-8')
            st = live.state()
            self.assertEqual(st['rev'], rev + 1)
            self.assertEqual((st['games'][1]['scoreA'], st['games'][1]['scoreB']), (4, 1))
            self.assertTrue(list((live.data / 'backups').glob('before-external-change-*.json')))
        finally:
            live.close()


# ------------------------------------------------------------------ migration

class TestMigration(unittest.TestCase):
    def start_with(self, state_obj):
        raw = json.dumps(state_obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        live = Live(prepare=lambda d: (d / 'state.json').write_bytes(raw))
        return live, raw

    def test_v11_shipped_state_is_lossless(self):
        original = fx.v11_state()
        raw = (HERE / 'data' / 'v1.1-state.json').read_bytes()
        live = Live(prepare=lambda d: (d / 'state.json').write_bytes(raw))
        try:
            st = live.state()
            for key in ('mode', 'teamA', 'teamB', 'meta', 'games', 'presets', 'teams'):
                self.assertEqual(st[key], original[key], key)
            self.assertEqual(st['schemaVersion'], 2)
            backups = list((live.data / 'backups').glob('pre-1.1.1-migration-*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), raw)       # byte-identical copy of the original
        finally:
            live.close()

    def test_v11_match_with_scores_ot_and_logo_preserved(self):
        d = fx.v11_state()
        d['mode'] = 7
        d['meta'].update(label='GRAND FINAL', scale=1.25)
        d['games'][0].update(scoreA=3, scoreB=1)
        d['games'][1].update(scoreA=2, scoreB=2, ot=True, otWinner='B')
        d['games'][6].update(scoreA=0, scoreB=0)
        d['teams'][0]['logo'] = fx.data_url('png', fx.png_bytes(200, 120))
        d['teams'][1]['name'] = 'Mötley Crüe 🎸 Caps'
        live, _ = self.start_with(d)
        try:
            st = live.state()
            for key in ('mode', 'teamA', 'teamB', 'meta', 'games', 'presets', 'teams'):
                self.assertEqual(st[key], d[key], key)
        finally:
            live.close()

    def test_normalizations_are_reported_not_silent(self):
        d = fx.v11_state()
        d['mode'] = 5.0
        d['junk'] = 'x' * 5000
        d['teams'][0]['note'] = 'extra'
        d['games'][0].update(scoreA=2, scoreB=2, ot=False, otWinner='A')
        d['games'][1].update(scoreA=3, scoreB=1, ot=False, otWinner='B')
        d['presets'].append(dict(d['presets'][0]))
        d['presets'][0]['id'] = 'has spaces'
        d['teams'][2]['name'] = 'Tab\tInside'
        d['teams'][3]['logo'] = fx.data_url('png', fx.png_bomb(16000, 16000))
        live, raw = self.start_with(d)
        try:
            st = live.state()
            texts = ' | '.join(n['text'] for n in live.store.notices)
            self.assertEqual(st['mode'], 5)
            self.assertNotIn('junk', st)
            self.assertIn('Ignored 2 unknown field(s)', texts)
            self.assertIn('backup.junk', texts)
            self.assertIn('backup.teams[0].note', texts)
            self.assertEqual((st['games'][0]['ot'], st['games'][0]['otWinner']), (True, 'A'))
            self.assertIn('Game 1: overtime winner A kept', texts)
            self.assertIsNone(st['games'][1]['otWinner'])
            self.assertIn('Game 2: removed overtime winner B', texts)
            ids = [p['id'] for p in st['presets']]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(st['teams'][2]['name'], 'Tab Inside')
            self.assertIsNone(st['teams'][3]['logo'])
            self.assertIn('was set aside', texts)
            aside = list((live.data / 'quarantine').glob('logos-set-aside-*.json'))
            self.assertEqual(len(aside), 1)
            self.assertEqual(json.loads(aside[0].read_bytes())['logos'][0]['logo'], d['teams'][3]['logo'])
            pre = list((live.data / 'backups').glob('pre-1.1.1-migration-*.json'))
            self.assertEqual(pre[0].read_bytes(), raw)
        finally:
            live.close()

    def test_data_v11_would_reject_is_refused_whole(self):
        mig = srv.Migration()
        d = fx.v11_state()
        d['teams'][0]['primary'] = 'red'
        with self.assertRaises(srv.BadRequest):
            srv.migrate(d, mig)
        with self.assertRaises(srv.BadRequest):
            srv.migrate({'schemaVersion': 3}, srv.Migration())
        with self.assertRaises(srv.BadRequest):
            srv.migrate([], srv.Migration())


class TestRestore(Base):
    def test_dry_run_then_restore_v11_backup(self):
        backup = fx.v11_state()
        backup['games'][0].update(scoreA=5, scoreB=0)
        backup['junk'] = {'a': 1}
        base = self.live.state()['rev']
        status, _, raw = self.live.post('/api/restore', {'dryRun': True, 'baseRev': base, 'backup': backup})
        self.assertEqual(status, 200)
        report = json.loads(raw)['report']
        self.assertEqual(report['teams'], 30)
        self.assertEqual(report['ignoredFields'], ['backup.junk'])
        self.assertEqual(self.live.state()['rev'], base)                 # dry run changes nothing
        status, _, raw = self.live.post('/api/restore', {'dryRun': False, 'baseRev': base + 7, 'backup': backup})
        self.assertEqual(status, 409)
        status, _, raw = self.live.post('/api/restore', {'dryRun': False, 'baseRev': base, 'backup': backup})
        self.assertEqual(status, 200)
        st = self.live.state()
        self.assertEqual(st['rev'], base + 1)
        self.assertEqual(st['games'][0]['scoreA'], 5)
        self.assertTrue(list((self.live.data / 'backups').glob('before-restore-*.json')))

    def test_restore_refuses_invalid_backup(self):
        bad = fx.v11_state()
        bad['games'][0]['scoreA'] = CRAFTED
        status, _, raw = self.live.post('/api/restore', {'dryRun': True, 'baseRev': 0, 'backup': bad})
        self.assertEqual(status, 400)
        self.assertIn('games[0].scoreA', json.loads(raw)['path'])

    def test_v111_backup_round_trip(self):
        st = self.live.state()
        st['games'][2].update(scoreA=1, scoreB=1, ot=True, otWinner='A')
        self.assertEqual(self.live.save(st, base=st['rev'])[0], 200)
        backup = self.live.state()
        status, _, raw = self.live.post('/api/restore', {'dryRun': False, 'baseRev': backup['rev'], 'backup': backup})
        self.assertEqual(status, 200)
        after = self.live.state()
        for key in ('mode', 'teamA', 'teamB', 'meta', 'games', 'presets', 'teams'):
            self.assertEqual(after[key], backup[key])


class TestHeartbeatStatus(Base):
    def test_heartbeat_and_status(self):
        ok = {'id': 'abcdef123456', 'kind': 'obs', 'rev': 0, 'ok': True}
        self.assertEqual(self.live.post('/api/heartbeat', ok)[0], 200)
        status, _, raw = self.live.get('/api/status')
        s = json.loads(raw)
        self.assertEqual(s['version'], '1.1.1')
        self.assertEqual([c['kind'] for c in s['clients']], ['obs'])
        for bad in [{**ok, 'id': 'x'}, {**ok, 'kind': 'admin'}, {**ok, 'rev': 'n'}, {**ok, 'ok': 1}, {**ok, 'error': 'e' * 201}]:
            self.assertEqual(self.live.post('/api/heartbeat', bad)[0], 400, bad)
        self.assertEqual(self.live.post('/api/heartbeat', ok, origin='https://evil.example')[0], 403)
        for i in range(srv.MAX_CLIENTS + 10):
            self.live.post('/api/heartbeat', {**ok, 'id': 'client%08d' % i})
        self.assertLessEqual(len(self.live.store.clients), srv.MAX_CLIENTS)


# ----------------------------------------------------------------------- R-11

class TestR11Robustness(Base):
    def test_stalled_connections_time_out_and_are_capped(self):
        self.assertEqual(srv.Handler.timeout, 15)
        old = srv.Handler.timeout
        srv.Handler.timeout = 1
        try:
            socks = []
            for _ in range(srv.MAX_HANDLERS + 20):
                s = socket.create_connection(('127.0.0.1', self.live.port))
                s.sendall(b'GET / HTTP/1.1\r\nHost: ' + self.live.host.encode() + b'\r\n')
                socks.append(s)
            time.sleep(0.3)
            self.assertLessEqual(threading.active_count(), srv.MAX_HANDLERS + 10)
            time.sleep(1.5)
            self.assertEqual(self.live.get('/api/state')[0], 200)
            closed = 0
            for s in socks:
                s.settimeout(0.2)
                try:
                    if s.recv(10) == b'':
                        closed += 1
                except (socket.timeout, ConnectionResetError):
                    closed += 1
                s.close()
            self.assertEqual(closed, len(socks))
        finally:
            srv.Handler.timeout = old

    def test_python_version_messages(self):
        self.assertEqual(srv.python_ok((3, 8, 0))[0], False)
        self.assertIn('3.9', srv.python_ok((3, 8, 0))[1])
        ok, warn = srv.python_ok((3, 10, 20))
        self.assertTrue(ok)
        self.assertIn('no longer receives security updates', warn)
        self.assertEqual(srv.python_ok((3, 11, 0)), (True, None))
        self.assertEqual(srv.python_ok((3, 14, 0)), (True, None))

    def test_port_in_use_gives_clear_message(self):
        tmp = tempfile.mkdtemp()
        try:
            p = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'server.py'), '--port', str(self.live.port),
                                '--no-browser', '--data-dir', tmp], capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn('already in use', p.stdout)
            self.assertNotIn('Traceback', p.stdout + p.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_windows_bind_settings(self):
        self.assertEqual(srv.LocalServer.allow_reuse_address, os.name != 'nt')


# ------------------------------------------------- independent review (RV-xx)

def padded_png(total_bytes):
    """A valid 8x8 PNG padded with a tEXt chunk to about total_bytes."""
    import struct, zlib
    base = fx.png_bytes(8, 8)
    pad = max(0, total_bytes - len(base) - 12)
    chunk = b'tEXt' + b'c\x00' + b'x' * max(0, pad - 2)
    blob = struct.pack('>I', len(chunk) - 4) + chunk + struct.pack('>I', zlib.crc32(chunk) & 0xFFFFFFFF)
    return base[:-12] + blob + base[-12:]


class TestReviewFindings(unittest.TestCase):
    def test_RV02_wrong_types_never_crash(self):
        import random
        rnd = random.Random(1731)
        base = fx.v2_state()
        values = [None, True, 0, -1, 1.5, '', 'x', [], [1], {}, {'a': 1}, 'nban', [None], 10 ** 12]
        paths = [('teamA',), ('teamB',), ('mode',), ('meta',), ('meta', 'scale'), ('meta', 'label'), ('games',),
                 ('games', 0), ('games', 0, 'scoreA'), ('games', 0, 'ot'), ('games', 0, 'otWinner'), ('games', 0, 'map'),
                 ('presets',), ('presets', 0), ('presets', 0, 'id'), ('presets', 0, 'maps'), ('teams',), ('teams', 0),
                 ('teams', 0, 'id'), ('teams', 0, 'logo'), ('teams', 0, 'primary'), ('teams', 0, 'name'), ('schemaVersion',)]
        for _ in range(3000):
            d = copy.deepcopy(base)
            for _ in range(rnd.randint(1, 3)):
                path = rnd.choice(paths)
                obj = d
                try:
                    for k in path[:-1]:
                        obj = obj[k]
                    obj[path[-1]] = rnd.choice(values)
                except (KeyError, IndexError, TypeError):
                    pass
            for fn in (srv.validate_strict, lambda x: srv.migrate(x, srv.Migration())):
                try:
                    fn(d)
                except srv.BadRequest:
                    pass

    def test_RV02_bad_team_reference_quarantined_not_fatal(self):
        bad = fx.v11_state()
        bad['teamA'] = {'evil': 1}
        live = Live(prepare=lambda d: (d / 'state.json').write_text(json.dumps(bad), encoding='utf-8'))
        try:
            self.assertEqual(live.state()['teamA'], 'nban')
            self.assertEqual(len(list((live.data / 'quarantine').glob('state-invalid-*.json'))), 1)
            bad['teamB'] = [1, 2]
            time.sleep(0.02)
            (live.data / 'state.json').write_text(json.dumps(bad), encoding='utf-8')
            self.assertEqual(live.get('/api/state')[0], 200)
            self.assertEqual(live.get('/api/status')[0], 200)
            st = live.state()
            self.assertEqual(live.save(st, base=st['rev'])[0], 200)
            status, _, raw = live.post('/api/restore', {'dryRun': True, 'baseRev': 0, 'backup': bad})
            self.assertEqual(status, 400)
        finally:
            live.close()

    def test_RV04_one_size_limit_everywhere(self):
        base = fx.v2_state()
        budget = srv.LIMITS['stateBytes']

        def sized(target):
            d = copy.deepcopy(base)
            for i in range(4):
                d['teams'][i]['logo'] = fx.data_url('png', padded_png(560000))
            filler = srv.measure(d)
            d['teams'][5]['logo'] = fx.data_url('png', padded_png(8))
            while srv.measure(d) < target:
                gap = target - srv.measure(d)
                cur = len(base64_decode(d['teams'][5]['logo']))
                d['teams'][5]['logo'] = fx.data_url('png', padded_png(cur + max(1, gap * 3 // 4)))
            return d

        def base64_decode(url):
            import base64
            return base64.b64decode(url.split(',', 1)[1])
        under = sized(budget - 400)
        self.assertLessEqual(srv.measure(under), budget)
        srv.validate_strict(under)
        srv.migrate(copy.deepcopy(under), srv.Migration())
        over = copy.deepcopy(under)
        while srv.measure(over) <= budget:
            over['teams'][6]['era'] = (over['teams'][6]['era'] + 'x')[:80] if len(over['teams'][6]['era']) < 80 else over['teams'][6]['era']
            over['teams'][7]['logo'] = fx.data_url('png', padded_png(2000))
            over['teams'][8]['logo'] = fx.data_url('png', padded_png(2000 + (srv.measure(over) - budget) * -1 + 50))
        with self.assertRaises(srv.BadRequest):
            srv.validate_strict(over)
        with self.assertRaises(srv.BadRequest):
            srv.migrate(copy.deepcopy(over), srv.Migration())
        # rev and savedAt never change the measured size
        a, b = copy.deepcopy(under), copy.deepcopy(under)
        a['rev'], a['savedAt'] = 0, None
        b['rev'], b['savedAt'] = 123456789, '2026-11-11T21:11:44Z'
        self.assertEqual(srv.measure(a), srv.measure(b))

    def test_RV07_notice_ids_unique(self):
        live = Live()
        try:
            for i in range(120):
                live.store.notice('n%d' % i)
            ids = [n['id'] for n in live.store.notices]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(len(ids), 50)
        finally:
            live.close()

    def test_RV08_external_drop_in_keeps_original_bytes(self):
        live = Live()
        try:
            d = fx.v11_state()
            d['sponsorNotes'] = 'KEEP ME ' * 10
            d['games'][0]['map'] = 'Line\nBreak'
            raw = json.dumps(d).encode('utf-8')
            time.sleep(0.02)
            (live.data / 'state.json').write_bytes(raw)
            live.state()
            kept = list((live.data / 'backups').glob('external-original-*.json'))
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].read_bytes(), raw)
        finally:
            live.close()

    @unittest.skipIf(os.name == 'nt', 'symlinks')
    def test_RV09_RV13_symlinks(self):
        outside = Path(tempfile.mkdtemp())
        try:
            target = outside / 'my-state.json'
            target.write_text((ROOT / 'starter-state.json').read_text(encoding='utf-8'), encoding='utf-8')
            precious = outside / 'precious.json'
            precious.write_text('{}')
            live = Live(prepare=lambda d: (d / 'state.json').symlink_to(target))
            try:
                b = live.data / 'backups'
                b.mkdir(exist_ok=True)
                (b / 'auto-20200101-000000-abcd.json').symlink_to(precious)
                for _ in range(srv.KEEP_BACKUPS + 3):
                    st = live.state()
                    self.assertEqual(live.save(st, base=st['rev'], checkpoint='clear-scores')[0], 200)
                self.assertTrue((live.data / 'state.json').is_symlink())
                self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['rev'], live.state()['rev'])
                self.assertTrue(precious.exists())
            finally:
                live.close()
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_RV10_backup_failures_never_fail_a_save(self):
        live = Live()
        try:
            shutil.rmtree(live.data / 'backups', ignore_errors=True)
            (live.data / 'backups').write_text('not a folder')
            live.store.last_auto_backup = 0
            st = live.state()
            self.assertEqual(live.save(st, base=st['rev'])[0], 200)
            st = live.state()
            self.assertEqual(live.save(st, base=st['rev'], checkpoint='clear-scores')[0], 200)
            status, _, _ = live.post('/api/restore', {'dryRun': False, 'baseRev': live.state()['rev'], 'backup': fx.v11_state()})
            self.assertEqual(status, 200)
            self.assertTrue(any('Could not write a safety backup' in n['text'] for n in live.store.notices))
        finally:
            live.close()

    def test_RV11_unknown_keys_escaped_and_bounded(self):
        d = fx.v11_state()
        d['\x1b]0;pwned\x07\x1b[2J'] = 1
        for i in range(10000):
            d['k%05d' % i] = 'v'
        mig = srv.Migration()
        srv.migrate(d, mig)
        text = ' '.join(mig.notes)
        self.assertNotIn('\x1b', text)
        self.assertLess(len(text), 2000)
        live = Live(prepare=lambda p: (p / 'state.json').write_text(json.dumps(d), encoding='utf-8'))
        try:
            status, _, raw = live.get('/api/status')
            self.assertEqual(status, 200)
            self.assertLess(len(raw), 50000)
            self.assertNotIn(b'\x1b', raw)
        finally:
            live.close()

    def test_RV14_temp_files_in_subfolders_cleaned(self):
        def prep(d):
            for sub in ('backups', 'quarantine'):
                (d / sub).mkdir()
                (d / sub / '.state-1-bbbb.tmp').write_bytes(b'x')
        live = Live(prepare=prep)
        try:
            self.assertEqual(list(live.data.rglob('*.tmp')), [])
        finally:
            live.close()

    def test_status_reports_instance(self):
        live = Live()
        try:
            s1 = json.loads(live.get('/api/status')[2])
            self.assertEqual(len(s1['instance']), 16)
        finally:
            live.close()


# ------------------------------------------------------------ image parsing

class TestImageInfo(unittest.TestCase):
    def test_headers(self):
        self.assertEqual(srv.image_info(fx.png_bytes(3, 5)), ('png', 3, 5))
        self.assertEqual(srv.image_info(fx.jpeg_header(640, 480, exif=True)), ('jpeg', 640, 480))
        self.assertEqual(srv.image_info(fx.webp_vp8x(1000, 20)), ('webp', 1000, 20))
        self.assertEqual(srv.image_info(fx.webp_vp8l(64, 32)), ('webp', 64, 32))
        self.assertEqual(srv.image_info(fx.webp_vp8(77, 66)), ('webp', 77, 66))
        for junk in [b'', b'\xff\xd8\xff', b'\xff\xd8\xff\xda\x00\x02', b'RIFF0000WEBPVP8X', b'GIF89a' + b'\x00' * 30, b'<svg/>']:
            self.assertIsNone(srv.image_info(junk))


if __name__ == '__main__':
    unittest.main()
