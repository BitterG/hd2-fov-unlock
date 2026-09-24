import sys
from pathlib import Path
d = Path('FOV-Unlock/Addon/9ba626afa44a3aa3.patch_0').read_bytes()
i = d.find(b'-- HD2-Addon:')
body = d[i:]
e = body.find(b'\x00\x00\x00\x00')
if e > 0:
    body = body[:e]
print('BOM offset      :', body.find(b'\xef\xbb\xbf'))
print('marker count    :', body.count(b'-- HD2-Addon: '))
print('body bytes      :', len(body))
lines = body.split(b'\n')
print('line 1          :', lines[0].decode())
print('line 2          :', lines[1].decode()[:40])
for n, ln in enumerate(lines, 1):
    if b'local VERSION' in ln:
        print(f'version @line {n} :', ln.decode())
        break
