"""The worked example of the official rules (모집요강 붙임4) as a site: the four example findings
on /board/view?id=1024 and ?id=1180 (one inside an iframe widget), reached from /board/list.

The answers in spec/answers.json use the example's own locations. The example writes
`div.board-list > tr:nth-child(7) > td.title > a`; a <tr> cannot be a direct child of a <div>
in HTML, so the page keeps the row inside a table and the answer points at the same element
(the scorer compares elements, as the official rule says)."""
from __future__ import annotations

import http.server
from urllib.parse import parse_qs, urlsplit

STYLE = """<style>body{font-family:sans-serif;background:#fff;color:#222}
.visually-hidden-note{color:#888}</style>"""


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{title}</title>{STYLE}</head>
<body><header class="top"><a href="/board/list">게시판</a></header><main>{body}</main>
<footer class="foot"><p>예시기관 · 대표전화 02-000-0000</p></footer></body></html>"""


def rows(n_ad: int) -> str:
    out = []
    for k in range(1, 9):
        text = "ｍｅｇａ－ＢＥＴ 첫충 30％ 지급" if k == n_ad else f"{k}번째 공지사항입니다"
        out.append(f'<tr><td class="no">{k}</td><td class="title"><a href="/board/view?id={1000 + k}">{text}</a></td></tr>')
    return "".join(out)


LIST = page("게시판", """<h1>공지사항</h1>
<ul class="links"><li><a href="/board/view?id=1024">시설 이용 안내</a></li><li><a href="/board/view?id=1180">행사 안내</a></li></ul>""")

VIEW_1024 = page("시설 이용 안내", f"""<h1>시설 이용 안내</h1>
<div class="notice-board"><p class="lead">이용 시간과 주차 안내입니다.</p>
<span class="visually-hidden">ㅋㅏㅈㅣㄴㅗ ㅂㅗㄴㅓㅅㅡ 코드 LUCKY7</span></div>
<div class="board-list"><table class="list"><tbody>{rows(7)}</tbody></table></div>""")

VIEW_1180 = page("행사 안내", """<h1>행사 안내</h1>
<div class="article-body"><p class="desc">가을 문화행사를 안내합니다.
<span style="color:transparent">먹튀검증 안전놀이터 상담 텔레그램 @promo_777</span></p>
<iframe src="/widget/notice" title="알림" width="400" height="80"></iframe></div>""")

WIDGET = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>알림</title></head><body>
<div class="wrap"><p>오늘의 알림이 없습니다.</p>
<span class="hidden-link" style="position:absolute;left:-9999px">무료 슬롯 체험 바로가기</span></div></body></html>"""

OTHER = page("공지", "<h1>공지</h1><p>일반 공지 내용입니다.</p>")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/board/list"):
            body = LIST
        elif u.path == "/board/view":
            body = {"1024": VIEW_1024, "1180": VIEW_1180}.get((q.get("id") or [""])[0], OTHER)
        elif u.path == "/widget/notice":
            body = WIDGET
        else:
            self.send_response(404)
            self.end_headers()
            return
        b = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
