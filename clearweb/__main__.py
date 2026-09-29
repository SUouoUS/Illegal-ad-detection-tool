"""ClearWeb entry point.

  ClearWeb                  -> start the local screen (127.0.0.1) and open the browser
  ClearWeb scan <url>       -> run a scan without the screen, write result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ClearWeb")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="scan a site without the screen")
    s.add_argument("url")
    s.add_argument("--out", type=Path, default=None, help="folder for result.json (default: program folder)")
    s.add_argument("--headed", action="store_true")
    s.add_argument("--max-pages", type=int, default=None, help="최대 점검 페이지 수 (기본 50)")
    s.add_argument("--max-depth", type=int, default=None, help="최대 탐색 깊이 (기본 2)")
    s.add_argument("--budget", type=float, default=None, help="전체 시간 예산(초, 기본 1500)")
    s.add_argument("--concurrency", type=int, default=None, help="동시 점검 페이지 수 (기본 4)")
    s.add_argument("--no-follow", action="store_true", help="입력한 페이지만 점검(하위 페이지 탐색 안 함)")
    g = sub.add_parser("serve", help="start the local screen")
    g.add_argument("--port", type=int, default=8765)
    g.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "scan":
        from .scanner import Scan, ScanConfig
        cfg = ScanConfig(headless=not a.headed, follow_links=not a.no_follow)
        if a.max_pages:
            cfg.max_pages = a.max_pages
        if a.max_depth is not None:
            cfg.max_depth = a.max_depth
        if a.budget:
            cfg.total_budget_s = a.budget
        if a.concurrency:
            cfg.concurrency = a.concurrency
        sc = Scan(a.url, cfg, a.out)
        rec = sc.run()
        print(json.dumps({k: rec[k] for k in ("state", "end_reason", "elapsed_sec", "result_path")}, ensure_ascii=False))
        print(f"findings: {len(rec['result']['findings'])}")
        return 0 if rec["state"] in ("done", "stopped") else 1

    from .server import serve
    port = getattr(a, "port", 8765)
    serve(port=port, open_browser=not getattr(a, "no_browser", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
