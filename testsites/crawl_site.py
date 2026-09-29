"""Dynamic test site for crawling (answers: testsites/crawl/answers.json).

Covers: query-string boards and pagination, redirects, JS-loaded comments,
"댓글 더보기" (content only after a click), content inserted late by JS, nested
iframes, onclick-only links, an HTTP 500 page, an endless calendar, files,
external links, and traps that must never be requested (/logout, /board/delete).
The /logout page even contains a hidden ad: reporting it means the crawler
followed a state-changing link.
"""
from __future__ import annotations

import http.server
import json
import threading
from urllib.parse import parse_qs, urlsplit

HITS: dict[str, int] = {}
_lock = threading.Lock()

CSS = """<style>
body{font-family:sans-serif;margin:0;background:#fff;color:#222}
header{background:#234;color:#fff;padding:8px 16px} header a{color:#fff;margin-right:10px}
main{padding:16px} .blind{position:absolute;left:-10000px;top:auto;width:1px;height:1px;overflow:hidden}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
.notice-body .ind{text-indent:-9999px;overflow:hidden;white-space:nowrap}
.frame-box{background:#fbfbf7}.frame-box .same{color:#fbfbf7}
</style>"""

NAV = """<header><a href="/">홈</a><a href="/notice">공지사항</a><a href="/board/list?page=1">자유게시판</a>
<a href="/calendar?month=1">행사 달력</a><a href="/about">기관 소개</a><a href="/logout">로그아웃</a>
<a href="https://example.com/partner">협력기관</a><a href="/files/guide.pdf">이용안내(PDF)</a></header>"""


def page(title: str, body: str, extra_head: str = "") -> str:
    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{title}</title>{CSS}{extra_head}</head><body>{NAV}<main>{body}</main></body></html>"


POSTS = {
    1: ("도서관 휴관 안내", "이번 주 금요일은 정기 휴관일입니다."),
    2: ("주차장 이용 문의", "주말 주차 가능 시간이 궁금합니다."),
    3: ("ㅂㅏㅋㅏㄹㅏ 첫충 20% 이벤트", "문의는 댓글로 남겨주세요."),
    4: ("민원 처리 기간 문의", "지난주 신청한 민원 처리 기간이 궁금합니다."),
    5: ("시민 참여 행사 배너", "아래 배너를 확인하세요."),
    6: ("가로등 고장 신고", "○○로 12번지 앞 가로등이 꺼져 있습니다."),
    7: ("도로 보수 일정", "다음 달 도로 보수 일정을 공유합니다."),
    8: ("오류 페이지", ""),
    9: ("청소년 도박 예방 교육 안내", "학교별 예방 교육 신청을 받습니다."),
    10: ("문화센터 강좌 안내", "가을 학기 강좌를 안내합니다."),
}
COMMENTS = {
    2: (["주말은 18시까지 가능합니다.", "ｓｌｏｔ 신규가입 꽁머니 지급 @slot_vip", "감사합니다."], []),
    4: (["담당 부서에 문의해 보세요.", "저도 궁금합니다."],
        ["7일 이내 처리됩니다.", "<span class='tiny' style='font-size:1px'>토토사이트 가입코드 VIP77 즉시 환전</span>"]),
    9: (["좋은 교육이네요.", "<span class='sr'>사설토토는 불법입니다. 신고 112</span>신청했습니다."], []),
}


def view(pid: int) -> str:
    title, text = POSTS[pid]
    extra = ""
    if pid == 5:
        extra = '<div class="banner"><iframe src="/widget/banner?id=5" width="420" height="160" title="배너"></iframe></div>'
    if pid == 6:
        extra = """<div id="late-box"></div><script>setTimeout(function(){document.getElementById('late-box').innerHTML=
'<p class="late-msg">담당 부서 확인 중입니다.<span class="late" style="opacity:0">먹튀검증 메이저놀이터 텔레그램 @major_safe</span></p>'},800)</script>"""
    if pid == 3:
        extra = '<p class="admin"><a href="/board/delete?id=3">삭제</a> <a href="/board/write">글쓰기</a></p>'
    cm = ""
    if pid in COMMENTS:
        cm = f"""<section class="cmt"><h4>댓글</h4><ul class="cmt-list"></ul>
<button type="button" class="cmt-more">댓글 더보기</button></section>
<script>
function add(items){{var ul=document.querySelector('.cmt-list');items.forEach(function(t){{var li=document.createElement('li');li.className='cmt-item';li.innerHTML='<p class="cmt-body">'+t+'</p>';ul.appendChild(li);}});}}
setTimeout(function(){{fetch('/api/comments?id={pid}').then(function(r){{return r.json()}}).then(add)}},300);
document.querySelector('.cmt-more').addEventListener('click',function(e){{fetch('/api/comments?id={pid}&more=1').then(function(r){{return r.json()}}).then(function(x){{add(x);e.target.remove();}})}});
</script>"""
    prev_next = f'<p class="pager"><a href="/board/view?id={pid - 1}">이전 글</a> <a href="/board/list?page=1">목록</a></p>' if pid > 1 else ""
    return page(title, f'<article class="view"><h3 class="view-title">{title}</h3><div class="view-body"><p>{text}</p>{extra}</div></article>{cm}{prev_next}')


