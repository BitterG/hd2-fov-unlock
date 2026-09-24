#!/usr/bin/env python3
"""Search binaries/bundles for ASCII keywords with context windows.

Usage: python strscan.py <file> <keyword> [keyword...] [--ctx N] [--max M]
"""
import sys, re

def main():
    args = sys.argv[1:]
    ctx = 48
    maxhits = 40
    positional = []
    i = 0
    while i < len(args):
        if args[i] == '--ctx':
            ctx = int(args[i+1]); i += 2
        elif args[i] == '--max':
            maxhits = int(args[i+1]); i += 2
        else:
            positional.append(args[i]); i += 1
    path = positional[0]
    kws = [k.encode('ascii') for k in positional[1:]]
    with open(path, 'rb') as f:
        data = f.read()
    print(f"[file] {path}  {len(data)} bytes")
    for kw in kws:
        hits = [m.start() for m in re.finditer(re.escape(kw), data)]
        print(f"\n=== {kw.decode()} : {len(hits)} hits ===")
        for h in hits[:maxhits]:
            lo = max(0, h - ctx)
            hi = min(len(data), h + len(kw) + ctx)
            chunk = data[lo:hi]
            txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)
            print(f"  @0x{h:08X}  {txt}")
        if len(hits) > maxhits:
            print(f"  ... {len(hits)-maxhits} more")

if __name__ == '__main__':
    main()
