#!/usr/bin/env python3
"""Look for a settings range table holding the 45/90 FOV slider bounds.

The deserializer's clamp loads plain 45.0f/90.0f constants. The Options slider
needs its own min/max from somewhere; if that is a data table, raising it would
give players a native path (no boot-order problem, no consumer problem).

Scans data sections for 45.0f with a 90.0f nearby and prints the surrounding
floats so a schema can be recognised.

Usage: python find_ui_range.py [--window 0x20]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DUMP = (r'C:\Users\kugua\Desktop'
        r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')


def main():
    window = 0x20
    if '--window' in sys.argv:
        window = int(sys.argv[sys.argv.index('--window') + 1], 16)
    pe = PE(DUMP)

    f45 = struct.pack('<f', 45.0)
    f90 = struct.pack('<f', 90.0)

    pairs = []
    for s in pe.sections:
        if s['rawsize'] < 0x100:
            continue
        base = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        start = 0
        while True:
            i = blob.find(f45, start)
            if i < 0:
                break
            start = i + 1
            lo = max(0, i - window)
            hi = min(len(blob), i + 4 + window)
            j = blob.find(f90, lo, hi)
            if j >= 0:
                pairs.append((base + i, base + j, j - i, s['name'] or 'sec'))

    print(f'[pairs] {len(pairs)} place(s) where 90.0f sits within +/-{window:#x} of 45.0f')
    for a, b, delta, name in pairs[:40]:
        print(f'\n  45@0x{a:#x}  90@0x{b:#x}  delta={delta} [{name}]')
        # print the surrounding floats as a candidate schema row
        off = pe.va_to_off(a - 0x20)
        if off is None:
            continue
        for k in range(0, 0x50, 4):
            raw = pe.data[off + k:off + k + 4]
            if len(raw) < 4:
                break
            f = struct.unpack('<f', raw)[0]
            iv = struct.unpack('<i', raw)[0]
            mark = ''
            tgt = a - 0x20 + k
            if tgt == a:
                mark = '  <-- 45.0f'
            elif tgt == b:
                mark = '  <-- 90.0f'
            if abs(f) < 1e6 and (f == 0 or 1e-3 < abs(f) < 1e5):
                print(f'    +{k:#04x}  {f:>14g}{mark}')
            else:
                print(f'    +{k:#04x}  (int {iv}){mark}')


if __name__ == '__main__':
    main()
