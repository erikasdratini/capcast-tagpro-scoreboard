#!/usr/bin/env python3
"""Offline TagPro Scoreboard server. Python standard library only.
Serves ONLY localhost and only files from this application directory.
"""
import argparse
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data' / 'state.json'
LOCK = threading.Lock()
MAX_BYTES = 3 * 1024 * 1024
STATIC = {'/': 'index.html', '/index.html': 'index.html', '/overlay.html': 'overlay.html', '/styles.css': 'styles.css', '/app.js': 'app.js', '/overlay.js': 'overlay.js'}
TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8'}
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


def validated(data):
    if not isinstance(data, dict):
        raise ValueError('State must be an object')
    if data.get('mode') not in (5, 7):
        raise ValueError('Mode must be 5 or 7 games')
    teams = data.get('teams', [])
    games = data.get('games', [])
    presets = data.get('presets', [])
    if not isinstance(teams, list) or len(teams) > 700 or not isinstance(games, list) or len(games) != 7 or not isinstance(presets, list) or len(presets) > 150:
        raise ValueError('Invalid library or game count')
    seen = set()
    for t in teams:
        if not isinstance(t, dict) or not isinstance(t.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', t['id']) or t['id'] in seen:
            raise ValueError('Invalid or duplicate team ID')
        seen.add(t['id'])
        for field, limit in [('name', 110), ('code', 12), ('era', 80)]:
            if not isinstance(t.get(field), str) or len(t[field]) > limit:
                raise ValueError('Invalid team ' + field)
        if not t['name'].strip() or not t['code'].strip():
            raise ValueError('Team name and code required')
        for key in ('primary', 'secondary'):
            if not isinstance(t.get(key), str) or not HEX.fullmatch(t[key]):
                raise ValueError('Invalid team hex color')
        logo = t.get('logo')
        if logo is not None and (not isinstance(logo, str) or len(logo) > 800000 or not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+', logo)):
            raise ValueError('Invalid embedded logo; use a small PNG/JPEG/WEBP')
    if data.get('teamA') not in seen or data.get('teamB') not in seen:
        raise ValueError('Choose existing teams')
    for g in games:
        if not isinstance(g, dict) or not isinstance(g.get('map'), str) or len(g['map']) > 70:
            raise ValueError('Invalid map')
        for f in ('scoreA', 'scoreB'):
            n = g.get(f)
            if n is not None and (type(n) is not int or n < 0 or n > 999):
                raise ValueError('Scores must be whole numbers between 0 and 999')
        if g.get('otWinner') not in (None, 'A', 'B') or not isinstance(g.get('ot'), bool):
            raise ValueError('Invalid OT settings')
    for p in presets:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or len(p['id']) > 80 or not isinstance(p.get('name'), str) or len(p['name']) > 100:
            raise ValueError('Invalid preset')
        maps = p.get('maps')
        if not isinstance(maps, list) or len(maps) != 7 or any(not isinstance(m, str) or len(m) > 70 for m in maps):
            raise ValueError('Preset must have 7 map slots')
    meta = data.get('meta')
    if not isinstance(meta, dict):
        raise ValueError('Invalid metadata')
    for k in ('league', 'event', 'label'):
        if not isinstance(meta.get(k), str) or len(meta[k]) > 100:
            raise ValueError('Invalid metadata ' + k)
    if type(meta.get('scale')) not in (int, float) or not 0.6 <= meta['scale'] <= 1.5:
        raise ValueError('Invalid scale')
    return data


class Handler(BaseHTTPRequestHandler):
    def _trusted_host(self):
        # Reject unexpected Host headers, including DNS rebinding attempts.
        return self.headers.get('Host') == '127.0.0.1:' + str(self.server.server_port)

    def _reject_untrusted_host(self):
        if self._trusted_host():
            return False
        self.send_data(403, b'Invalid host', 'text/plain; charset=utf-8')
        return True

    def send_data(self, status, raw, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'")
        self.send_header('Access-Control-Allow-Origin', 'http://127.0.0.1:'+str(self.server.server_port))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self._reject_untrusted_host():
            return
        path = urlparse(self.path).path
        if path == '/api/state':
            with LOCK:
                raw = DATA.read_bytes()
            return self.send_data(200, raw, 'application/json; charset=utf-8')
        if path not in STATIC:
            return self.send_data(404, b'Not found', 'text/plain')
        f = ROOT / STATIC[path]
        return self.send_data(200, f.read_bytes(), TYPES[f.suffix])

    def do_POST(self):
        if self._reject_untrusted_host():
            return
        if urlparse(self.path).path != '/api/state':
            return self.send_data(404, b'Not found', 'text/plain')
        if self.headers.get('Origin') != 'http://127.0.0.1:'+str(self.server.server_port):
            return self.send_data(403, b'Bad origin', 'text/plain')
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return self.send_data(415, b'JSON required', 'text/plain')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 2 or length > MAX_BYTES:
                raise ValueError('Request too large or empty')
            data = validated(json.loads(self.rfile.read(length).decode('utf-8')))
            raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            with LOCK:
                with tempfile.NamedTemporaryFile(dir=DATA.parent, delete=False, suffix='.tmp') as tmp:
                    tmp.write(raw)
                    temp_path = tmp.name
                os.replace(temp_path, DATA)
            self.send_data(200, b'{"ok":true}', 'application/json')
        except (ValueError, UnicodeError, json.JSONDecodeError) as e:
            self.send_data(400, json.dumps({'error': str(e)}).encode(), 'application/json')
        except Exception as e:
            self.send_data(500, json.dumps({'error': 'Unable to save state'}).encode(), 'application/json')
            print('Save error:', e)

    def log_message(self, format, *args):
        if not self.path.startswith('/api/state'):
            return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be 1024–65535')
    if not DATA.exists():
        raise SystemExit('Missing data/state.json')
    # Bind loopback ONLY; don't expose operator controls on your home network.
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print('\nTAGPRO SCOREBOARD — running locally')
        print(f'Control dashboard: http://127.0.0.1:{args.port}/')
        print(f'OBS Browser Source: http://127.0.0.1:{args.port}/overlay.html')
        print('Keep this window open while broadcasting. Ctrl+C to close.\n')
        try:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{args.port}/')
        except Exception:
            pass
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')

if __name__ == '__main__':
    main()
