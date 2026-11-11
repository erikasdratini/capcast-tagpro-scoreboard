#!/usr/bin/env python3
"""CAPCAST v1.1.1 - local TagPro scoreboard server for OBS Studio.

Python standard library only. The server listens on 127.0.0.1 (this computer)
and serves a fixed list of files from this folder plus a small JSON API used by
the control dashboard and the OBS overlay. Data lives in data/state.json.

Security model (see SECURITY.md):
  * loopback-only socket; exact Host check (blocks DNS rebinding)
  * cross-site requests refused (Origin + Sec-Fetch-Site); JSON-only writes
  * every save is validated against a strict schema, including image headers
  * optimistic concurrency (rev) so two dashboards cannot silently overwrite
  * atomic, fsync'd writes; automatic backups before risky operations
"""
import argparse
import base64
import binascii
import datetime
import errno
import json
import os
import re
import secrets
import shutil
import socket
import struct
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

VERSION = '1.1.1'
SCHEMA_VERSION = 2
MIN_PYTHON = (3, 9)
RECOMMENDED_PYTHON = (3, 11)
HOST = '127.0.0.1'
DEFAULT_PORT = 8765

ROOT = Path(__file__).resolve().parent
STARTER = ROOT / 'starter-state.json'

# Keep in sync with LIMITS in shared.js (tests/test_server.py checks this).
LIMITS = {
    'teams': 700,
    'presets': 150,
    'games': 7,
    'teamId': 80,
    'teamName': 110,
    'teamCode': 12,
    'teamEra': 80,
    'presetId': 80,
    'presetName': 100,
    'mapName': 70,
    'meta': 100,
    'scoreMax': 999,
    'scaleMin': 0.6,
    'scaleMax': 1.5,
    'logoChars': 800000,
    'logoBytes': 600000,
    'logoDim': 2048,
    'stateBytes': 3500000,
}
MAX_BODY = 5 * 1024 * 1024     # request body cap (restore envelopes included)
MAX_HANDLERS = 48              # concurrent connections served at once
SOCKET_TIMEOUT = 15            # seconds a client may stay idle
AUTO_BACKUP_SECONDS = 600
KEEP_BACKUPS = 30
CLIENT_TTL = 15.0
MAX_CLIENTS = 32
CHECKPOINTS = ('clear-scores', 'delete-team', 'delete-preset', 'import-library', 'overwrite-conflict')

STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/overlay.html': ('overlay.html', 'text/html; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    '/shared.js': ('shared.js', 'text/javascript; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/overlay.js': ('overlay.js', 'text/javascript; charset=utf-8'),
}
PAGES = ('/', '/index.html', '/overlay.html')

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
SECURITY_HEADERS = (
    ('Cache-Control', 'no-store'),
    ('X-Content-Type-Options', 'nosniff'),
    ('Referrer-Policy', 'no-referrer'),
    ('X-Frame-Options', 'SAMEORIGIN'),
    ('Content-Security-Policy', CSP),
    ('Cross-Origin-Resource-Policy', 'same-origin'),
    ('Cross-Origin-Opener-Policy', 'same-origin'),
    ('Permissions-Policy', 'camera=(), microphone=(), geolocation=()'),
)

ID_RE = re.compile(r'[A-Za-z0-9_-]{1,80}')
CLIENT_ID_RE = re.compile(r'[A-Za-z0-9-]{8,40}')
HEX_RE = re.compile(r'#[0-9a-fA-F]{6}')
LOGO_RE = re.compile(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})')
CONTROL_RE = re.compile('[\x00-\x1f\x7f-\x9f]')
SURROGATE_RE = re.compile('[\ud800-\udfff]')
TEMP_RE = re.compile(r'(tmp\w+|\.state-[\w-]+)\.tmp')
ROTATING_RE = re.compile(r'(auto|before-[a-z-]+)-\d{8}-\d{6}-[0-9a-f]{4}\.json')


# --------------------------------------------------------------------------- output

def say(*parts):
    """print() that never fails on consoles without UTF-8."""
    text = ' '.join(str(p) for p in parts)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'), flush=True)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def human_size(n):
    return '%.1f MB' % (n / 1e6) if n >= 100000 else '%d bytes' % n


# --------------------------------------------------------------------------- JSON

class BadRequest(ValueError):
    def __init__(self, message, path=None, status=400, reason=None):
        super().__init__(message)
        self.message = message
        self.path = path
        self.status = status
        self.reason = reason


def _no_constants(name):
    raise ValueError('NaN and Infinity are not allowed')


def _safe_int(text):
    if len(text) > 15:
        raise ValueError('number too long')
    return int(text)


def _safe_float(text):
    if len(text) > 32:
        raise ValueError('number too long')
    return float(text)


def describe(e):
    if isinstance(e, BadRequest):
        return '%s%s' % (e.path + ': ' if e.path else '', e.message)
    return printable('%s: %s' % (type(e).__name__, e), 160)


def parse_json(raw):
    """Parse untrusted JSON bytes. Rejects NaN/Infinity and huge numbers."""
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise BadRequest('Body is not valid UTF-8')
    if text.startswith('﻿'):
        text = text[1:]
    try:
        return json.loads(text, parse_constant=_no_constants, parse_int=_safe_int, parse_float=_safe_float)
    except RecursionError:
        raise BadRequest('JSON is nested too deeply')
    except ValueError as e:
        raise BadRequest('Invalid JSON: %s' % e)


def dump_state(state):
    return json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def measure(state):
    """Size used for the data budget. Same rule as shared.js: rev and savedAt
    count as 0 and null, so the budget never depends on how many saves happened."""
    probe = dict(state)
    probe['rev'], probe['savedAt'] = 0, None
    return len(dump_state(probe))


# --------------------------------------------------------------------------- images

