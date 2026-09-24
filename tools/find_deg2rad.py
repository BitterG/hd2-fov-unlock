#!/usr/bin/env python3
"""Find who converts the FOV setting (degrees) into the camera's radians.

If the game multiplies the setting by pi/180 (0.0174532924) or its reciprocal
(57.2957795), that site is the consumer - the place stage 2's write has to
reach. Prints each site with context and flags ones that also touch +0x2C.

Usage: python find_deg2rad.py
"""
import struct
import sys
from pathlib import Path

import capstone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DUMP = (r'C:\Users\kugua\Desktop'
        r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')

FORMS = [(b'\xf3\x0f\x10', 'movss'), (b'\xf3\x0f\x59', 'mulss'),
         (b'\xf3\x0f\x5e', 'divss'), (b'\xf3\x0f\x5c', 'subss'),
         (b'\xf3\x0f\x58', 'addss'), (b'\xf3\x0f\x5d', 'minss'),
         (b'\xf3\x0f\x5f', 'maxss'), (b'\x0f\x2f', 'comiss'),
         (b'\x0f\x2e', 'ucomiss')]

TARGETS = {0.01745329238474369: 'pi/180', 57.29577951308232: '180/pi',
           0.017453292519943295: 'pi/180'}


def main():
    pe = PE(DUMP)
    print(f'[dump] image_base={pe.image_base:#x}')
    sites = []
    for s in pe.sections:
        if s['rawsize'] < 0x10000:
            continue
        base = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        for prefix, kind in FORMS:
            start = 0
            while True:
                i = blob.find(prefix, start)
                if i < 0:
                    break
                start = i + 1
                ma = i + len(prefix)
                if ma >= len(blob) or (blob[ma] & 0xC7) != 0x05:
                    continue
                disp = struct.unpack_from('<i', blob, ma + 1)[0]
                tgt = base + ma + 5 + disp
                off = pe.va_to_off(tgt)
                if off is None or off + 4 > len(pe.data):
                    continue
                f = struct.unpack_from('<f', pe.data, off)[0]
                for want, label in TARGETS.items():
                    if abs(f - want) < 1e-9:
                        sites.append((base + i, kind, label, tgt))
                        break

    print(f'[sites] {len(sites)} instruction(s) referencing pi/180 or 180/pi')
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    by_label = {}
    for va, kind, label, tgt in sites:
        by_label.setdefault(label, []).append((va, kind))
    for label, items in by_label.items():
        print(f'  {label}: {len(items)}')

    # Which of them also touch +0x2C nearby? That is the interesting subset.
    print('\n=== sites that also touch [+reg+0x2c] within +/-0x80 ===')
    interesting = 0
    for va, kind, label, tgt in sites:
        lo = pe.va_to_off(va - 0x80)
        hi = pe.va_to_off(va + 0x80)
        if lo is None or hi is None:
            continue
        window = pe.data[lo:hi]
        has_2c = False
        for prefix, _k in FORMS:
            st = 0
            while True:
                j = window.find(prefix, st)
                if j < 0:
                    break
                st = j + 1
                m = j + len(prefix)
                if m >= len(window):
                    continue
                modrm = window[m]
                mod = modrm >> 6
                if mod == 1 and (modrm & 7) not in (4, 5):
                    d = struct.unpack_from('<b', window, m + 1)[0]
                    if d == 0x2C:
                        has_2c = True
                elif mod == 2 and (modrm & 7) not in (4, 5) and m + 5 <= len(window):
                    d = struct.unpack_from('<i', window, m + 1)[0]
                    if d == 0x2C:
                        has_2c = True
        if has_2c:
            interesting += 1
            print(f'  {va:#014x} {kind:8s} {label}')
    if not interesting:
        print('  (none)')


if __name__ == '__main__':
    main()
