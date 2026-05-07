"""Cross-platform launcher.

Usage:
    python run.py            # build SPA if missing, then start the server
    python run.py --skip-ui  # start the server without (re)building the SPA
    python run.py --rebuild  # force a fresh SPA build
    python run.py --dev      # back-end only; you run `npm run dev` separately

Works on Windows, macOS, and Linux. Requires Python 3.10+ and Node.js 18+.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess  # noqa: S404 - needed to invoke npm; commands are static
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
STATIC = ROOT / "app" / "static"


def _which(cmd: str) -> str | None:
    # Resolve commands like 'npm' on Windows where it's actually 'npm.cmd'.
    candidates = [cmd]
    if os.name == "nt":
        candidates += [f"{cmd}.cmd", f"{cmd}.exe"]
    for c in candidates:
        path = shutil.which(c)
        if path:
            return path
    return None


def build_frontend(force: bool = False) -> None:
    index_html = STATIC / "index.html"
    if index_html.exists() and not force:
        return
    npm = _which("npm")
    if npm is None:
        print(
            "[run.py] npm not found in PATH. Either install Node.js 18+ or pass --skip-ui "
            "(the panel will start with an API-only placeholder).",
            file=sys.stderr,
        )
        sys.exit(2)
    print("[run.py] Installing frontend dependencies…")
    subprocess.check_call([npm, "ci"], cwd=str(FRONTEND))  # noqa: S603 - static argv
    print("[run.py] Building frontend…")
    subprocess.check_call([npm, "run", "build"], cwd=str(FRONTEND))  # noqa: S603 - static argv


def start_server() -> None:
    # Imported here so we don't pull FastAPI before the venv is active.
    import uvicorn

    from app.config import get_settings

    settings = get_settings()
    print(f"[run.py] Starting on http://{settings.host}:{settings.port}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ui", action="store_true", help="Don't build the SPA.")
    parser.add_argument("--rebuild", action="store_true", help="Force a fresh SPA build.")
    parser.add_argument("--dev", action="store_true", help="Skip SPA build (use Vite dev server).")
    args = parser.parse_args()

    if not args.dev and not args.skip_ui:
        build_frontend(force=args.rebuild)

    start_server()


if __name__ == "__main__":
    main()
