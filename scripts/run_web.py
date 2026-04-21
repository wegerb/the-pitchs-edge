"""Regenerate web/data.json and serve the static dashboard on localhost.

    python -m scripts.run_web
    python -m scripts.run_web --port 8080 --no-regen
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import webbrowser
from pathlib import Path

from pitchs_edge.config import REPO_ROOT

WEB_DIR = REPO_ROOT / "web"


def regenerate() -> None:
    from scripts.export_web_data import main as export_main

    sys.argv = ["export_web_data.py"]
    export_main()


def serve(port: int, open_browser: bool) -> None:
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(WEB_DIR), **kw)
    with socketserver.TCPServer(("", port), handler) as httpd:
        url = f"http://localhost:{port}/"
        print(f"[web] serving {WEB_DIR} at {url}  (Ctrl-C to stop)")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[web] bye")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-regen", action="store_true", help="skip regenerating data.json before serving")
    ap.add_argument("--no-open",  action="store_true", help="don't auto-open the browser")
    args = ap.parse_args()

    if not args.no_regen:
        regenerate()
    serve(port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