def board_list(n: int) -> str:
    ids = {1: [1, 2, 3, 4], 2: [5, 6, 7, 8], 3: [9, 10]}.get(n, [])
    rows = "".join(f'<tr><td>{i}</td><td class="subj"><a href="/board/view?id={i}">{POSTS[i][0]}</a></td></tr>' for i in ids)
    if n == 1:
        rows += '<tr onclick="location.href=\'/board/view.do?bbsId=B01&nttId=501\'"><td>501</td><td class="subj">공지 이관 게시물</td></tr>'
        rows += '<tr><td>7</td><td class="subj"><a href="/go?to=/board/view%3Fid%3D7">도로 보수 일정(바로가기)</a></td></tr>'
    if n == 3:
        rows += '<tr><td>-</td><td class="subj">문화센터 강좌 안내<span class="blind">홀덤 바카라 가입 첫충 30% 바로가기</span></td></tr>'
    pager = " ".join(f'<a href="/board/list?page={k}">{k}</a>' for k in (1, 2, 3))
    if n < 3:
        pager += f' <a href="/board/list?page={n + 1}">다음</a>'
    return page("자유게시판", f'<h2>자유게시판</h2><table class="board"><tbody>{rows}</tbody></table><p class="paging">{pager}</p>')


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: C901 - a test fixture
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        with _lock:
            HITS[u.path] = HITS.get(u.path, 0) + 1
        path = u.path
        if path == "/":
            body = """<h2>행복구청 누리집</h2><p>구민 여러분을 환영합니다.</p>
<p class="sr">불법 도박 예방 캠페인: 도박 문제 상담 1336</p>
<ul class="quick"><li><a href="/notice">공지사항</a></li><li><a href="/board/list?page=1">자유게시판</a></li>
<li><a href="mailto:help@example.go.kr">메일 문의</a></li><li><a href="javascript:void(0)">새로고침</a></li></ul>"""
            return self._send(200, page("행복구청", body))
        if path == "/notice":
            return self._send(200, page("공지사항", '<h2>공지사항</h2><div class="notice-body"><p>추석 연휴 민원실 운영 안내</p>'
                                                    '<p class="ind">무료야동 성인방송 바로가기 adult-free.xyz</p></div>'))
        if path == "/about":
            return self._send(200, page("기관 소개", '<h2>기관 소개</h2><p>행복구청은 불법 도박 근절 캠페인을 진행합니다.</p>'
                                                    '<p class="sr">사설토토, 불법 카지노 광고를 발견하면 신고해 주세요.</p>'))
        if path == "/board/list":
            return self._send(200, board_list(int(q.get("page", "1") or 1)))
        if path == "/board/view":
            pid = int(q.get("id", "0") or 0)
            if pid == 8:
                return self._send(500, "<h1>500 Internal Server Error</h1>")
            if pid not in POSTS:
                return self._send(404, page("없음", "<p>게시글이 없습니다.</p>"))
            return self._send(200, view(pid))
        if path == "/board/view.do":
            if q.get("bbsId") == "B01" and q.get("nttId") == "501":
                return self._send(200, page("공지 이관 게시물", '<article class="view"><h3 class="view-title">공지 이관 게시물</h3>'
                                                              '<div class="view-body"><p>이관된 공지입니다.<em class="ghost" style="color:transparent">'
                                                              '카지노 가입 즉시 보너스 지급 casino-kr.bet</em></p></div></article>'))
            return self._send(404, page("없음", "<p>없음</p>"))
        if path == "/go":
            return self._send(302, "", headers={"Location": q.get("to", "/")})
        if path == "/api/comments":
            pid = int(q.get("id", "0") or 0)
            first, more = COMMENTS.get(pid, ([], []))
            return self._send(200, json.dumps(more if q.get("more") else first, ensure_ascii=False), "application/json; charset=utf-8")
        if path == "/widget/banner":
            return self._send(200, "<!doctype html><html lang='ko'><head><meta charset='utf-8'></head><body>"
                                   "<div class='wb'><p>행사 배너</p><iframe src='/widget/inner?id=5' width='300' height='90'></iframe></div></body></html>")
        if path == "/widget/inner":
            return self._send(200, f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>{CSS}</head><body>"
                                   "<div class='frame-box'><p>참여 안내</p><p class='same'>출장안마 24시 후불 010-2222-3333</p></div></body></html>")
        if path == "/calendar":
            m = int(q.get("month", "1") or 1)
            return self._send(200, page("행사 달력", f'<h2>{m}번째 달</h2><a href="/calendar?month={m + 1}">다음 달</a>'))
        if path == "/logout":
            return self._send(200, page("로그아웃", '<p>로그아웃되었습니다.</p><span style="opacity:0">카지노 가입코드 TRAP1 즉시 지급</span>'))
        if path in ("/board/delete", "/board/write"):
            return self._send(200, page("처리", "<p>처리되었습니다.</p>"))
        if path == "/__hits":
            return self._send(200, json.dumps(HITS, ensure_ascii=False), "application/json")
        return self._send(404, page("없음", "<p>페이지가 없습니다.</p>"))
