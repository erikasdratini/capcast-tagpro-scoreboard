"""R-12: release packaging checks. Builds dist/ with tools/build_release.py, then
unpacks the release ZIP with the system 'unzip' (which keeps file modes, like
macOS Archive Utility and most Linux tools) and starts it with the launcher.

Set CAPCAST_OLD_PYTHON to a Python 3.8 interpreter to check the version message.
"""
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
RELEASE = DIST / 'CAPCAST_TagPro_OBS_Scoreboard_Community_v1.1.1.zip'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestR12Distribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'tools' / 'build_release.py')], capture_output=True, text=True)
        if out.returncode:
            raise RuntimeError(out.stdout + out.stderr)
        cls.tmp = Path(tempfile.mkdtemp(prefix='capcast dist & \'quotes\' '))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_checksum_matches(self):
        sums = dict(reversed(line.split('  ', 1)) for line in (DIST / 'SHA256SUMS.txt').read_text().splitlines())
        self.assertEqual(sums[RELEASE.name], hashlib.sha256(RELEASE.read_bytes()).hexdigest())

    def test_rebuild_is_identical(self):
        # Stored (uncompressed) entries make the hash independent of the zlib build (zlib vs zlib-ng).
        first = hashlib.sha256(RELEASE.read_bytes()).hexdigest()
        with zipfile.ZipFile(RELEASE) as z:
            self.assertEqual({i.compress_type for i in z.infolist()}, {zipfile.ZIP_STORED})
        out = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'tools' / 'build_release.py')], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(hashlib.sha256(RELEASE.read_bytes()).hexdigest(), first)

    def test_source_package_is_complete(self):
        source = DIST / 'capcast-tagpro-scoreboard-v1.1.1-source.zip'
        with zipfile.ZipFile(source) as z:
            names = {n.split('/', 1)[1] for n in z.namelist() if '/' in n}
        for needed in ('tests/data/v1.1-state.json', 'tests/fixtures.py', 'starter-state.json', 'tools/build_release.py',
                       '.gitattributes', '.gitignore', 'LICENSE'):
            self.assertIn(needed, names)
        self.assertFalse([n for n in names if n.startswith('data/') or '__pycache__' in n or n.startswith('dist/')])

    def test_contents(self):
        with zipfile.ZipFile(RELEASE) as z:
            names = z.namelist()
            self.assertIn('CAPCAST_TagPro_Scoreboard/LICENSE', names)
            self.assertIn('MIT License', z.read('CAPCAST_TagPro_Scoreboard/LICENSE').decode())
            for doc in ('README.txt', 'SHARING_AND_SECURITY.txt', 'SECURITY.md', 'CHANGELOG.md', 'starter-state.json'):
                self.assertIn('CAPCAST_TagPro_Scoreboard/' + doc, names)
            self.assertFalse([n for n in names if '/data/' in n or n.endswith('/state.json')])
            self.assertFalse([n for n in names if 'tests/' in n or '__pycache__' in n or '.git' in n])
            bat = z.read('CAPCAST_TagPro_Scoreboard/start-windows.bat')
            self.assertNotIn(b'\n', bat.replace(b'\r\n', b''))
            self.assertIn(b'-I server.py', bat)
            for sh in ('start-linux.sh', 'start-mac.command'):
                self.assertNotIn(b'\r', z.read('CAPCAST_TagPro_Scoreboard/' + sh))
                mode = z.getinfo('CAPCAST_TagPro_Scoreboard/' + sh).external_attr >> 16
                self.assertEqual(mode & 0o777, 0o755)

    @unittest.skipUnless(shutil.which('unzip'), 'unzip not installed')
    def test_unzip_and_start_with_launcher(self):
        dest = self.tmp / 'extract'
        dest.mkdir()
        subprocess.run(['unzip', '-q', str(RELEASE), '-d', str(dest)], check=True)
        app = dest / 'CAPCAST_TagPro_Scoreboard'
        self.assertTrue(os.access(app / 'start-linux.sh', os.X_OK))
        port = free_port()
        env = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get('PATH', ''))
        proc = subprocess.Popen([str(app / 'start-linux.sh'), '--port', str(port), '--no-browser'], cwd='/', env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            body = None
            for _ in range(60):
                try:
                    req = urllib.request.Request('http://127.0.0.1:%d/api/status' % port)
                    body = urllib.request.urlopen(req, timeout=1).read()
                    break
                except OSError:
                    time.sleep(0.2)
            self.assertIsNotNone(body)
            self.assertIn(b'"version": "1.1.1"', body)
            self.assertTrue((app / 'data' / 'state.json').exists())
            self.assertFalse(list(app.rglob('__pycache__')))      # -I -B style run leaves no bytecode in the app folder
        finally:
            proc.terminate()
            proc.wait(5)

    @unittest.skipUnless(os.environ.get('CAPCAST_OLD_PYTHON'), 'set CAPCAST_OLD_PYTHON to a Python 3.8 interpreter')
    def test_old_python_gets_clear_message(self):
        p = subprocess.run([os.environ['CAPCAST_OLD_PYTHON'], '-I', str(ROOT / 'server.py'), '--no-browser'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn('CAPCAST needs Python 3.9 or newer', p.stdout)
        self.assertNotIn('Traceback', p.stdout + p.stderr)


if __name__ == '__main__':
    unittest.main()
