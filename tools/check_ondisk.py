#!/usr/bin/env python3
"""Can the *on-disk* game.dll be validated against, despite WinLicense?

`.rdata` string literals are absent from the file, so the constant pool is
encrypted/compressed. But the .text instruction *shapes* we ship as signatures
need no constants:

    clamp:   F3 0F 10 0D ?? ?? ?? ?? 0F 2F C8 77 0C F3 0F 10 0D ?? ?? ?? ?? F3 0F 5D C8
    chain:   48 8B 0D ?? ?? ?? ?? 48 81 C1 ?? ?? ?? ??

If those appear in the on-disk file, the code is intact and we can verify the
current build offline. If the section is high-entropy, it is encrypted and only
a memory dump can help.

Usage: python check_ondisk.py <game.dll>
"""
import math
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

# dist32 wildcards
CLAMP_SHAPE = (bytes([0xF3, 0x0F, 0x10, 0x0D]), 4, bytes([0x0F, 0x2F, 0xC8]), 0,
               bytes([0x77, 0x0C]), 0, bytes([0xF3, 0x0F, 0x10, 0x0D]), 4,
               bytes([0xF3, 0x0F, 0x5D, 0xC8]))
CHAIN_PREFIX = bytes([0x48, 0x8B, 0x0D])
CHAIN_MID = bytes([0x48, 0x81, 0xC1])


def entropy(blob):
    if not blob:
        return 0.0
    counts = Counter(blob)
    n = len(blob)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def match_shape(blob, i):
    pos = i
    for item in CLAMP_SHAPE:
        if isinstance(item, int):
            pos += item
            continue
        if blob[pos:pos + len(item)] != item:
            return False
        pos += len(item)
    return True


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        r'E:\SteamLibrary\steamapps\common\Helldivers 2\data\game\game.dll'
    pe = PE(path)
    print(f'[file] {path}  {len(pe.data)} bytes')
    for s in pe.sections:
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']] if s['rawsize'] else b''
        name = s['name'] or '(unnamed)'
        print(f"  {name:10s} va={s['va']:#x} vsize={s['vsize']:#x} raw={s['rawsize']:#x} "
              f"chars={s['chars']:#010x} entropy={entropy(blob[:1 << 20]):.3f}")

    total_clamp = 0
    total_chain = 0
    for s in pe.sections:
        if not s['rawsize']:
            continue
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        name = s['name'] or '(unnamed)'
        # clamp shape
        start = 0
        hits = 0
        while True:
            i = blob.find(CLAMP_SHAPE[0], start)
            if i < 0:
                break
            start = i + 1
            if i + 30 <= len(blob) and match_shape(blob, i):
                hits += 1
        # chain shape
        chits = 0
        start = 0
        while True:
            i = blob.find(CHAIN_PREFIX, start)
            if i < 0:
                break
            start = i + 1
            if i + 13 <= len(blob) and blob[i + 7:i + 10] == CHAIN_MID:
                chits += 1
        if hits or chits:
            print(f'  {name}: clamp_shape={hits} chain_shape={chits}')
        total_clamp += hits
        total_chain += chits

    print(f'\nTOTAL clamp_shape={total_clamp}  chain_shape={total_chain}')
    if total_clamp:
        print('=> on-disk .text is readable and the shipped clamp shape IS present')
    else:
        print('=> clamp shape absent: either the build changed or .text is encrypted')


if __name__ == '__main__':
    main()