def image_info(data):
    """Return (type, width, height) from PNG/JPEG/WebP header bytes, or None."""
    n = len(data)
    if n >= 24 and data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
        w, h = struct.unpack('>II', data[16:24])
        return 'png', w, h
    if n >= 4 and data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF:
        i = 2
        while i < n:
            if data[i] != 0xFF:
                return None
            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                return None
            marker = data[i]
            i += 1
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue
            if marker in (0xD9, 0xDA):
                return None
            if i + 2 > n:
                return None
            length = (data[i] << 8) | data[i + 1]
            if length < 2:
                return None
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if i + 7 > n:
                    return None
                h = (data[i + 3] << 8) | data[i + 4]
                w = (data[i + 5] << 8) | data[i + 6]
                return 'jpeg', w, h
            i += length
        return None
    if n >= 30 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        chunk = data[12:16]
        if chunk == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            w = (data[26] | (data[27] << 8)) & 0x3FFF
            h = (data[28] | (data[29] << 8)) & 0x3FFF
            return 'webp', w, h
        if chunk == b'VP8L' and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], 'little')
            return 'webp', (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b'VP8X':
            return 'webp', 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
    return None


def check_logo(value):
    """Validate an embedded logo data URL. Returns None if OK, else a reason string."""
    if not isinstance(value, str):
        return 'logo must be text'
    if len(value) > LIMITS['logoChars']:
        return 'logo is too large (%d characters)' % len(value)
    m = LOGO_RE.fullmatch(value)
    if not m:
        return 'logo must be a PNG, JPEG or WebP data URL'
    declared, b64 = m.group(1), m.group(2)
    if len(b64) % 4:
        return 'logo is not valid base64'
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return 'logo is not valid base64'
    if len(data) > LIMITS['logoBytes']:
        return 'logo is too large (%d bytes)' % len(data)
    info = image_info(data)
    if not info:
        return 'logo is not a readable PNG, JPEG or WebP image'
    kind, w, h = info
    if kind != declared:
        return 'logo says image/%s but contains %s data' % (declared, kind.upper())
    if not (1 <= w <= LIMITS['logoDim'] and 1 <= h <= LIMITS['logoDim']):
        return 'logo dimensions %dx%d exceed %dx%d' % (w, h, LIMITS['logoDim'], LIMITS['logoDim'])
    return None


# --------------------------------------------------------------------------- validation

def _is_int(v):
    return type(v) is int


def _blank(v):
    # Same as JavaScript's v.trim() === '' once control characters are excluded.
    return not v.replace('\ufeff', ' ').strip()


def _text(v, path, limit, required=False):
    if not isinstance(v, str):
        raise BadRequest('must be text', path)
    if SURROGATE_RE.search(v):
        raise BadRequest('contains an invalid character', path)
    if CONTROL_RE.search(v):
        raise BadRequest('contains a control character', path)
    if len(v) > limit:
        raise BadRequest('longer than %d characters' % limit, path)
    if required and _blank(v):
        raise BadRequest('is required', path)
    return v


def _exact_keys(obj, allowed, path):
    if not isinstance(obj, dict):
        raise BadRequest('must be an object', path)
    for k in obj:
        if k not in allowed:
            raise BadRequest('unknown field', '%s.%s' % (path, k))


TOP_KEYS = ('schemaVersion', 'rev', 'savedAt', 'mode', 'teamA', 'teamB', 'meta', 'games', 'presets', 'teams')
TEAM_KEYS = ('id', 'name', 'code', 'era', 'primary', 'secondary', 'logo')
GAME_KEYS = ('map', 'scoreA', 'scoreB', 'ot', 'otWinner')
PRESET_KEYS = ('id', 'name', 'maps')
META_KEYS = ('league', 'event', 'label', 'scale')


def validate_strict(d):
    """Validate a v2 state from the dashboard. Returns a canonical copy (rev/savedAt unset)."""
    _exact_keys(d, TOP_KEYS, 'state')
    if d.get('schemaVersion') != SCHEMA_VERSION or not _is_int(d.get('schemaVersion')):
        raise BadRequest('must be %d' % SCHEMA_VERSION, 'state.schemaVersion')
    mode = d.get('mode')
    if not _is_int(mode) or mode not in (5, 7):
        raise BadRequest('must be 5 or 7', 'state.mode')

    teams = d.get('teams')
    if not isinstance(teams, list) or len(teams) > LIMITS['teams'] or not teams:
        raise BadRequest('must be a list of 1-%d teams' % LIMITS['teams'], 'state.teams')
    out_teams, seen = [], set()
    for i, t in enumerate(teams):
        p = 'state.teams[%d]' % i
        _exact_keys(t, TEAM_KEYS, p)
        tid = t.get('id')
        if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
            raise BadRequest('invalid team id', p + '.id')
        if tid in seen:
            raise BadRequest('duplicate team id', p + '.id')
        seen.add(tid)
        for key in ('primary', 'secondary'):
            if not isinstance(t.get(key), str) or not HEX_RE.fullmatch(t[key]):
                raise BadRequest('must be a #RRGGBB color', '%s.%s' % (p, key))
        logo = t.get('logo')
        if logo is not None:
            reason = check_logo(logo)
            if reason:
                raise BadRequest(reason, p + '.logo')
        out_teams.append({
            'id': tid,
            'name': _text(t.get('name'), p + '.name', LIMITS['teamName'], True),
            'code': _text(t.get('code'), p + '.code', LIMITS['teamCode'], True),
            'era': _text(t.get('era'), p + '.era', LIMITS['teamEra']),
            'primary': t['primary'],
            'secondary': t['secondary'],
            'logo': logo,
        })
    for side in ('teamA', 'teamB'):
        if not isinstance(d.get(side), str) or d.get(side) not in seen:
            raise BadRequest('must be a team in the library', 'state.' + side)

    games = d.get('games')
    if not isinstance(games, list) or len(games) != LIMITS['games']:
        raise BadRequest('must be a list of 7 games', 'state.games')
    out_games = []
    for i, g in enumerate(games):
        p = 'state.games[%d]' % i
        _exact_keys(g, GAME_KEYS, p)
        for key in ('scoreA', 'scoreB'):
            v = g.get(key)
            if v is not None and (not _is_int(v) or not 0 <= v <= LIMITS['scoreMax']):
                raise BadRequest('must be a whole number 0-%d' % LIMITS['scoreMax'], '%s.%s' % (p, key))
        if not isinstance(g.get('ot'), bool):
            raise BadRequest('must be true or false', p + '.ot')
        if g.get('otWinner') not in (None, 'A', 'B'):
            raise BadRequest('must be A, B or empty', p + '.otWinner')
        if g.get('otWinner') is not None and not g['ot']:
            raise BadRequest('overtime winner set but OT is not ticked', p + '.otWinner')
        out_games.append({'map': _text(g.get('map'), p + '.map', LIMITS['mapName']),
                          'scoreA': g.get('scoreA'), 'scoreB': g.get('scoreB'),
                          'ot': g['ot'], 'otWinner': g.get('otWinner')})

    presets = d.get('presets')
    if not isinstance(presets, list) or len(presets) > LIMITS['presets']:
        raise BadRequest('must be a list of at most %d presets' % LIMITS['presets'], 'state.presets')
    out_presets, pseen = [], set()
    for i, pr in enumerate(presets):
        p = 'state.presets[%d]' % i
        _exact_keys(pr, PRESET_KEYS, p)
        pid = pr.get('id')
        if not isinstance(pid, str) or not ID_RE.fullmatch(pid):
            raise BadRequest('invalid preset id', p + '.id')
        if pid in pseen:
            raise BadRequest('duplicate preset id', p + '.id')
        pseen.add(pid)
        maps = pr.get('maps')
        if not isinstance(maps, list) or len(maps) != LIMITS['games']:
            raise BadRequest('must have 7 map slots', p + '.maps')
        out_presets.append({'id': pid,
                            'name': _text(pr.get('name'), p + '.name', LIMITS['presetName']),
                            'maps': [_text(m, '%s.maps[%d]' % (p, j), LIMITS['mapName']) for j, m in enumerate(maps)]})

    meta = d.get('meta')
    _exact_keys(meta, META_KEYS, 'state.meta')
    scale = meta.get('scale')
    if type(scale) not in (int, float) or not LIMITS['scaleMin'] <= scale <= LIMITS['scaleMax']:
        raise BadRequest('must be between %s and %s' % (LIMITS['scaleMin'], LIMITS['scaleMax']), 'state.meta.scale')
    out_meta = {k: _text(meta.get(k), 'state.meta.' + k, LIMITS['meta']) for k in ('league', 'event', 'label')}
    out_meta['scale'] = int(scale) if isinstance(scale, float) and scale.is_integer() else scale

    state = {'schemaVersion': SCHEMA_VERSION, 'rev': 0, 'savedAt': None, 'mode': mode,
             'teamA': d['teamA'], 'teamB': d['teamB'], 'meta': out_meta,
             'games': out_games, 'presets': out_presets, 'teams': out_teams}
    size = measure(state)
    if size > LIMITS['stateBytes']:
        raise BadRequest('data is too large (%s of %s); remove some logos' % (human_size(size), human_size(LIMITS['stateBytes'])), 'state')
    return state


class Migration:
    """Collects human-readable notes and set-aside logos while upgrading old data."""

    def __init__(self):
        self.notes = []
        self.set_aside = []
        self.ignored = []
        self.ignored_bytes = 0

    def finish(self):
        if self.ignored:
            shown = ', '.join(self.ignored[:5]) + (' and %d more' % (len(self.ignored) - 5) if len(self.ignored) > 5 else '')
            self.notes.append('Ignored %d unknown field(s) CAPCAST does not use (%s): %s.'
                              % (len(self.ignored), human_size(self.ignored_bytes), shown))

    def note(self, text):
        self.notes.append(text)


def _size_of(value):
    try:
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    except (TypeError, ValueError):
        return 0


def _mig_text(v, path, limit, mig, required=False, fallback=None):
    if not isinstance(v, str):
        raise BadRequest('must be text', path)
    fixed = v
    if SURROGATE_RE.search(fixed):
        fixed = SURROGATE_RE.sub('�', fixed)
        mig.note('%s: replaced invalid characters with "�".' % path)
    if CONTROL_RE.search(fixed):
        fixed = CONTROL_RE.sub(' ', fixed)
        mig.note('%s: replaced control characters (tabs, line breaks) with spaces.' % path)
    if len(fixed) > limit:
        raise BadRequest('longer than %d characters' % limit, path)
    if required and _blank(fixed):
        if not fallback:
            raise BadRequest('is required', path)
        mig.note('%s: was empty after cleanup; set to %r.' % (path, fallback))
        fixed = fallback
    return fixed


def printable(text, limit=60):
    """Escape control characters and bound the length of text taken from data files."""
    text = CONTROL_RE.sub(lambda m: '\\x%02x' % ord(m.group()), SURROGATE_RE.sub('\ufffd', str(text)))
    return text if len(text) <= limit else text[:limit - 1] + '\u2026'


def _drop_unknown(obj, allowed, path, mig):
    for k in list(obj):
        if k not in allowed:
            name = '%s.%s' % (path, printable(k))
            mig.ignored.append(name)
            mig.ignored_bytes += _size_of(obj[k])


def migrate(d, mig):
    """Upgrade a v1.1 (schema 1) or v1.1.1 (schema 2) state/backup to a canonical v2 state.

    Data that v1.1 itself would have accepted is never rejected: values are kept,
    normalized with a note, or (for logos that fail the new image checks) set
    aside with a note so they can be recovered. Data v1.1 would have refused is
    rejected with a clear error instead of being partly imported.
    """
    if not isinstance(d, dict):
        raise BadRequest('A CAPCAST backup must be a JSON object', 'backup')
    version = d.get('schemaVersion', 1)
    if not _is_int(version) or version < 1:
        raise BadRequest('unknown schemaVersion', 'backup.schemaVersion')
    if version > SCHEMA_VERSION:
        raise BadRequest('this backup was made by a newer CAPCAST (schema %d)' % version, 'backup.schemaVersion')
    if version == 1:
        mig.note('Upgraded data from CAPCAST v1.1 format to v1.1.1 format.')
    _drop_unknown(d, TOP_KEYS, 'backup', mig)

    mode = d.get('mode')
    if isinstance(mode, float) and mode in (5.0, 7.0):
        mig.note('Match format %r stored as %d.' % (mode, int(mode)))
        mode = int(mode)
    if not _is_int(mode) or mode not in (5, 7):
        raise BadRequest('match format must be 5 or 7 games', 'backup.mode')

    teams = d.get('teams')
    if not isinstance(teams, list) or not teams or len(teams) > LIMITS['teams']:
        raise BadRequest('must contain 1-%d teams' % LIMITS['teams'], 'backup.teams')
    out_teams, seen = [], set()
    for i, t in enumerate(teams):
        p = 'backup.teams[%d]' % i
        if not isinstance(t, dict):
            raise BadRequest('must be an object', p)
        _drop_unknown(t, TEAM_KEYS, p, mig)
        tid = t.get('id')
        if not isinstance(tid, str) or not ID_RE.fullmatch(tid) or tid in seen:
            raise BadRequest('missing, invalid or duplicate team id', p + '.id')
        seen.add(tid)
        name = _mig_text(t.get('name'), p + '.name', LIMITS['teamName'], mig, True, fallback=tid)
        code = _mig_text(t.get('code'), p + '.code', LIMITS['teamCode'], mig, True, fallback=tid[:LIMITS['teamCode']].upper())
        era = _mig_text(t.get('era'), p + '.era', LIMITS['teamEra'], mig)
        for key in ('primary', 'secondary'):
            if not isinstance(t.get(key), str) or not HEX_RE.fullmatch(t[key]):
                raise BadRequest('must be a #RRGGBB color', '%s.%s' % (p, key))
        logo = t.get('logo')
        if logo is not None:
            if not isinstance(logo, str):
                raise BadRequest('logo must be text or empty', p + '.logo')
            reason = check_logo(logo)
            if reason:
                mig.set_aside.append({'teamId': tid, 'teamName': name, 'reason': reason, 'logo': logo})
                mig.note('Logo for "%s" was set aside: %s. It is kept in data/quarantine/; '
                         're-add it in Team Library to resize it.' % (name, reason))
                logo = None
        out_teams.append({'id': tid, 'name': name, 'code': code, 'era': era,
                          'primary': t['primary'], 'secondary': t['secondary'], 'logo': logo})
    for side in ('teamA', 'teamB'):
        if not isinstance(d.get(side), str) or d.get(side) not in seen:
            raise BadRequest('must name a team in the library', 'backup.' + side)

    games = d.get('games')
    if not isinstance(games, list) or len(games) != LIMITS['games']:
        raise BadRequest('must contain 7 games', 'backup.games')
    out_games = []
    for i, g in enumerate(games):
        p = 'backup.games[%d]' % i
        if not isinstance(g, dict):
            raise BadRequest('must be an object', p)
        _drop_unknown(g, GAME_KEYS, p, mig)
        a, b = g.get('scoreA'), g.get('scoreB')
        for key, v in (('scoreA', a), ('scoreB', b)):
            if v is not None and (not _is_int(v) or not 0 <= v <= LIMITS['scoreMax']):
                raise BadRequest('must be a whole number 0-%d' % LIMITS['scoreMax'], '%s.%s' % (p, key))
        ot, win = g.get('ot'), g.get('otWinner')
        if not isinstance(ot, bool) or win not in (None, 'A', 'B'):
            raise BadRequest('invalid overtime settings', p)
        if win is not None and not ot:
            if _is_int(a) and a == b:
                ot = True
                mig.note('Game %d: overtime winner %s kept and the OT box ticked to match (it already decided this game).' % (i + 1, win))
            else:
                mig.note('Game %d: removed overtime winner %s because OT was not ticked and the score was not tied '
                         '(it had no effect on totals).' % (i + 1, win))
                win = None
        out_games.append({'map': _mig_text(g.get('map'), p + '.map', LIMITS['mapName'], mig),
                          'scoreA': a, 'scoreB': b, 'ot': ot, 'otWinner': win})

    presets = d.get('presets')
    if not isinstance(presets, list) or len(presets) > LIMITS['presets']:
        raise BadRequest('must contain at most %d presets' % LIMITS['presets'], 'backup.presets')
    out_presets, pseen = [], set()
    for i, pr in enumerate(presets):
        p = 'backup.presets[%d]' % i
        if not isinstance(pr, dict):
            raise BadRequest('must be an object', p)
        _drop_unknown(pr, PRESET_KEYS, p, mig)
        pid = pr.get('id')
        if not isinstance(pid, str) or len(pid) > LIMITS['presetId']:
            raise BadRequest('invalid preset id', p + '.id')
        if not ID_RE.fullmatch(pid) or pid in pseen:
            new = 'preset-migrated-%d' % (i + 1)
            while new in pseen:
                new += 'x'
            mig.note('Preset %d: id %r replaced with %r (ids must be unique letters, digits, - or _).' % (i + 1, printable(pid), new))
            pid = new
        pseen.add(pid)
        maps = pr.get('maps')
        if not isinstance(maps, list) or len(maps) != LIMITS['games']:
            raise BadRequest('must have 7 map slots', p + '.maps')
        out_presets.append({'id': pid,
                            'name': _mig_text(pr.get('name'), p + '.name', LIMITS['presetName'], mig),
                            'maps': [_mig_text(m, '%s.maps[%d]' % (p, j), LIMITS['mapName'], mig) for j, m in enumerate(maps)]})

    meta = d.get('meta')
    if not isinstance(meta, dict):
        raise BadRequest('must be an object', 'backup.meta')
    _drop_unknown(meta, META_KEYS, 'backup.meta', mig)
    scale = meta.get('scale')
    if type(scale) not in (int, float) or not LIMITS['scaleMin'] <= scale <= LIMITS['scaleMax']:
        raise BadRequest('overlay scale must be between 0.6 and 1.5', 'backup.meta.scale')
    out_meta = {k: _mig_text(meta.get(k), 'backup.meta.' + k, LIMITS['meta'], mig) for k in ('league', 'event', 'label')}
    out_meta['scale'] = int(scale) if isinstance(scale, float) and scale.is_integer() else scale

    mig.finish()
    rev = d.get('rev') if version == SCHEMA_VERSION and _is_int(d.get('rev')) and d.get('rev') >= 0 else 0
    saved = d.get('savedAt') if isinstance(d.get('savedAt'), str) and len(d.get('savedAt')) <= 40 else None
    state = {'schemaVersion': SCHEMA_VERSION, 'rev': rev, 'savedAt': saved, 'mode': mode,
             'teamA': d['teamA'], 'teamB': d['teamB'], 'meta': out_meta,
             'games': out_games, 'presets': out_presets, 'teams': out_teams}
    size = measure(state)
    if size > LIMITS['stateBytes']:
        raise BadRequest('data is too large (%s of %s); remove some logos' % (human_size(size), human_size(LIMITS['stateBytes'])), 'backup')
    return state


# --------------------------------------------------------------------------- files

def fsync_dir(path):
    if os.name == 'nt':
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def replace_with_retry(src, dst, attempts=6):
    """os.replace, retried briefly: on Windows another program (antivirus, sync
    tools) can hold the file open for a moment."""
    for attempt in range(attempts):
        try:
            os.replace(str(src), str(dst))
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (2 ** attempt))


