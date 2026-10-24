"""Build and serve the UI and Flask API together; no separate Vite proxy required."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5001)
    parser.add_argument('--skip-build', action='store_true', help='Serve an existing frontend/dist build')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if not args.skip_build:
        npm = shutil.which('npm')
        if npm is None:
            parser.error('Node/npm is required to build. Install it, or use --skip-build with a release build.')
        if not (ROOT/'frontend/node_modules').is_dir():
            parser.error('Install frontend dependencies first: cd frontend && npm ci')
        result = subprocess.run([npm, 'run', 'build'], cwd=ROOT/'frontend')
        if result.returncode:
            raise SystemExit(result.returncode)
    if not (ROOT/'frontend/dist/index.html').is_file():
        parser.error('Frontend build is missing. Run again without --skip-build.')
    sys.path.insert(0, str(ROOT))
    from backend.app import app
    print(f'GridGuard UI and API: http://{args.host}:{args.port}', flush=True)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
