"""Failure counting and reasons (2026-10-03 real-site runs).

- The end reason counted 'partial' pages (slow load, but checked) as 'not loaded', so it said
  6 while the screen said 4 (museum) and 14 vs 11 (Daegu).
- Pages that showed an alert ("로그인이 필요한 서비스입니다", "메뉴 권한이 없습니다",
  "이 글은 비밀글 입니다") and then turned into about:blank were 'failed' with no reason.
- The last pages of a scan were cut because the whole budget ran out, but read as slow pages."""
import asyncio
import http.server
import json
import threading

import pytest

from clearweb import scanner
from clearweb.scanner import fail_kind, page_summary


def pg(status, error="", code=None, frames=None):
    return {"status": status, "error": error, "http_status": code, "frames": frames or []}


def test_summary_counts_pages_like_the_screen():
    pages = [pg("ok"), pg("ok", frames=[{"status": "failed"}, {"status": "detached"}]),   # frame problems: page is ok
             pg("partial", "로딩 지연/실패: : Timeout 20000ms exceeded."), pg("duplicate"), pg("file"),
             pg("failed", "로딩 지연/실패: : Timeout 20000ms exceeded."),
             pg("failed", "로딩 지연/실패: : net::ERR_NAME_NOT_RESOLVED at http://x.example/"),
             pg("restricted", "접근 제한 — 사이트 알림창 뒤 빈 화면으로 바뀜: “메뉴 권한이 없습니다.”"),
             pg("failed", "사이트 알림창 뒤 빈 화면으로 바뀜: “이 글은 비밀글 입니다.”"),   # 0.1.6/0.1.7 record
             pg("failed", "전체 시간 예산이 거의 끝나 9초만 점검함 — 일부만 점검됨"),        # 0.1.6-0.1.8 record
             pg("budget_cut", "시간 한도로 중단 — 전체 시간 예산이 거의 끝나 5초만 점검함(이어서 검사 대상)"),
             pg("failed", ""), pg("http_error", "HTTP 400", 400), pg("http_error", "HTTP 400", 400),
             pg("http_error", "HTTP 500", 500)]
    s = page_summary(pages)
    assert s.startswith("불러오지 못함 3건(")
    assert "시간 한도로 중단 2건(이어서 검사 대상)" in s
    assert "HTTP 응답 오류 3건(400: 2, 500: 1)" in s and "일부만 로딩 1건(점검함)" in s
    assert "접근 제한 2건" in s
    assert "없는 페이지" not in s and "404 등" not in s
    kinds = [fail_kind(p) for p in pages if p["status"] in ("failed", "restricted", "budget_cut")]
    assert kinds == ["로딩 제한 시간 초과", "주소 이름 해석 실패(DNS)", "접근 제한(로그인·권한 필요 등)",
                     "접근 제한(로그인·권한 필요 등)", "시간 한도로 중단", "시간 한도로 중단", "사유 미기록"]


@pytest.fixture()
def alert_site():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/":
                body = '<!doctype html><meta charset="utf-8"><p>홈</p><a href="/board/view?id=7">비밀글</a>'
            else:   # like the museum mypage: alert, then the page leaves for about:blank
                body = ('<!doctype html><meta charset="utf-8"><script>alert("로그인이 필요한 서비스입니다.");'
                        'location.replace("about:blank");</script>')
            b = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def _record(sc, tmp_path):
    return json.loads((tmp_path / "runs" / sc.run_id / "scan.json").read_text(encoding="utf-8"))


def test_alert_then_blank_page_records_what_the_site_said(alert_site, tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    sc = scanner.Scan(alert_site, scanner.ScanConfig(), tmp_path)
    sc.run()
    rec = _record(sc, tmp_path)
    my = next(p for p in rec["pages"] if "/board/view" in p["requested_url"])
    assert my["status"] == "restricted"
    assert my["error"] == "접근 제한 — 사이트 알림창 뒤 빈 화면으로 바뀜: “로그인이 필요한 서비스입니다.”"
    assert "접근 제한 1건" in rec["end_reason"]
    assert sc.progress.pages_failed == 0 and sc.progress.pages_restricted == 1


def test_page_cut_by_the_end_of_the_budget_is_marked_and_kept_for_continue(alert_site, tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    orig = scanner.Scan._page_work

    async def slow(self, page, url, rec, findings, excluded):
        await orig(self, page, url, rec, findings, excluded)
        if url.endswith("/"):
            await asyncio.sleep(60)          # longer than what is left of the 15 s budget
    monkeypatch.setattr(scanner.Scan, "_page_work", slow)
    sc = scanner.Scan(alert_site, scanner.ScanConfig(total_budget_s=15, follow_links=False), tmp_path)
    sc.run()
    rec = _record(sc, tmp_path)
    page = rec["pages"][0]
    assert page["status"] == "budget_cut" and page["error"].startswith("시간 한도로 중단")
    assert sc.progress.pages_failed == 0 and sc.progress.pages_cut == 1
    assert any(u.rstrip("/") == alert_site.rstrip("/") for _, u, _ in rec["resume"]["pending"])


@pytest.fixture()
def login_site():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/board/members"):          # anonymous visitor -> login page
                self.send_response(302)
                self.send_header("Location", "/member/login.do?returnUrl=/board/members")
                self.end_headers()
                return
            body = ('<!doctype html><meta charset="utf-8"><p>홈</p><a href="/board/members">회원 게시판</a>'
                    if self.path == "/" else '<!doctype html><meta charset="utf-8"><form>아이디 비밀번호</form>')
            b = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def test_link_that_lands_on_a_login_page_is_restricted(login_site, tmp_path, monkeypatch):
    """museum 0.1.8 desktop run: a mentornew link landed on login.do?schM=public_login and was
    checked as an ordinary page."""
    pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    sc = scanner.Scan(login_site, scanner.ScanConfig(), tmp_path)
    sc.run()
    rec = _record(sc, tmp_path)
    pg_ = next(p for p in rec["pages"] if "/board/members" in p["requested_url"])
    assert pg_["status"] == "restricted" and pg_["error"].startswith("접근 제한 — 로그인 페이지로 이동됨")
    assert sc.progress.pages_failed == 0


def test_account_and_session_urls():
    from clearweb.crawl import low_value_reason, strip_tracking
    assert low_value_reason("https://www.example.go.kr/curator/idsearch/idsearch.do") == "idsearch"
    assert low_value_reason("https://www.example.go.kr/curator/pwsearch/pwsearch.do") == "pwsearch"
    assert strip_tracking("https://x.example.go.kr/list.do;jsessionid=B5FE0F4B?pageIndex=2") == \
        "https://x.example.go.kr/list.do?pageIndex=2"
    assert strip_tracking("https://x.example.go.kr/list.do?pageIndex=2&sid=abc") == \
        "https://x.example.go.kr/list.do?pageIndex=2&sid=abc"      # query values are kept
