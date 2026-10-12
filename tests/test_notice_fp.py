"""Regression for two public-notice false positives (2026-09-30 real-site scans).

1. A press-registration notice: "청와대 출입" read as "대출" once spaces were removed, and
   the id pattern took "ident" out of "president" as a messenger id.
2. A sports-club recruitment notice: one ambiguous word ("성인", from 성인부) + a homepage
   address + "상담" was enough for an ad.
All contacts below are fictitious (example.* domains, 010-0000-xxxx numbers)."""
import asyncio
from pathlib import Path

import pytest

from clearweb.detect import adjudge, analyze_frame, term_hits
from clearweb.textnorm import restore


def J(t, **kw):
    return adjudge(t, restore(t).text, **kw)


def node(i, p, tag, text, inline=False, signals=None, cls=""):
    return {"i": i, "p": p, "tag": tag, "cls": cls, "inline": inline, "text": text, "href": "",
            "signals": signals or [], "rect": None, "own": True, "id": ""}


CLIPPED = [{"technique": "OFFSCREEN", "kind": "clipped-by-overflow", "root": -1, "detail": "상위 요소 overflow로 잘림"}]
NOTICE = ["신규 출입 뉴미디어 등록 공고",
          "언론 환경 변화에 맞춰 뉴미디어의 청와대 출입 및 취재 기회를 확대하고자 합니다.",
          "□등록 방법 : 인터넷(press@office.example.kr) 또는 방문 접수",
          "□등록 문의 : 인터넷(press@office.example.kr) 또는 춘추관"]
CLUB = ("태권도스포츠클럽 회원 모집. * 성인부(선수부) 모집 - 오전 07시40분 (태보, 요가) * 초등부(태권도) 모집 "
        "* 신청방법 : 직접방문 또는 전화상담 * 전화 : 053-000-0000 ※ 자세한 사항은 스포츠클럽 홈페이지 참조 "
        "( https://sportsclub.example.or.kr/club01/index.do )")


# ---------------------------------------------------------------- word matching
def test_term_may_not_start_inside_a_word():
    assert not term_hits("청와대 출입 기자")["weak"]                 # 청와|대 출입
    assert "loan:대출" in term_hits("대출 상담")["weak"]
    assert "loan:대출" in term_hits("대 출 문의")["weak"]             # spaced-out ad word still found
    assert "loan:대출" in term_hits("무직자대출")["strong"] | term_hits("무직자대출")["weak"] or term_hits("무직자대출")["strong"]
    assert "gambling:카지노" in term_hits("카 지 노 가입")["strong"]


def test_id_pattern_needs_a_word_boundary():
    assert not J("문의 : 인터넷(pressinfo@president.go.kr) 또는 춘추관").contact
    assert not J("온라인 Store 이용 안내").contact                     # 온|라인 + Latin word
    assert any("abc123" in c for c in J("대출 문의 ID: abc123").contact)
    assert any("loan77" in c for c in J("급전 당일대출 카톡 loan77").contact)


# ---------------------------------------------------------------- the two notices
def test_registration_notice_is_not_an_ad():
    for t in NOTICE + [" ".join(NOTICE)]:
        assert not J(t).is_ad, t


def test_adult_class_recruitment_is_not_an_ad():
    assert not J(CLUB).is_ad
    assert not J("성인반 요가 수강생 모집, 상담 문의 https://center.example.go.kr").is_ad
    assert not J("도서 대출 안내 및 이용 문의 https://lib.example.go.kr").is_ad


def test_real_ads_are_still_ads():
    for t in ["대출 문의 010-0000-1234",                       # one ambiguous word + private phone
              "성인 화상 상담 카톡 adult77",                     # one ambiguous word + messenger id
              "소액 대출 즉시 승인 www.fastloan-example.com",     # two topic words + web address
              "성인방송 무료 가입 bit.example.com",
              "카지노 가입코드 WIN24 첫충 20%",
              "대 출 문 의 010-0000-5678",
              "슬롯 무료 체험 바로가기 play-now.example",          # one word + web address + two calls to action
              "대출 상담 무료 www.loan-example.com"]:
        assert J(t).is_ad, t


def test_reasons_and_matches_name_the_real_strings():
    v = J("대출 문의 010-0000-1234")
    kinds = {(m["kind"], m["text"]) for m in v.matches}
    assert ("보조 주제어", "대출") in kinds and ("연락·접속 수단", "010-0000-1234") in kinds
    assert all(isinstance(m["pos"], int) for m in v.matches)
    # one address matched by the URL and the bare-domain patterns is listed once
    v = J("소액 대출 즉시 https://fastloan.example.com/a")
    assert len(v.contact) == 1


