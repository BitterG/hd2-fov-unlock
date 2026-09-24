#!/usr/bin/env python3
"""Map settings keys to struct offsets by reading the deserializer.

In the deserializer the pattern for every setting is:

    movss xmm2, dword ptr [rbx+0x2c]   ; default loaded from the field
    lea   rdx, "vertical_fov"          ; key name
    ...
    call  qword ptr [r8+0x88]          ; read value from the config backend
    ...
    movss dword ptr [rbx+0x2c], xmm1   ; result stored back to the field

so the field offset for a key is the load just before the `lea rdx, [key]`.

Usage: python map_fields.py [start_hex] [length_hex]
"""
import re
import struct
import sys
from pathlib import Path

import capstone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DEFAULT_DUMP = (r'C:\Users\kugua\Desktop'
                r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')

KEY_RE = re.compile(r'^[a-z][a-z0-9_]{2,40}$')


def load_keys(pe):
    """Collect every plausible settings key string and its VA."""
    keys = {}
    for s in pe.sections:
        if s['rawsize'] < 0x1000:
            continue
        base = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        for m in re.finditer(rb'[a-z][a-z0-9_]{2,40}\x00', blob):
            txt = m.group()[:-1].decode('latin1')
            if KEY_RE.match(txt) and ('_' in txt or len(txt) > 6):
                keys[base + m.start()] = txt
    return keys


def main():
    start = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x7ff95b71c400
    length = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x2000
    pe = PE(DEFAULT_DUMP)
    keys = load_keys(pe)
    print(f'[keys] {len(keys)} candidate key strings')

    off = pe.va_to_off(start)
    blob = pe.data[off:off + length]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    insns = list(md.disasm(blob, start))

    def is_struct_load(insn):
        for op in insn.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.base == capstone.x86.X86_REG_RBX:
                if insn.mnemonic.startswith('mov') or insn.mnemonic == 'movzx':
                    # must be writing a register from memory
                    if insn.operands[0].type == capstone.x86.X86_OP_REG:
                        return op.mem.disp
        return None

    def is_struct_store(insn):
        for op in insn.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.base == capstone.x86.X86_REG_RBX:
                if insn.mnemonic.startswith('mov'):
                    if insn.operands[0].type == capstone.x86.X86_OP_MEM:
                        return op.mem.disp
        return None

    rows = []
    for idx, insn in enumerate(insns):
        if insn.mnemonic != 'lea' or len(insn.operands) != 2:
            continue
        dst, src = insn.operands
        if dst.type != capstone.x86.X86_OP_REG or dst.reg != capstone.x86.X86_REG_RDX:
            continue
        if src.type != capstone.x86.X86_OP_MEM or src.mem.base != capstone.x86.X86_REG_RIP:
            continue
        tgt = insn.address + insn.size + src.mem.disp
        key = keys.get(tgt)
        if not key:
            continue
        back = None
        for j in range(idx - 1, max(-1, idx - 16), -1):
            d = is_struct_load(insns[j])
            if d is not None:
                back = d
                break
        fwd = None
        for j in range(idx + 1, min(len(insns), idx + 26)):
            d = is_struct_store(insns[j])
            if d is not None:
                fwd = d
                break
        rows.append((key, back, fwd, insn.address))

    print(f'[map] {len(rows)} key sites')
    print(f'{"key":38s} {"default_from":>13s} {"stored_to":>10s}')
    for key, back, fwd, va in rows:
        b = f'{back:#x}' if back is not None else '-'
        f = f'{fwd:#x}' if fwd is not None else '-'
        print(f'{key:38s} {b:>13s} {f:>10s}   {va:#x}')


if __name__ == '__main__':
    main()
