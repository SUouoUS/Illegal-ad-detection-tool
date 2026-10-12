"""'More'/'expand' clicks: pure menu toggles are learned and skipped, loaders are not.

On www.museum.go.kr every page clicked the same 12 menu accordion buttons ("예약하기 펼치기"
...), 237 pages x 12 clicks: the menu text is already in the page, so nothing new appears.
A comment loader asks the server (even when nothing more comes back) and stays clicked."""
import http.server
import json
import threading

import pytest

pytest.importorskip("playwright.async_api")
from clearweb import scanner  # noqa: E402

MENU = "".join(f'<li><button type="button" class="tg" aria-expanded="false" '
               f'onclick="this.nextElementSibling.hidden=!this.nextElementSibling.hidden">메뉴{k} 펼치기</button>'
               f'<ul hidden><li><a href="/p?id={k}">하위{k}</a></li></ul></li>' for k in range(1, 4))


def page(i: int) -> str:
    links = "".join(f'<a href="/p?id={j}">글 {j}</a> ' for j in range(1, 6))
    return f"""<!doctype html><meta charset="utf-8"><title>글 {i}</title>
<nav><ul>{MENU}</ul></nav><main><h1>공지 {i}</h1><p>시설 이용 안내 본문입니다.</p>{links}
<section><ul class="cmts"></ul><button type="button" class="more">댓글 더보기</button></section></main>
<script>document.querySelector('.more').addEventListener('click', function(){{
  fetch('/c?id={i}').then(function(r){{return r.json()}}).then(function(xs){{xs.forEach(function(t){{
    var li=document.createElement('li'); li.innerHTML=t; document.querySelector('.cmts').appendChild(li);}});}});}});</script>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/c?id="):
            i = int(self.path.split("=")[1])
            # only post 5 has a (hidden) ad comment; the others return nothing more
            items = ['<span style="opacity:0">카지노 가입코드 WIN24 첫충 20% 지급</span>'] if i == 5 else []
            body, ct = json.dumps(items).encode(), "application/json"
        else:
            i = int(self.path.split("=")[1]) if "id=" in self.path else 1
            body, ct = page(i).encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_menu_toggles_are_skipped_after_two_pages_loaders_are_not(tmp_path, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    try:
        cfg = scanner.ScanConfig(concurrency=1, concurrency_auto=False)   # pages in a fixed order
        sc = scanner.Scan(f"http://127.0.0.1:{srv.server_port}/p?id=1", cfg, tmp_path)
        sc.run()
    finally:
        srv.shutdown()
    rec = json.loads((tmp_path / "runs" / sc.run_id / "scan.json").read_text(encoding="utf-8"))
    pages = [p for p in rec["pages"] if p["status"] in ("ok", "partial")]
    assert len(pages) >= 5
    menu_clicks = [sum(1 for x in p["expanded"] if "메뉴" in x) for p in pages]
    assert menu_clicks[:2] == [3, 3]                    # learned on two pages ...
    assert all(n == 0 for n in menu_clicks[2:])         # ... then skipped
    assert all("댓글 더보기" in p["expanded"] for p in pages)     # the loader is always clicked
    assert sc.expand_skipped >= 3 * (len(pages) - 2)
    # the ad that only appears after the comment loader is still found
    assert any("WIN24" in f["evidence_text"] for f in rec["findings"])
