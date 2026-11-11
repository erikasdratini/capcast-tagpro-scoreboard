#!/usr/bin/env python3
"""Build the CAPCAST release ZIP, a source ZIP and SHA256SUMS.txt (standard library only).

    python3 tools/build_release.py            # writes dist/

The release ZIP is reproducible: fixed timestamps, sorted entries, explicit file
modes and no compression (deflate output differs between zlib builds such as
zlib-ng), so anyone can rebuild it from the tagged source and compare hashes.
Build from a checkout with LF line endings (Linux, macOS, or Windows with
core.autocrlf=false); the build refuses launchers with the wrong line endings.
"""
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
VERSION = '1.1.1'
FOLDER = 'CAPCAST_TagPro_Scoreboard'
RELEASE_NAME = 'CAPCAST_TagPro_OBS_Scoreboard_Community_v%s.zip' % VERSION
SOURCE_NAME = 'capcast-tagpro-scoreboard-v%s-source.zip' % VERSION
STAMP = (2026, 11, 11, 0, 0, 0)
RELEASE_FILES = [
    'LICENSE', 'README.txt', 'SHARING_AND_SECURITY.txt', 'SECURITY.md', 'CHANGELOG.md',
    'server.py', 'shared.js', 'app.js', 'overlay.js', 'index.html', 'overlay.html', 'styles.css',
    'starter-state.json', 'start-windows.bat', 'start-linux.sh', 'start-mac.command',
]
EXECUTABLE = {'start-linux.sh', 'start-mac.command', 'tools/build_release.py'}
SKIP_TOP = {'.git', 'dist', 'build', 'data', 'node_modules'}   # only at the top level: tests/data/ is needed


def info(name, mode):
    zi = zipfile.ZipInfo(name, STAMP)
    zi.create_system = 3
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = (mode & 0xFFFF) << 16
    if name.endswith('/'):
        zi.external_attr |= 0x10
    return zi


def write_zip(path, entries, top=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        if top:
            z.writestr(info(top + '/', 0o40755), b'')
        for rel in sorted(entries):
            data = (ROOT / rel).read_bytes()
            mode = 0o100755 if rel in EXECUTABLE else 0o100644
            z.writestr(info('%s/%s' % (top, rel) if top else rel, mode), data)


def source_files():
    try:
        out = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        files = [f for f in out.splitlines() if f]
    except (OSError, subprocess.CalledProcessError):
        files = [str(p.relative_to(ROOT)).replace('\\', '/') for p in ROOT.rglob('*') if p.is_file()]
    def wanted(f):
        parts = Path(f).parts
        return parts[0] not in SKIP_TOP and '__pycache__' not in parts and not f.endswith('.pyc') and (ROOT / f).is_file()
    return sorted(f for f in files if wanted(f))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_tree():
    problems = []
    server = (ROOT / 'server.py').read_text(encoding='utf-8')
    if "VERSION = '%s'" % VERSION not in server:
        problems.append('server.py VERSION is not %s' % VERSION)
    if 'V%s' % VERSION not in (ROOT / 'index.html').read_text(encoding='utf-8'):
        problems.append('index.html does not show V%s' % VERSION)
    if not re.search(r'^## %s\b' % re.escape(VERSION), (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), re.M):
        problems.append('CHANGELOG.md has no %s entry' % VERSION)
    bat = (ROOT / 'start-windows.bat').read_bytes()
    if b'\n' in bat.replace(b'\r\n', b''):
        problems.append('start-windows.bat must use CRLF line endings')
    for sh in ('start-linux.sh', 'start-mac.command'):
        if b'\r' in (ROOT / sh).read_bytes():
            problems.append('%s must use LF line endings' % sh)
    for f in RELEASE_FILES:
        if not (ROOT / f).is_file():
            problems.append('missing ' + f)
    return problems


def verify_release(path):
    problems = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        expected = {FOLDER + '/'} | {'%s/%s' % (FOLDER, f) for f in RELEASE_FILES}
        if set(names) != expected:
            problems.append('unexpected entries: %s' % sorted(set(names) ^ expected))
        for zi in z.infolist():
            mode = zi.external_attr >> 16
            rel = zi.filename[len(FOLDER) + 1:]
            if rel in EXECUTABLE and mode & 0o111 != 0o111:
                problems.append('%s is not executable' % zi.filename)
            if '..' in zi.filename.split('/') or zi.filename.startswith('/'):
                problems.append('unsafe path ' + zi.filename)
        if any('/data/' in n or n.endswith('state.json') and 'starter' not in n for n in names):
            problems.append('release must not contain data/state.json')
    return problems


def main():
    problems = check_tree()
    if problems:
        print('Refusing to build:\n  ' + '\n  '.join(problems))
        return 1
    DIST.mkdir(exist_ok=True)
    release = DIST / RELEASE_NAME
    source = DIST / SOURCE_NAME
    write_zip(release, RELEASE_FILES, FOLDER)
    write_zip(source, source_files(), 'capcast-tagpro-scoreboard-v%s' % VERSION)
    problems = verify_release(release)
    if problems:
        print('Release ZIP failed verification:\n  ' + '\n  '.join(problems))
        return 1
    sums = '%s  %s\n%s  %s\n' % (sha256(release), RELEASE_NAME, sha256(source), SOURCE_NAME)
    (DIST / 'SHA256SUMS.txt').write_text(sums, encoding='ascii')
    print(sums, end='')
    print('Built %s (%d bytes) and %s (%d bytes)' % (release.name, release.stat().st_size, source.name, source.stat().st_size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