# ---------------------------------------------------------------- units on one page
def test_hidden_paragraph_of_a_notice_is_not_reported():
    nodes = [node(0, -1, "td", " ".join(NOTICE))] + [node(k + 1, 0, "p", t) for k, t in enumerate(NOTICE)]
    nodes[-1]["signals"] = CLIPPED
    cands, _ = analyze_frame(nodes)
    assert cands == []


def test_notice_and_separate_hidden_ad_on_one_page():
    nodes = [node(0, -1, "div", "")]
    nodes += [node(1, 0, "td", " ".join(NOTICE))] + [node(k + 2, 1, "p", t) for k, t in enumerate(NOTICE)]
    nodes += [node(9, 0, "div", "카지노 가입코드 WIN24 첫충 20% 지급", signals=[
        {"technique": "TRANSPARENT", "kind": "opacity", "root": 9, "detail": "opacity:0"}])]
    cands, _ = analyze_frame(nodes)
    assert [(c.index, c.technique) for c in cands] == [(9, "TRANSPARENT")]


def test_hidden_part_needs_its_own_evidence():
    hide = [{"technique": "TRANSPARENT", "kind": "opacity", "root": 1, "detail": "opacity:0"}]
    # visible ad, hidden piece holds only a CTA word -> not reported as hidden ad
    nodes = [node(0, -1, "p", "카지노 첫충 20% 지급 가입 문의"), node(1, 0, "span", "문의", inline=True, signals=hide)]
    assert analyze_frame(nodes)[0] == []
    # hidden piece holds the contact -> reported, and the reasons say the judgement covered the parent
    nodes = [node(0, -1, "p", "카지노 첫충 20% 지급 텔레그램 @cs_example77"),
             node(1, 0, "span", "텔레그램 @cs_example77", inline=True, signals=hide)]
    cands, _ = analyze_frame(nodes)
    assert [(c.index, c.technique) for c in cands] == [(1, "TRANSPARENT")]
    assert any(r.startswith("판정 범위:") for r in cands[0].ad_reasons)
    assert cands[0].ad_matches


def test_visible_extra_ad_keeps_match_records():
    vis = []
    analyze_frame([node(0, -1, "p", "대출 문의 010-0000-1234")], True, vis)
    assert vis and vis[0]["ad_matches"]


# ---------------------------------------------------------------- collector in a real browser
PAGE = """<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;height:100%} body{overflow-y:auto;overflow-x:hidden}
.tall{height:2000px} .box{width:1px;height:1px;overflow:hidden}</style>
<script>window.Map = function () { this.map = new Object(); };</script>
<body><div class="tall"><p>첫 화면 문구</p></div>
<p id="below">스크롤해야 보이는 안내 문구</p>
<div class="box"><p id="tiny">카지노 가입코드 WIN24 첫충 20% 지급</p></div></body>"""


@pytest.fixture(scope="module")
def browser_page():
    pw_mod = pytest.importorskip("playwright.async_api")

    async def run():
        async with pw_mod.async_playwright() as pw:
            b = await pw.chromium.launch()
            pg = await b.new_page(viewport={"width": 1200, "height": 800})
            await pg.set_content(PAGE)
            from clearweb.scanner import COLLECT_JS, Scan, ScanConfig
            sc = Scan("http://127.0.0.1/", ScanConfig())
            plain = await pg.evaluate(COLLECT_JS)                 # object transfer broken by window.Map
            data = await sc._ev(pg.main_frame, COLLECT_JS)        # fallback packs the value as JSON
            links = await sc._ev(pg.main_frame, __import__("clearweb.scanner", fromlist=["LINKS_JS"]).LINKS_JS)
            await b.close()
            return plain, data, links, sc._packed_evals
    return asyncio.run(run())


def test_page_that_replaces_map_is_still_collected(browser_page):
    plain, data, links, packed = browser_page
    assert plain is None                                   # what the scanner saw before
    assert data and data["nodes"] and packed >= 1
    assert isinstance(links, list)


def test_scroll_box_is_not_hiding_but_tiny_box_is(browser_page):
    _, data, _, _ = browser_page
    by_text = {n["text"].strip(): n for n in data["nodes"]}
    below = by_text["스크롤해야 보이는 안내 문구"]
    assert not below["signals"], below["signals"]          # reachable by scrolling <body>
    tiny = by_text["카지노 가입코드 WIN24 첫충 20% 지급"]
    assert any(s["technique"] == "OFFSCREEN" for s in tiny["signals"])


