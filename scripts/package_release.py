"""Package source, built frontend, licences, fixtures and docs; omit local environments."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT.parent/'GridGuard.zip'
excluded={'.venv','node_modules','__pycache__','.pytest_cache','.git'}
assert (ROOT/'frontend/dist/index.html').is_file(), 'Run npm run build first'
with zipfile.ZipFile(OUTPUT,'w',zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(ROOT.rglob('*')):
        relative=file.relative_to(ROOT)
        if file.is_symlink() or excluded.intersection(relative.parts): continue
        if file.is_file(): archive.write(file,Path('gridguard')/relative)
with zipfile.ZipFile(OUTPUT) as archive:
    assert archive.testzip() is None
    assert 'gridguard/frontend/dist/index.html' in archive.namelist()
    print(f'Packaged {len(archive.namelist())} files: {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)')
