#!/usr/bin/env python3
"""Linear-disassemble a range of the decrypted game.dll image dump.

Resolves RIP-relative memory operands and shows the float/int they point at,
which is how the vertical-FOV clamp becomes readable.

Usage: python disasm.py <va> [length] [--dump PATH]
"""
import struct
import sys
from pathlib import Path

import capstone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DEFAULT_DUMP = (r'C:\Users\kugua\Desktop'
                r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')


def describe(pe, insn):
    """Return a list of annotations for RIP-relative operands."""
    notes = []
    for op in insn.operands:
        if op.type != capstone.x86.X86_OP_MEM:
            continue
        if op.mem.base != capstone.x86.X86_REG_RIP:
            continue
        tgt = insn.address + insn.size + op.mem.disp
        off = pe.va_to_off(tgt)
        if off is None or off + 4 > len(pe.data):
            notes.append(f'-> {tgt:#x}')
            continue
        raw = pe.data[off:off + 4]
        f = struct.unpack('<f', raw)[0]
        i = struct.unpack('<i', raw)[0]
        notes.append(f'-> {tgt:#x} = {f:g}f / int {i}')
    return notes


def main():
    va = int(sys.argv[1], 16)
    length = int(sys.argv[2], 16) if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else 0x100
    dump = DEFAULT_DUMP
    if '--dump' in sys.argv:
        dump = sys.argv[sys.argv.index('--dump') + 1]

    pe = PE(dump)
    off = pe.va_to_off(va)
    if off is None:
        print(f'{va:#x} not mapped')
        return
    code = pe.data[off:off + length]

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    for insn in md.disasm(code, va):
        notes = describe(pe, insn)
        hexs = ' '.join(f'{b:02x}' for b in insn.bytes)
        line = f'{insn.address:#014x}  {hexs:<32s} {insn.mnemonic:<8s} {insn.op_str}'
        if notes:
            line += '   ; ' + '  '.join(notes)
        print(line)


if __name__ == '__main__':
    main()
