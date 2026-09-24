#!/usr/bin/env python3
"""Locate UTF-16 string literals in a PE and find RIP-relative code references.

HD2's game.dll is a normal MSVC x64 image: no packer, but all string literals
are UTF-16LE, which is why plain ASCII searches find nothing.

Usage:
  python pe_scan.py <pe_file> find <utf8_substring> ...
  python pe_scan.py <pe_file> xref <hex_va> [window]
  python pe_scan.py <pe_file> around <hex_va> [before] [after]
"""
import struct
import sys


class PE:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        d = self.data
        e_lfanew = struct.unpack_from('<I', d, 0x3C)[0]
        assert d[e_lfanew:e_lfanew + 4] == b'PE\0\0', 'not a PE'
        coff = e_lfanew + 4
        nsec = struct.unpack_from('<H', d, coff + 2)[0]
        opt_size = struct.unpack_from('<H', d, coff + 16)[0]
        opt = coff + 20
        magic = struct.unpack_from('<H', d, opt)[0]
        assert magic == 0x20B, f'not PE32+ (magic {magic:#x})'
        self.image_base = struct.unpack_from('<Q', d, opt + 24)[0]
        self.sections = []
        sec = opt + opt_size
        for i in range(nsec):
            o = sec + i * 40
            name = d[o:o + 8].rstrip(b'\0').decode('latin1')
            vsize, vaddr, rawsize, rawptr = struct.unpack_from('<IIII', d, o + 8)
            chars = struct.unpack_from('<I', d, o + 36)[0]
            self.sections.append(dict(name=name, va=vaddr, vsize=vsize,
                                      raw=rawptr, rawsize=rawsize, chars=chars))

    def va_to_off(self, va):
        rva = va - self.image_base
        for s in self.sections:
            if s['va'] <= rva < s['va'] + max(s['vsize'], s['rawsize']):
                off = s['raw'] + (rva - s['va'])
                if off < len(self.data):
                    return off
        return None

    def off_to_va(self, off):
        for s in self.sections:
            if s['raw'] <= off < s['raw'] + s['rawsize']:
                return self.image_base + s['va'] + (off - s['raw'])
        return None

    def section_of(self, va):
        rva = va - self.image_base
        for s in self.sections:
            if s['va'] <= rva < s['va'] + max(s['vsize'], s['rawsize']):
                return s['name']
        return '?'

    def dump(self):
        print(f'[pe] {self.path}  {len(self.data)} bytes  image_base={self.image_base:#x}')
        for s in self.sections:
            print(f"  {s['name']:8s} va={self.image_base + s['va']:#012x} "
                  f"vsize={s['vsize']:#x} rawoff={s['raw']:#x} rawsize={s['rawsize']:#x}")


def utf16_find(pe, text):
    """Return (va, off) for every UTF-16LE occurrence of text."""
    needle = text.encode('utf-16-le')
    out = []
    start = 0
    while True:
        at = pe.data.find(needle, start)
        if at < 0:
            break
        va = pe.off_to_va(at)
        if va is not None:
            out.append((va, at))
        start = at + 1
    return out


def xrefs(pe, target_va):
    """Find every offset holding a disp32 that resolves to target_va.

    RIP-relative disp32 is always the last 4 bytes of its instruction, so
    scanning every 4-byte window and adding the window end address catches
    both LEA and MOV forms without a disassembler. False positives on a
    16-byte string VA are effectively nil.
    """
    hits = []
    for s in pe.sections:
        if s['rawsize'] < 0x1000:
            continue
        base_va = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        for i in range(len(blob) - 4):
            disp = struct.unpack_from('<i', blob, i)[0]
            # instruction virtual address just past the disp32
            end_va = base_va + i + 4
            if end_va + disp == target_va:
                hits.append((base_va + i, s['name'] or f"sec@{s['va']:x}"))
    return hits


def around(pe, va, before, after):
    off = pe.va_to_off(va)
    if off is None:
        print(f'  {va:#x}: not mapped')
        return
    lo = max(0, off - before)
    hi = min(len(pe.data), off + after)
    blob = pe.data[lo:hi]
    base_va = va - (off - lo)
    for row in range(0, len(blob), 16):
        chunk = blob[row:row + 16]
        hexs = ' '.join(f'{b:02x}' for b in chunk)
        text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        mark = '<<<' if base_va + row <= va < base_va + row + 16 else ''
        print(f'  {base_va + row:#012x}  {hexs:<48s} {text} {mark}')


def main():
    pe = PE(sys.argv[1])
    cmd = sys.argv[2]
    pe.dump()
    if cmd == 'find':
        for text in sys.argv[3:]:
            hits = utf16_find(pe, text)
            print(f'\n=== UTF-16 "{text}": {len(hits)} hit(s) ===')
            for va, off in hits[:20]:
                print(f'  va={va:#012x} off={off:#x} section={pe.section_of(va)}')
    elif cmd == 'xref':
        target = int(sys.argv[3], 16)
        hits = xrefs(pe, target)
        print(f'\n=== xrefs to {target:#x}: {len(hits)} ===')
        for va, name in hits[:40]:
            print(f'  va={va:#012x} section={name}')
    elif cmd == 'around':
        target = int(sys.argv[3], 16)
        before = int(sys.argv[4], 16) if len(sys.argv) > 4 else 0x80
        after = int(sys.argv[5], 16) if len(sys.argv) > 5 else 0x100
        print(f'\n=== around {target:#x} ===')
        around(pe, target, before, after)


if __name__ == '__main__':
    main()
