"""Package the release ZIP for FOV Unlock.

The ZIP opens directly to `manifest.json` and the option folder `Addon/`, which
is the layout the managers require. Reference files (README, Source, tests,
scripts, fov_unlock.cfg) ride along without being deployed.

Usage: python scripts/package.py
"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.2.0'
OUTPUT = ROOT.parent / f'FOV-Unlock-{VERSION}.zip'
FIXED_TIME = (1980, 1, 1, 0, 0, 0)

# Windows forbids these in a filename; managers use manifest Name as a folder.
ILLEGAL_IN_NAME = set('\\/:*?"<>|')


def check_display_name(name):
    bad = sorted(ILLEGAL_IN_NAME & set(name))
    assert not bad, f'manifest Name contains illegal filename characters: {bad}'
    assert name.isascii(), 'manifest Name must be pure ASCII'


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    check_display_name(manifest['Name'])

    entry = (ROOT / 'Source/mods/hd2/fov_unlock.lua').read_text(encoding='utf-8')
    declared = entry.split("local VERSION = '", 1)[1].split("'", 1)[0]
    assert declared == VERSION, f'entry says {declared}, package.py says {VERSION}'

    includes = []
    for option in manifest.get('Options', []):
        includes.extend(option.get('Include', []))

    files = ['manifest.json', 'README.md', 'fov_unlock.cfg']
    for include in includes:
        files.extend(str(p.relative_to(ROOT)).replace('\\', '/')
                     for p in sorted((ROOT / include).rglob('*')) if p.is_file())
    for directory in ('Source', 'tests', 'scripts'):
        path = ROOT / directory
        if path.exists():
            files.extend(str(p.relative_to(ROOT)).replace('\\', '/')
                         for p in sorted(path.rglob('*'))
                         if p.is_file()
                         and p.suffix.lower() not in ('.dll', '.pyc')
                         and '__pycache__' not in p.parts)

    seen, unique = set(), []
    for name in files:
        if name not in seen and (ROOT / name).is_file():
            seen.add(name)
            unique.append(name)

    with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED) as package:
        for name in unique:
            source = ROOT / name
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, source.read_bytes())
            print(f'  + {name} ({source.stat().st_size} bytes)')

    with zipfile.ZipFile(OUTPUT) as package:
        names = package.namelist()
        assert 'manifest.json' in names, 'manifest.json must be at the ZIP root'
        assert any(n.startswith('Addon/') for n in names), 'Addon/ missing'
        assert package.testzip() is None, 'ZIP integrity check failed'
    print(f'\nwrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, {len(unique)} entries)')


if __name__ == '__main__':
    main()
