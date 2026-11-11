"""Shared test fixtures: image byte builders and v1.1-format states. Standard library only."""
import base64
import copy
import json
import os
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def png_bytes(w, h, noise=False, color_type=6):
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    bpp = {6: 4, 2: 3, 0: 1}[color_type]
    if noise:
        raw = b''.join(b'\x00' + os.urandom(w * bpp) for _ in range(h))
    else:
        raw = b''.join(b'\x00' + bytes([40, 120, 200, 255][:bpp]) * w for _ in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, color_type, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def png_bomb(w, h):
    """1-bit grayscale, all zero: tiny file, huge decoded size."""
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    rowlen = (w + 7) // 8
    co = zlib.compressobj(9)
    data = b''.join(co.compress(b'\x00' * (rowlen + 1)) for _ in range(h)) + co.flush()
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 1, 0, 0, 0, 0))
            + chunk(b'IDAT', data) + chunk(b'IEND', b''))


def jpeg_header(w, h, exif=False):
    """A syntactically plausible JPEG prefix (APP0/APP1 + SOF0) for header parsing tests."""
    out = b'\xff\xd8'
    out += b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    if exif:
        payload = b'Exif\x00\x00' + b'GPS-LAT-42.4' * 20
        out += b'\xff\xe1' + struct.pack('>H', len(payload) + 2) + payload
    out += b'\xff\xc0' + struct.pack('>HBHHB', 17, 8, h, w, 3) + b'\x01\x22\x00\x02\x11\x01\x03\x11\x01'
    out += b'\xff\xda' + struct.pack('>H', 12) + b'\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00' + b'\x00' * 32 + b'\xff\xd9'
    return out


def webp_vp8x(w, h):
    body = b'VP8X' + struct.pack('<I', 10) + b'\x00\x00\x00\x00' + (w - 1).to_bytes(3, 'little') + (h - 1).to_bytes(3, 'little')
    return b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WEBP' + body


def webp_vp8l(w, h):
    bits = (w - 1) | ((h - 1) << 14)
    payload = b'\x2f' + bits.to_bytes(4, 'little') + b'\x00' * 8
    body = b'VP8L' + struct.pack('<I', len(payload)) + payload
    return b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WEBP' + body


def webp_vp8(w, h):
    payload = b'\x00\x00\x00' + b'\x9d\x01\x2a' + struct.pack('<HH', w, h) + b'\x00' * 10
    body = b'VP8 ' + struct.pack('<I', len(payload)) + payload
    return b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WEBP' + body


def data_url(kind, data):
    return 'data:image/%s;base64,%s' % (kind, base64.b64encode(data).decode('ascii'))


def v11_state():
    """The exact data/state.json shipped in the v1.1 ZIP (schema 1)."""
    return json.loads((ROOT / 'tests' / 'data' / 'v1.1-state.json').read_text(encoding='utf-8'))


def v2_state():
    return json.loads((ROOT / 'starter-state.json').read_text(encoding='utf-8'))


def mutate(base, fn):
    d = copy.deepcopy(base)
    fn(d)
    return d
