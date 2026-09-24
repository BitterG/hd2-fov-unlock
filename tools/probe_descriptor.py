#!/usr/bin/env python3
"""Is the 45.0f/90.0f descriptor at .data the vertical_fov setting schema?

Looks for pointers to the "vertical_fov" string near the candidate descriptor
and prints the surrounding bytes as candidate struct fields.

Usage: python probe_descriptor.py [va_hex]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DUMP = (r'C:\Users\kugua\Desktop'
        r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')
CANDIDATE = 0x7FF95C45EE74      # the 45.0f whose neighbour is 90.0f


def main():
    target = int(sys.argv[1], 16) if len(sys.argv) > 1 else CANDIDATE
    pe = PE(DUMP)
    base = pe.image_base

    # every ASCII "vertical_fov" in the image, and pointers to each
    strings = []
    for s in pe.sections:
        if s['rawsize'] < 0x100:
            continue
        b = base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        start = 0
        while True:
            i = blob.find(b'vertical_fov\x00', start)
            if i < 0:
                break
            start = i + 1
            strings.append(b + i)
    print(f'[strings] "vertical_fov" at: {[hex(x) for x in strings]}')

    for sv in strings:
        needle = struct.pack('<Q', sv)
        hits = []
        for s in pe.sections:
            if s['rawsize'] < 0x100:
                continue
            b = base + s['va']
            blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
            start = 0
            while True:
                i = blob.find(needle, start)
                if i < 0:
                    break
                start = i + 1
                hits.append(b + i)
        print(f'[ptr] pointers to {sv:#x}: {len(hits)}')
        for h in hits[:12]:
            near = abs(h - target)
            tag = '   <== NEAR THE 45/90 DESCRIPTOR' if near < 0x2000 else ''
            print(f'   {h:#x} (rva {h - base:#x}) delta={near:#x}{tag}')

    print(f'\n[descriptor] around {target:#x}')
    off = pe.va_to_off(target - 0x40)
    for k in range(0, 0xC0, 8):
        raw = pe.data[off + k:off + k + 8]
        f1 = struct.unpack_from('<f', raw, 0)[0]
        f2 = struct.unpack_from('<f', raw, 4)[0]
        q = struct.unpack_from('<Q', raw, 0)[0]
        print(f'  {target - 0x40 + k:#x}  q={q:#018x}  f32=[{f1:g}, {f2:g}]')


if __name__ == '__main__':
    main()
