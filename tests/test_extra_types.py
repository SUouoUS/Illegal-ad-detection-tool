"""Extra detection types in result_extra.json: SEO_SPAM (ad text only in title/meta/alt/title=/
aria-label) and HIDDEN_LINK (a link to another site with no visible words). Domains are fictitious."""
import http.server
import json
import threading

import pytest

pytest.importorskip("playwright.async_api")
from clearweb import result_writer, scanner  # noqa: E402

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>한빛시청 공지사항</title>
<meta name="description" content="한빛시청 카지노 사이트 가입코드 WIN24 첫충 20% 지급">
<meta name="keywords" content="한빛시, 공지, 민원"></head>
<body><main><h1>공지사항</h1><p>민원실 운영 시간 안내입니다.</p>
<img class="banner" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="안전놀이터 먹튀검증 텔레그램 @hb_promo77" width="10" height="10">
<img class="poster" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="시민 정보화 교육 포스터" width="10" height="10">
<a class="ghost" href="https://casino-win24.example/join?code=WIN24"></a>
<a class="gov" href="https://www.gov.example/portal">정부 누리집</a>
<a class="empty-gov" href="https://www.gov.example/portal"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="정부 누리집" width="10" height="10"></a>
</main></body></html>"""


@pytest.fixture()
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def test_seo_spam_and_hidden_link(site, tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    scanner.Scan(site, scanner.ScanConfig(follow_links=False), tmp_path).run()
    extra = json.loads((tmp_path / "result_extra.json").read_text(encoding="utf-8"))
    assert result_writer.validate_extra(extra) == []
    got = {(f["extra_finding"].split("(")[0], f["location"]) for f in extra["findings"]}
    assert ("SEO_SPAM", 'head > meta:nth-of-type(2)') in got or any(
        k == "SEO_SPAM" and "meta" in loc for k, loc in got)                      # meta description
    assert any(k == "SEO_SPAM" and "img.banner" in loc for k, loc in got)       # alt text
    assert any(k == "HIDDEN_LINK" and "a.ghost" in loc for k, loc in got)       # empty link to an ad site
    assert not any("poster" in loc or "gov" in loc for _, loc in got)           # ordinary alt / links
    assert not any("keywords" in f["evidence_text"] for f in extra["findings"])  # ordinary keywords
    link = next(f for f in extra["findings"] if f["extra_finding"].startswith("HIDDEN_LINK"))
    assert "casino-win24.example" in link["evidence_text"]
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["findings"] == []   # official file untouched


def test_title_shown_on_page_is_not_seo_spam(tmp_path, monkeypatch):
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>카지노 사이트 가입코드 WIN24 첫충 20%</title></head>"
            "<body><h1>카지노 사이트 가입코드 WIN24 첫충 20%</h1></body></html>").encode()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
        scanner.Scan(f"http://127.0.0.1:{srv.server_port}/", scanner.ScanConfig(follow_links=False), tmp_path).run()
    finally:
        srv.shutdown()
    extra = json.loads((tmp_path / "result_extra.json").read_text(encoding="utf-8"))
    assert not any(f["extra_finding"].startswith("SEO_SPAM") for f in extra["findings"])
