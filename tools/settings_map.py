#!/usr/bin/env python3
"""Helpers over the decrypted game.dll dump: string tables, function starts,
call xrefs, and field-map hunting.

Usage:
  python settings_map.py strings <va> <len>
  python settings_map.py funcstart <va>
  python settings_map.py callers <va>
  python settings_map.py floatmaps <va> [span]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DEFAULT_DUMP = (r'C:\Users\kugua\Desktop'
                r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')


def print_strings(pe, va, length):
    off = pe.va_to_off(va)
    blob = pe.data[off:off + length]
    cur = bytearray()
    cur_va = va
    for i, b in enumerate(blob):
        if 0x20 <= b < 0x7F:
            if not cur:
                cur_va = va + i
            cur.append(b)
        else:
            if len(cur) >= 3:
                print(f'  {cur_va:#014x}  {cur.decode("latin1")}')
            cur = bytearray()
    if len(cur) >= 3:
        print(f'  {cur_va:#014x}  {cur.decode("latin1")}')


def func_start(pe, va):
    """Walk back to the first byte after a run of int3 alignment padding."""
    off = pe.va_to_off(va)
    # find the section the address lives in
    sec = None
    for s in pe.sections:
        rva = va - pe.image_base
        if s['va'] <= rva < s['va'] + max(s['vsize'], s['rawsize']):
            sec = s
            break
    start_off = sec['raw']
    i = off
    while i > start_off + 4:
        if pe.data[i - 1] == 0xCC and pe.data[i - 2] == 0xCC:
            # skip the whole padding run
            j = i
            while j > start_off and pe.data[j - 1] == 0xCC:
                j -= 1
            return pe.off_to_va(j)
        i -= 1
    return None


def callers(pe, target_va):
    hits = []
    for s in pe.sections:
        if s['rawsize'] < 0x1000:
            continue
        base_va = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        i = 0
        while True:
            i = blob.find(b'\xe8', i)
            if i < 0 or i + 5 > len(blob):
                break
            rel = struct.unpack_from('<i', blob, i + 1)[0]
            if base_va + i + 5 + rel == target_va:
                hits.append(base_va + i)
            i += 1
    return hits


def main():
    pe = PE(DEFAULT_DUMP)
    cmd = sys.argv[1]
    va = int(sys.argv[2], 16)
    if cmd == 'strings':
        length = int(sys.argv[3], 16)
        print(f'=== strings at {va:#x} ({length:#x} bytes) ===')
        print_strings(pe, va, length)
    elif cmd == 'funcstart':
        fs = func_start(pe, va)
        print(f'func start before {va:#x} = {fs:#x} (rva {fs - pe.image_base:#x})')
        if fs:
            print('callers:', [hex(c) for c in callers(pe, fs)])
    elif cmd == 'callers':
        print('callers:', [hex(c) for c in callers(pe, va)])
    elif cmd == 'floatmaps':
        # Show every RIP-relative float constant load in a span, with the struct
        # field offset it is stored to (movss [reg+off], xmm) - a field map.
        span = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x400
        off = pe.va_to_off(va)
        blob = pe.data[off:off + span]
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        md.detail = True
        for insn in md.disasm(blob, va):
            if insn.mnemonic == 'movss' and len(insn.operands) == 2:
                dst, src = insn.operands
                if dst.type == capstone.x86.X86_OP_MEM and src.type == capstone.x86.X86_OP_REG:
                    print(f'  {insn.address:#014x}  store [reg+{dst.mem.disp:#x}] <- xmm{src.reg - capstone.x86.X86_REG_XMM0}')
                elif dst.type == capstone.x86.X86_OP_REG and src.type == capstone.x86.X86_OP_MEM:
                    tgt = insn.address + insn.size + src.mem.disp
                    o2 = pe.va_to_off(tgt)
                    f = struct.unpack_from('<f', pe.data, o2)[0] if o2 else float('nan')
                    print(f'  {insn.address:#014x}  load  xmm{dst.reg - capstone.x86.X86_REG_XMM0} <- [rip {tgt:#x}] = {f:g}f')
            elif insn.mnemonic == 'lea' and len(insn.operands) == 2:
                dst, src = insn.operands
                if dst.type == capstone.x86.X86_OP_REG and src.type == capstone.x86.X86_OP_MEM and src.mem.base == capstone.x86.X86_REG_RIP:
                    tgt = insn.address + insn.size + src.mem.disp
                    o2 = pe.va_to_off(tgt)
                    s = ''
                    if o2:
                        raw = pe.data[o2:o2 + 48].split(b'\x00')[0]
                        try:
                            s = raw.decode('ascii')
                        except UnicodeDecodeError:
                            s = ''
                    if s and s.isprintable():
                        print(f'  {insn.address:#014x}  key   rdx -> "{s}"')


if __name__ == '__main__':
    main()