def atomic_write(path, data):
    """Write bytes to path atomically: temp file + fsync + replace. Never leaves temp files."""
    path = Path(os.path.realpath(str(path)))   # a symlinked state.json keeps pointing at its target
    tmp = path.parent / ('.state-%d-%s.tmp' % (os.getpid(), secrets.token_hex(6)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
    try:
        fd = os.open(str(tmp), flags, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
        fsync_dir(path.parent)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- store

class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / 'state.json'
        self.backup_dir = self.data_dir / 'backups'
        self.quarantine_dir = self.data_dir / 'quarantine'
        self.lock = threading.RLock()
        self.state = None
        self.raw = b''
        self.file_sig = None
        self.last_auto_backup = 0.0
        self.session = secrets.token_hex(8)     # changes on every start; dashboards reload when it does
        self.notice_seq = 0
        self.notices = []
        self.clients = {}

    # notices ---------------------------------------------------------------
    def notice(self, text, level='info'):
        text = printable(text, 600)
        with self.lock:
            self.notice_seq += 1
            self.notices.append({'id': '%s-%d' % (self.session, self.notice_seq),
                                 'level': level, 'text': text, 'at': now_iso()})
            del self.notices[:-50]
        say('[%s] %s' % (level.upper(), text))

    def report_notes(self, mig, label):
        """Show migration notes as notices; long lists go to a text file in data/backups/."""
        notes = mig.notes
        shown = notes if len(notes) <= 10 else notes[:8]
        for text in shown:
            warn = any(w in text for w in ('set aside', 'Ignored', 'removed', 'replaced', 'empty after'))
            self.notice(text, 'warning' if warn else 'info')
        if len(notes) > len(shown):
            try:
                self._ensure_dir(self.backup_dir)
                name = '%s-report-%s-%s.txt' % (label, stamp(), secrets.token_hex(2))
                atomic_write(self.backup_dir / name, ('\n'.join(notes) + '\n').encode('utf-8'))
                where = 'data/backups/' + name
            except OSError as e:
                where = 'the CAPCAST window (could not write a report file: %s)' % e
            self.notice('%d more adjustment(s); the full list is in %s.' % (len(notes) - len(shown), where), 'warning')

    # disk helpers ------------------------------------------------------------
    def _sig(self):
        try:
            st = os.stat(str(self.state_path))
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _write_state(self, state):
        raw = dump_state(state)
        atomic_write(self.state_path, raw)
        self.state, self.raw, self.file_sig = state, raw, self._sig()

    def _ensure_dir(self, path):
        path.mkdir(parents=True, exist_ok=True)

    def backup(self, label, raw=None):
        """Copy the current (or given) state bytes into data/backups/."""
        raw = self.raw if raw is None else raw
        if not raw:
            return None
        self._ensure_dir(self.backup_dir)
        name = '%s-%s-%s.json' % (label, stamp(), secrets.token_hex(2))
        atomic_write(self.backup_dir / name, raw)
        self._rotate()
        return 'data/backups/' + name

    def safe_backup(self, label, raw=None):
        """backup() that reports problems as a notice instead of failing the user's action."""
        try:
            return self.backup(label, raw)
        except OSError as e:
            self.notice('Could not write a safety backup (%s): %s. Your change was still saved.' % (label, e), 'warning')
            return None

    def _rotate(self):
        try:
            # Only files CAPCAST named itself; migration copies and user files are never deleted.
            files = [p for p in self.backup_dir.glob('*.json') if ROTATING_RE.fullmatch(p.name) and not p.is_symlink()]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[KEEP_BACKUPS:]:
                old.unlink()
        except OSError:
            pass

    def quarantine(self, label, raw):
        self._ensure_dir(self.quarantine_dir)
        name = '%s-%s-%s.json' % (label, stamp(), secrets.token_hex(2))
        atomic_write(self.quarantine_dir / name, raw)
        return 'data/quarantine/' + name

    def _set_aside(self, mig):
        if not mig.set_aside:
            return None
        payload = json.dumps({'note': 'Logos set aside by CAPCAST because they failed the v1.1.1 image checks.',
                              'logos': mig.set_aside}, ensure_ascii=False, indent=1).encode('utf-8')
        return self.quarantine('logos-set-aside', payload)

    def _clean_temps(self):
        removed = 0
        candidates = []
        for folder in (self.data_dir, self.backup_dir, self.quarantine_dir):
            try:
                candidates.extend(folder.glob('*.tmp'))
                candidates.extend(folder.glob('.*.tmp'))
            except OSError:
                pass
        for p in set(candidates):
            if TEMP_RE.fullmatch(p.name) and not p.is_symlink():
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        if removed:
            self.notice('Removed %d leftover temporary file(s) from an earlier run.' % removed)

    # startup -----------------------------------------------------------------
    def load_initial(self):
        self._ensure_dir(self.data_dir)
        self._clean_temps()
        with self.lock:
            if not self.state_path.exists():
                if not STARTER.exists():
                    raise SystemExit('starter-state.json is missing; re-extract the CAPCAST ZIP.')
                state = migrate(parse_json(STARTER.read_bytes()), Migration())
                self._write_state(state)
                self.notice('Created data/state.json from the starter team list.')
                return
            raw = self.state_path.read_bytes()
            try:
                mig = Migration()
                state = migrate(parse_json(raw), mig)
            except Exception as e:  # noqa: BLE001 - any bad file is quarantined, never fatal
                where = self.quarantine('state-invalid', raw)
                self.notice('data/state.json could not be loaded (%s). A copy was saved as %s.' % (describe(e), where), 'warning')
                state = self._recover()
                self._write_state(state)
                return
            if mig.notes or raw != dump_state(state):
                if mig.notes:
                    where = self.backup('pre-1.1.1-migration', raw)
                    self.notice('Your data was upgraded. The original file was saved as %s.' % where)
                self.report_notes(mig, 'migration')
                where = self._set_aside(mig)
                if where:
                    self.notice('Set-aside logos were saved in %s.' % where, 'warning')
                self._write_state(state)
            else:
                self.state, self.raw, self.file_sig = state, raw, self._sig()

    def _recover(self):
        """Newest valid backup, else the starter file."""
        try:
            files = sorted(self.backup_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            files = []
        for p in files:
            if p.is_symlink():
                continue
            try:
                state = migrate(parse_json(p.read_bytes()), Migration())
                self.notice('Loaded the most recent backup instead: data/backups/%s.' % printable(p.name), 'warning')
                return state
            except Exception:  # noqa: BLE001
                continue
        self.notice('No usable backup was found; started from the starter team list.', 'warning')
        return migrate(parse_json(STARTER.read_bytes()), Migration())

    # reads -------------------------------------------------------------------
    def refresh_if_changed(self):
        """Pick up a state.json edited or replaced on disk while running (validated)."""
        with self.lock:
            sig = self._sig()
            if sig == self.file_sig:
                return
            if sig is None:
                self.notice('data/state.json disappeared; it was written again from memory.', 'warning')
                self._write_state(self.state)
                return
            try:
                raw = self.state_path.read_bytes()
            except OSError:
                return
            if raw == self.raw:
                self.file_sig = sig
                return
            try:
                mig = Migration()
                state = migrate(parse_json(raw), mig)
            except Exception as e:  # noqa: BLE001 - any bad file is quarantined, never fatal
                where = self.quarantine('state-invalid', raw)
                self.notice('data/state.json was changed outside CAPCAST but is invalid (%s). '
                            'It was ignored and saved as %s.' % (describe(e), where), 'warning')
                self._write_state(self.state)
                return
            self.safe_backup('before-external-change')
            if mig.notes:
                kept = self.safe_backup('external-original', raw)   # keep the dropped-in file byte-for-byte
                if kept:
                    self.notice('The file you placed was upgraded; its original was saved as %s.' % kept)
            where = self._set_aside(mig)
            state['rev'] = self.state['rev'] + 1
            state['savedAt'] = now_iso()
            self._write_state(state)
            self.notice('data/state.json was changed outside CAPCAST and has been loaded (rev %d).' % state['rev'], 'warning')
            self.report_notes(mig, 'external-change')
            if where:
                self.notice('Set-aside logos were saved in %s.' % where, 'warning')

    def get_raw(self):
        self.refresh_if_changed()
        with self.lock:
            return self.raw

    # writes ------------------------------------------------------------------
    def save(self, body):
        if not isinstance(body, dict):
            raise BadRequest('body must be an object')
        base, incoming, checkpoint = body.get('baseRev'), body.get('state'), body.get('checkpoint')
        if not _is_int(base):
            raise BadRequest('baseRev is required', 'baseRev')
        if checkpoint is not None and checkpoint not in CHECKPOINTS:
            raise BadRequest('unknown checkpoint', 'checkpoint')
        # Optional, sent by v1.1.1 dashboards: the savedAt and server instance of the version they edited.
        # rev alone is not enough after a restart, because another data folder can be at the same rev.
        has_saved_at = 'baseSavedAt' in body
        base_saved_at, base_instance = body.get('baseSavedAt'), body.get('baseInstance')
        if has_saved_at and base_saved_at is not None and not isinstance(base_saved_at, str):
            raise BadRequest('baseSavedAt must be a string or null', 'baseSavedAt')
        if base_instance is not None and not isinstance(base_instance, str):
            raise BadRequest('baseInstance must be a string', 'baseInstance')
        state = validate_strict(incoming)
        self.refresh_if_changed()
        with self.lock:
            stale = base != self.state['rev'] or (has_saved_at and base_saved_at != self.state['savedAt'])
            if stale:
                if base_instance is not None and base_instance != self.session:
                    raise BadRequest('CAPCAST was restarted and is using different saved data (rev %d). '
                                     'Nothing was overwritten.' % self.state['rev'], 'baseRev', status=409, reason='restarted')
                raise BadRequest('The scoreboard was changed in another window (rev %d). Reload to continue.'
                                 % self.state['rev'], 'baseRev', status=409, reason='changed')
            if checkpoint:
                self.safe_backup('before-' + checkpoint)
            state['rev'] = self.state['rev'] + 1
            state['savedAt'] = now_iso()
            self._write_state(state)
            if time.monotonic() - self.last_auto_backup > AUTO_BACKUP_SECONDS:
                self.last_auto_backup = time.monotonic()
                self.safe_backup('auto')
            return {'ok': True, 'rev': state['rev'], 'savedAt': state['savedAt']}

    def restore(self, body):
        if not isinstance(body, dict):
            raise BadRequest('body must be an object')
        base, backup, dry = body.get('baseRev'), body.get('backup'), body.get('dryRun')
        if not isinstance(dry, bool):
            raise BadRequest('dryRun must be true or false', 'dryRun')
        mig = Migration()
        try:
            state = migrate(backup, mig)
        except BadRequest:
            raise
        except Exception as e:  # noqa: BLE001
            raise BadRequest('This backup could not be read (%s).' % describe(e), 'backup')
        report = {
            'teams': len(state['teams']), 'presets': len(state['presets']),
            'logos': sum(1 for t in state['teams'] if t['logo']),
            'mode': state['mode'],
            'scoresEntered': sum(1 for g in state['games'] if g['scoreA'] is not None or g['scoreB'] is not None),
            'notes': mig.notes, 'ignoredFields': mig.ignored,
            'setAside': [{'team': s['teamName'], 'reason': s['reason']} for s in mig.set_aside],
        }
        if dry:
            return {'ok': True, 'dryRun': True, 'report': report}
        if not _is_int(base):
            raise BadRequest('baseRev is required', 'baseRev')
        self.refresh_if_changed()
        with self.lock:
            if base != self.state['rev']:
                raise BadRequest('The scoreboard was changed in another window (rev %d). Reload and try again.'
                                 % self.state['rev'], 'baseRev', status=409)
            saved_as = self.safe_backup('before-restore') or '(no backup: see notices)'
            try:
                aside = self._set_aside(mig)
            except OSError as e:
                raise BadRequest('Could not save the set-aside logos to data/quarantine (%s); nothing was restored.' % e, status=500)
            state['rev'] = self.state['rev'] + 1
            state['savedAt'] = now_iso()
            self._write_state(state)
            self.notice('Backup restored (rev %d). Previous data saved as %s.' % (state['rev'], saved_as))
            if len(mig.notes) > 0:
                self.report_notes(mig, 'restore')
            if aside:
                self.notice('Set-aside logos were saved in %s.' % aside, 'warning')
            return {'ok': True, 'rev': state['rev'], 'savedAt': state['savedAt'], 'report': report,
                    'previousSavedAs': saved_as, 'setAsideSavedAs': aside}

    # heartbeats --------------------------------------------------------------
    def heartbeat(self, body):
        if not isinstance(body, dict):
            raise BadRequest('body must be an object')
        cid, kind, rev, ok = body.get('id'), body.get('kind'), body.get('rev'), body.get('ok')
        if not isinstance(cid, str) or not CLIENT_ID_RE.fullmatch(cid):
            raise BadRequest('invalid id', 'id')
        if kind not in ('obs', 'browser', 'dashboard'):
            raise BadRequest('invalid kind', 'kind')
        if not _is_int(rev) or rev < -1 or not isinstance(ok, bool):
            raise BadRequest('invalid rev/ok', 'rev')
        err = body.get('error')
        if err is not None and (not isinstance(err, str) or len(err) > 200):
            raise BadRequest('invalid error text', 'error')
        dirty = body.get('dirty', False)
        if not isinstance(dirty, bool):
            raise BadRequest('invalid dirty flag', 'dirty')
        now = time.monotonic()
        with self.lock:
            for k in [k for k, v in self.clients.items() if now - v['seen'] > CLIENT_TTL]:
                del self.clients[k]
            if cid not in self.clients and len(self.clients) >= MAX_CLIENTS:
                oldest = min(self.clients, key=lambda k: self.clients[k]['seen'])
                del self.clients[oldest]
            self.clients[cid] = {'kind': kind, 'rev': rev, 'ok': ok, 'error': err, 'dirty': dirty, 'seen': now}
            return {'ok': True, 'rev': self.state['rev']}

    def status(self):
        self.refresh_if_changed()
        now = time.monotonic()
        with self.lock:
            clients = [{'id': k, 'kind': v['kind'], 'rev': v['rev'], 'ok': v['ok'], 'error': v['error'],
                        'dirty': v['dirty'], 'ageMs': int((now - v['seen']) * 1000)}
                       for k, v in self.clients.items() if now - v['seen'] <= CLIENT_TTL]
            return {'version': VERSION, 'instance': self.session, 'rev': self.state['rev'], 'savedAt': self.state['savedAt'],
                    'clients': clients, 'notices': list(self.notices)}


# --------------------------------------------------------------------------- HTTP

FORBIDDEN_PAGE = '''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>CAPCAST</title></head>
<body><h1>Use the 127.0.0.1 address</h1>
<p>CAPCAST only answers on <a href="http://127.0.0.1:{port}/">http://127.0.0.1:{port}/</a>.
Names such as <b>localhost</b> are refused on purpose, to block DNS-rebinding attacks.</p>
</body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = 'CAPCAST'
    sys_version = ''
    protocol_version = 'HTTP/1.0'
    timeout = SOCKET_TIMEOUT

    def log_message(self, fmt, *args):
        pass

    # responses ---------------------------------------------------------------
    def send_bytes(self, status, raw, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def send_json(self, status, obj):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def send_error(self, code, message=None, explain=None):
        # Replaces http.server's HTML error page: fixed text, our security headers.
        try:
            self.close_connection = True
            text = {400: 'Bad request', 404: 'Not found', 405: 'Method not allowed', 408: 'Request timeout',
                    414: 'URI too long', 431: 'Headers too large', 501: 'Not supported'}.get(code, 'Error')
            self.send_bytes(code, text.encode('ascii'), 'text/plain; charset=utf-8')
        except OSError:
            pass

    def api_error(self, e):
        body = {'error': e.message}
        if e.path:
            body['path'] = e.path
        if e.status == 409:
            body['rev'] = self.server.store.state['rev']
            if e.reason:
                body['reason'] = e.reason
        self.send_json(e.status, body)

    # gate --------------------------------------------------------------------
    def _single(self, name):
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            raise BadRequest('duplicate %s header' % name)
        return values[0] if values else None

    def gate(self, path):
        """Return True if the request may proceed; otherwise a response was sent."""
        try:
            host = self._single('Host')
            for name in ('Origin', 'Content-Type', 'Content-Length', 'Sec-Fetch-Site', 'Sec-Fetch-Mode', 'Sec-Fetch-Dest'):
                self._single(name)
        except BadRequest:
            self.send_bytes(400, b'Bad request', 'text/plain; charset=utf-8')
            return False
        if host != self.server.expected_host:
            page = FORBIDDEN_PAGE.format(port=self.server.server_port).encode('utf-8')
            self.send_bytes(403, page, 'text/html; charset=utf-8')
            return False
        parts = (self.requestline or '').split()
        target = parts[1] if len(parts) >= 2 else ''      # as sent (Python 3.12+ rewrites self.path)
        if not target.startswith('/') or target.startswith('//'):
            self.send_bytes(400, b'Bad request', 'text/plain; charset=utf-8')
            return False
        site = self.headers.get('Sec-Fetch-Site')
        if site is not None and site not in ('same-origin', 'none'):
            navigation = (self.command == 'GET' and path in PAGES
                          and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                          and self.headers.get('Sec-Fetch-Dest') == 'document')
            if not navigation:
                self.send_bytes(403, b'Cross-site request refused', 'text/plain; charset=utf-8')
                return False
        if self.command == 'POST':
            if self.headers.get('Origin') != self.server.expected_origin:
                self.send_bytes(403, b'Bad origin', 'text/plain; charset=utf-8')
                return False
            ctype = (self.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            if ctype != 'application/json':
                self.send_bytes(415, b'JSON required', 'text/plain; charset=utf-8')
                return False
            if self.headers.get('Transfer-Encoding'):
                self.send_bytes(400, b'Chunked bodies are not supported', 'text/plain; charset=utf-8')
                return False
        return True

    def read_body(self):
        try:
            length = int(self.headers.get('Content-Length') or '')
        except ValueError:
            raise BadRequest('Content-Length required')
        if length < 2 or length > MAX_BODY:
            raise BadRequest('Request too large or empty', status=413 if length > MAX_BODY else 400)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BadRequest('Request body was cut short')
        return parse_json(raw)

    # methods -----------------------------------------------------------------
    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.gate(path):
            return
        store = self.server.store
        if path == '/api/state':
            return self.send_bytes(200, store.get_raw(), 'application/json; charset=utf-8')
        if path == '/api/status':
            return self.send_json(200, store.status())
        if path == '/favicon.ico':
            return self.send_bytes(204, b'', 'image/x-icon')
        item = self.server.static.get(path)
        if item is None:
            return self.send_bytes(404, b'Not found', 'text/plain; charset=utf-8')
        return self.send_bytes(200, item[0], item[1])

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.gate(path):
            return
        store = self.server.store
        routes = {'/api/state': store.save, '/api/restore': store.restore, '/api/heartbeat': store.heartbeat}
        handler = routes.get(path)
        if handler is None:
            return self.send_bytes(404, b'Not found', 'text/plain; charset=utf-8')
        try:
            result = handler(self.read_body())
            self.send_json(200, result)
        except BadRequest as e:
            self.api_error(e)
        except Exception as e:  # noqa: BLE001 - report, never crash the server
            say('[ERROR] %s failed: %s: %s' % (path, type(e).__name__, e))
            self.send_json(500, {'error': 'The server could not save. Check the CAPCAST window for details.'})


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != 'nt'   # Windows: SO_REUSEADDR would allow port sharing
    request_queue_size = 32

    def __init__(self, address, handler, store, static):
        self._slots = threading.BoundedSemaphore(MAX_HANDLERS)
        self.store = store
        self.static = static
        super().__init__(address, handler)
        self.expected_host = '%s:%d' % (HOST, self.server_port)
        self.expected_origin = 'http://%s:%d' % (HOST, self.server_port)

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)   # too many open connections: drop this one
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        etype = sys.exc_info()[0]
        if etype and issubclass(etype, (ConnectionError, socket.timeout, TimeoutError)):
            return
        super().handle_error(request, client_address)


def load_static():
    return {path: ((ROOT / name).read_bytes(), ctype) for path, (name, ctype) in STATIC.items()}


def python_ok(info=None):
    """Return (ok, warning_or_error_text)."""
    info = info or sys.version_info
    current = '%d.%d' % (info[0], info[1])
    if tuple(info[:2]) < MIN_PYTHON:
        return False, ('CAPCAST needs Python %d.%d or newer (this is Python %s). '
                       'Install a current Python from https://www.python.org/downloads/' % (MIN_PYTHON + (current,)))
    if tuple(info[:2]) < RECOMMENDED_PYTHON:
        return True, ('WARNING: Python %s no longer receives security updates. CAPCAST still runs, '
                      'but please install Python %d.%d or newer from https://www.python.org/downloads/'
                      % ((current,) + RECOMMENDED_PYTHON))
    return True, None


def main(argv=None):
    ok, message = python_ok()
    if not ok:
        say(message)
        return 3
    parser = argparse.ArgumentParser(description='CAPCAST local scoreboard server')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='port on 127.0.0.1 (1024-65535)')
    parser.add_argument('--no-browser', action='store_true', help='do not open the dashboard automatically')
    parser.add_argument('--data-dir', default=str(ROOT / 'data'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be 1024-65535')
    if message:
        say(message)

    store = Store(args.data_dir)
    try:
        store.load_initial()
    except (OSError, BadRequest) as e:
        say('CAPCAST could not prepare its data folder: %s' % e)
        return 4

    try:
        server = LocalServer((HOST, args.port), Handler, store, load_static())
    except OSError as e:
        busy = e.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(e, 'winerror', None) in (10048, 10013)
        if busy:
            say('Port %d is already in use. Is CAPCAST already running? Close the other window, '
                'or start with a different port, e.g.:  python3 server.py --port %d' % (args.port, args.port + 1))
            return 2
        say('CAPCAST could not start: %s' % e)
        return 2

    url = 'http://%s:%d/' % (HOST, server.server_port)
    say('')
    say('CAPCAST %s - TagPro scoreboard, running on this computer only' % VERSION)
    say('Control dashboard:  %s' % url)
    say('OBS Browser Source: %soverlay.html' % url)
    say('Keep this window open while broadcasting. Press Ctrl+C to stop.')
    say('')
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say('\nServer stopped.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
