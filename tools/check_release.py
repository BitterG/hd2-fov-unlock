"""Sanity-check the shipped addon archive body before releasing.

Reads the declared resource length from the envelope header (the resource is
<u32 body_len><u32 version=2><body>) instead of guessing with a NUL-run search,
which silently truncates the body.

Usage: python tools/check_release.py [path-to-patch_0]
"""
import struct
import sys
from pathlib import Path

MARKER = b'-- HD2-Addon: '


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path('Addon/9ba626afa44a3aa3.patch_0')
    data = path.read_bytes()
    at = data.find(MARKER)
    if at < 8:
        raise SystemExit(f'{path}: no "{MARKER.decode().strip()}" declaration')
    version = struct.unpack_from('<I', data, at - 4)[0]
    body_len = struct.unpack_from('<I', data, at - 8)[0]
    body = data[at:at + body_len]
    bom = body.find(b'\xef\xbb\xbf')
    print(f'file            : {path} ({len(data)} bytes)')
    print(f'BOM offset      : {bom}  (-1 = clean)')
    print(f'marker count    : {body.count(MARKER)}  (must be 1)')
    print(f'envelope version: {version}  (must be 2)')
    print(f'body bytes      : {len(body)} (declared {body_len})')
    lines = body.split(b'\n')
    print(f'lines           : {len(lines)}')
    print(f'line 1          : {lines[0].decode()}')
    print(f'line 2          : {lines[1].decode()[:40]}')
    for n, ln in enumerate(lines, 1):
        if b'local VERSION' in ln:
            print(f'version @line {n}  : {ln.decode()}')
            break
    ok = (body.count(MARKER) == 1 and version == 2
          and len(body) == body_len and b'\xef\xbb\xbf' not in body)
    print('\nOK' if ok else '\nFAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
