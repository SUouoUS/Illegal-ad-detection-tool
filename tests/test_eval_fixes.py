"""Regression for the causes found in the first eval run (variants, not the eval pages)."""
import re

from clearweb.crawl import Policy
from clearweb.detect import adjudge, rules
from clearweb.textnorm import restore


# ---------------------------------------------------------------- path-style paging
def test_path_paging_is_lateral_only_in_a_paging_control():
    p = Policy("http://x/")
    assert p.child_depth("http://x/notice/list-1.html", "http://x/notice/list-2.html", 1, pager=True) == 1
    assert p.child_depth("http://x/board/page/2/", "http://x/board/page/3/", 1, pager=True) == 1
    assert p.child_depth("http://x/bbs/list.html", "http://x/bbs/list_2.html", 1, pager=True) == 1
    # same shape but not in a paging control (year archive in body text, next article)
    assert p.child_depth("http://x/news/2024.html", "http://x/news/2025.html", 1, pager=False) == 2
    assert p.child_depth("http://x/view-101.html", "http://x/view-102.html", 1, pager=False) == 2
    # different list or different query: not the same list
    assert p.child_depth("http://x/a/list-1.html?cat=1", "http://x/a/list-2.html?cat=2", 1, pager=True) == 2
    assert p.child_depth("http://x/notice/list-1.html", "http://x/notice/n-207.html", 1, pager=True) == 2


def test_path_paging_lateral_moves_are_capped():
    p = Policy("http://x/", per_pattern=3)
    depths = [p.child_depth(f"http://x/cal/m-{k}.html", f"http://x/cal/m-{k + 1}.html", 1, pager=True) for k in range(6)]
    assert depths == [1, 1, 1, 2, 2, 2]


# ---------------------------------------------------------------- digits used as letters
def test_edge_digits_restored_only_when_a_topic_word_appears():
    for raw, want in [("c4sin0 신규 가입", "casino"), ("t0t0 가입", "toto"), ("5lot 무료", "slot"), ("p0ker 3", "poker")]:
        assert want in restore(raw).text.lower(), raw
    for raw in ["RTX5090 출시", "WIN11 업데이트", "LUCKY7 코드", "TEST77", "가입코드 BET2026", "bet365", "A4 용지",
                "G7 정상회의", "MP3 파일", "문의: help@example.go.kr"]:
        assert restore(raw).text == raw, raw


# ---------------------------------------------------------------- domains and call-to-action context
def _identifiers(text):
    return [m.group(0) for rx in rules().identifier for m in [rx.search(text)] if m]


def test_domains_without_a_fixed_tld_list():
    for t in ["문의 play-now.live", "bac-live.test 입장", "slot7.xyz/event", "www.korea.go.kr", "bkr-vip.com"]:
        assert _identifiers(t), t


def test_file_names_sentences_and_mail_are_not_domains():
    for t in ["첨부: 안내문.hwp", "report.pdf 참고", "index.html", "help@example.go.kr", "완료했습니다.다음 안내",
              "e.g. 예시", "v2.5 업데이트", "3.14", "No.1 서비스", "Mr.Kim 발표", "done.Next time"]:
        assert not any(re.search(r"[a-z]\.[a-z]", x, re.I) for x in _identifiers(t)), (t, _identifiers(t))


def test_entry_and_live_words_alone_are_not_ads():
    for t in ["박물관 입장료 안내 및 실시간 혼잡도", "실시간 교통 정보 입장 대기", "바카라 실시간",
              "스포츠토토 실시간 배당 조회 서비스 종료 기사", "공연장 입장 안내 ticket.example.com"]:
        assert not adjudge(t, restore(t).text).is_ad, t
    assert adjudge("라이브 바카라 실시간 입장 bac-live.test", "라이브 바카라 실시간 입장 bac-live.test").is_ad