# ---------------------------------------------------------------- file links and broken links
def test_file_link_is_not_a_failed_page_and_404_is_kept_apart(tmp_path, monkeypatch):
    """An attachment link without a file extension starts a download (not a page, not a
    failure); a scheme-less link ("www.example.kr") resolves to a 404 on the same site."""
    pytest.importorskip("playwright.async_api")
    import http.server
    import threading
    from clearweb import scanner

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/":
                body = ('<!doctype html><meta charset="utf-8"><p>공지</p>'
                        '<a href="/jfile/readFile.do?fileId=7">첨부</a> <a href="www.example.kr">사이트</a>').encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif self.path.startswith("/jfile/"):
                body = b"%PDF-1.4 fake"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="a.pdf"')
            else:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    try:
        cfg = scanner.ScanConfig()
        cfg.max_pages = 3
        sc = scanner.Scan(f"http://127.0.0.1:{srv.server_port}/", cfg, tmp_path)
        sc.run()
    finally:
        srv.shutdown()
    p = sc.progress
    import json
    rec = json.loads((tmp_path / "runs" / sc.run_id / "scan.json").read_text(encoding="utf-8"))
    st = {pg["requested_url"].split("/", 3)[-1]: pg["status"] for pg in rec["pages"]}
    assert st.get("jfile/readFile.do?fileId=7") == "file"
    assert st.get("www.example.kr") == "http_error"
    assert p.pages_failed == 0 and p.pages_http_error == 1 and p.pages_file == 1
    assert "응답 오류" in p.end_reason and "불러오지 못함" not in p.end_reason


def test_scattered_weak_evidence_in_a_long_notice_is_not_an_ad():
    """'대상: 성인 80명 … 수강료: 무료 … 신청: 네이버 폼 https://… 문의' (2026-10-02, 경산시 박물관)."""
    t = ("박물관에서는 고고학 아카데미 프로그램을 운영하고자 합니다. 많은 관심과 참여 부탁드립니다. "
         "운영 기간 : 2025. 9. 4.(목) ~ 2025. 11. 27.(목) 14:00 ~ 16:00 모 집 대 상 : 성인 80명(선착순 접수) "
         "운 영 방 법 : 10회기 이론강좌 수 강 료 : 무료 신청방법 : 2025. 8. 1.(금) 10:00 부터 네이버 폼 "
         "https://form.example.com/Ab12 문 의 : 박물관 053-000-0000")
    v = J(t)
    assert not v.is_ad and v.excluded_by
    # the same words standing together are still an ad
    assert J("성인 무료 체험 바로가기 https://form.example.com/Ab12").is_ad
    assert J("슬롯 무료 체험 바로가기 play-now.example").is_ad


# ---------------------------------------------------------------- 2026-10-03: '오피' inside '오피스'
STARTUP_CARD = ("대구삼성창조캠퍼스 벤처오피스 찜하기 주소대구광역시 북구 호암로 51대구삼성창조캠퍼스 벤처오피스동 "
                "전화번호010-0000-0333 (053-000-3494) 특화분야벤처기업 공간수 52 공실수 8")


def test_office_words_are_not_the_adult_term():
    """'벤처오피스' matched the adult term '오피' (and '주소' + a mobile number completed the rule)."""
    assert not J(STARTUP_CARD).is_ad
    for t in ["오피스텔 분양 안내 주소 대구 전화 010-0000-1111", "공유오피스 입주 상담 010-0000-2222",
              "벤처오피스동 3층 주소 안내 문의 010-0000-3333"]:
        assert not J(t).is_ad, t
    assert not term_hits("벤처오피스 오피스텔")["weak"]


def test_real_ad_next_to_an_office_word_is_still_found():
    assert J("강남 오피 24시 출장 문의 010-0000-4444").is_ad
    assert J("오피스텔 입주 안내. 강남 오피 출장 문의 010-0000-5555").is_ad     # the lone '오피' still counts
    assert J("오 피 후불 문의 010-0000-6666").is_ad
    assert J("카지노 가입코드 WIN24 첫충 20% 지급").is_ad


def test_phone_number_alone_is_never_an_ad():
    assert not J("전화번호 010-0000-7777").is_ad
    assert not J("상담 문의 010-0000-8888 주소 대구광역시").is_ad


def test_office_card_official_and_extra_split():
    hide = [{"technique": "OFFSCREEN", "kind": "offscreen", "root": 3, "detail": "left:-9999px"}]
    nodes = [node(0, -1, "ul", ""),
             node(1, 0, "li", STARTUP_CARD, cls="card"),
             node(2, 0, "li", "오피스텔 매물 안내 " + "강남 오피 출장 문의 010-0000-9999", cls="card2"),
             node(3, 0, "li", "오피 24시 출장안마 텔레그램 @ex_office77", cls="card3", signals=hide)]
    vis = []
    cands, _ = analyze_frame(nodes, True, vis)
    assert [(c.index, c.technique) for c in cands] == [(3, "OFFSCREEN")]      # hidden ad: official result
    assert [v["index"] for v in vis] == [2]                                    # visible ad: extra suspect only
    # the office card itself is neither
