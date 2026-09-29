"""Local test sites with known answers.

usage: python -m testsites.server [--site dev] [--port 8811]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass


def start(site: str = "dev", port: int = 8811) -> http.server.ThreadingHTTPServer:
    if site == "crawl":
        from .crawl_site import Handler as handler  # dynamic pages
    else:
        handler = functools.partial(Handler, directory=str(ROOT / site))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="dev")
    ap.add_argument("--port", type=int, default=8811)
    a = ap.parse_args()
    s = start(a.site, a.port)
    print(f"serving {a.site} at http://127.0.0.1:{a.port}/")
    threading.Event().wait()
