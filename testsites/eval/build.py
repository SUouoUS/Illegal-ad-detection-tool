"""Build the evaluation site (static). Theme: a provincial museum, unlike the dev site.

All ad strings are fake test data (handles *_demo, domains *.test, codes like MUSE11).
Answers are fixed in answers.json BEFORE the detector is run on this site.
Location convention: the element that holds the hidden ad text (the element whose
text is the ad). If a hiding style sits on a wrapper that contains only a <p> with
the ad, the answer is the inner <p> (the text holder), not the wrapper.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CSS = """
*{box-sizing:border-box} body{margin:0;font-family:'Malgun Gothic',sans-serif;color:#26313b;background:#fafaf7}
.site-header{background:#fff;border-bottom:1px solid #ddd;padding:10px 24px;display:flex;gap:24px;align-items:center}
.site-header__logo{font-weight:700} .gnb__item{position:relative;display:inline-block;margin-right:14px}
.gnb__sub{display:none;position:absolute} .gnb__item:hover .gnb__sub{display:block}
.u-sr{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip-nav{position:absolute;top:-60px} .skip-nav:focus{top:0}
.page{max-width:1080px;margin:0 auto;padding:20px}
.card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:#fff;border:1px solid #e3e3dc;padding:12px}
.card__desc--cut{text-indent:-9999px;overflow:hidden;white-space:nowrap}
.site-footer{background:#1f2a36;color:#c9d2dc;padding:18px 24px}
.site-footer__band{color:#1f2a36}
.is-muted{opacity:0}
.tab-panel{display:none} .tab-panel.is-active{display:block}
.faq__answer{display:none}
.modal{display:none;position:fixed;inset:0}
.offcanvas{position:fixed;left:-320px;top:0;width:300px;height:100%}
.notice-table{width:100%;border-collapse:collapse} .notice-table td{border-bottom:1px solid #e3e3dc;padding:6px}
.clip-hide{position:absolute;clip:rect(0 0 0 0);width:1px;height:1px;overflow:hidden}
.post-view__body{line-height:1.7} .cmt{border-top:1px solid #eee;padding:8px 0}
.tiny{font-size:1px} .zero{font-size:0}
.hint-box{background:#eef4ea;padding:10px} .hint-box__same{color:#eef4ea}
"""


# only on the entry page (whether a shared-layout element repeated on every page counts
# once or per page is not settled by the official spec, so it is avoided here)
FOOTER_ADS = ('<p class="site-footer__band">바카라 첫입금 30% 추가 지급 입장코드 MUSE11</p>\n'
              '  <p class="site-footer__extra" style="display:none">홀덤 토너먼트 참가비 지원 가입코드 ACE9</p>')


def page(title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>{title} | 도립 한빛박물관</title><link rel="stylesheet" href="{up}style.css"></head>
<body>
<a class="skip-nav" href="#main">본문 바로가기</a>
<header class="site-header">
  <span class="site-header__logo">도립 한빛박물관</span>
  <nav class="gnb"><ul class="gnb__list">
    <li class="gnb__item"><a href="{up}exhibitions.html">전시</a>
      <ul class="gnb__sub"><li><a href="{up}exhibitions.html">상설전시</a></li><li><a href="{up}exhibitions.html">기획전시</a></li></ul></li>
    <li class="gnb__item"><a href="{up}notice/list-1.html">알림마당</a>
      <ul class="gnb__sub"><li><a href="{up}notice/list-1.html">공지사항</a></li><li><a href="{up}notice/n-205.html">보도자료</a></li></ul></li>
    <li class="gnb__item"><a href="{up}programs.html">교육·행사</a></li>
    <li class="gnb__item"><a href="{up}about.html">박물관 소개</a></li>
    <li class="gnb__item"><a href="{up}member/logout.html">로그아웃</a></li>
  </ul></nav>
</header>
<aside class="offcanvas" aria-hidden="true"><p>모바일 메뉴: 전시, 알림마당, 교육·행사, 박물관 소개</p></aside>
<main id="main" class="page">
{body}
</main>
<footer class="site-footer">
  <p class="site-footer__addr">(가상) 한빛도 한빛시 박물관로 1 · 관람 문의 000-000-0000</p>
  {FOOTER_ADS if title == "홈" else ""}
</footer>
</body>
</html>
"""


def write(rel: str, html: str):
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")


def main():
    (ROOT / "style.css").write_text(CSS, encoding="utf-8")

    # ------------------------------------------------------------ entry
    write("index.html", page("홈", """
<section class="hero"><h1 class="hero__title">빛으로 읽는 한빛의 역사</h1>
  <p class="hero__lead">２０２６년 가을 특별전이 시작됩니다.</p></section>
<section class="prevent-banner"><p class="prevent-banner__text">도박은 질병입니다. 혼자 고민하지 마시고 1336에 전화하세요. (한국도박문제예방치유원)</p></section>
<section class="news">
  <h2 class="news__title">알림</h2>
  <div class="card-grid">
    <article class="card"><h3 class="card__title"><a href="notice/list-1.html">공지사항</a></h3><p class="card__desc">휴관일과 관람 시간 변경 안내</p></article>
    <article class="card"><h3 class="card__title"><a href="exhibitions.html">전시 소식</a></h3>
      <p class="card__desc card__desc--cut">슬롯 무료체험 바로가기 slot-play.test</p></article>
    <article class="card"><h3 class="card__title"><a href="programs.html">교육 행사</a></h3><p class="card__desc">어린이 도슨트 프로그램 모집</p></article>
  </div>
</section>
<section class="tabs">
  <div class="tab-panel is-active"><p>오늘의 관람객 1,204명</p></div>
  <div class="tab-panel"><ul class="headline-list">
    <li><a href="notice/n-205.html">문화재단, 소장품 기증자에게 감사패 지급</a></li>
    <li><a href="notice/n-206.html">협력 기업, 올해 주주 현금배당 규모 발표</a></li>
    <li><a href="notice/n-204.html">가을 음악회 사전예약 이벤트 마감</a></li></ul></div>
</section>
<p class="lang-link">Москва 특별전 관람 안내</p>
""", 0))

    # ------------------------------------------------------------ exhibitions
    write("exhibitions.html", page("전시", """
<h1 class="sub-title">전시</h1>
<dl class="exhibit-list">
  <dt class="exhibit-list__name">상설전시 1관</dt><dd class="exhibit-list__desc">선사시대부터 근대까지 한빛의 생활사</dd>
  <dt class="exhibit-list__name">기획전시 A</dt><dd class="exhibit-list__desc exhibit-list__desc--sponsor">Ｃａｓｉｎｏ 신규회원 보너스 즉시 지급</dd>
  <dt class="exhibit-list__name">기획전시 B</dt><dd class="exhibit-list__desc">빛과 그림자, 근대 사진전<span class="tiny exhibit-list__tag">ㅂㅏㅋㅏㄹㅏ 첫충 이벤트 코드 RED7</span></dd>
</dl>
<figure class="exhibit-figure"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt=""><figcaption class="exhibit-figure__cap">ㅁㅓㄱㅌㅟ ㄱㅓㅁㅈㅡㅇ 사이트 가입 문의 받습니다</figcaption></figure>
<section class="faq"><h2>자주 묻는 질문</h2>
  <div class="faq__item"><p class="faq__q">사진 촬영이 가능한가요?</p><p class="faq__answer">플래시 없이 촬영 가능합니다.</p></div>
  <div class="faq__item"><p class="faq__q">주차는 무료인가요?</p><p class="faq__answer">관람객은 2시간 무료입니다.</p></div>
</section>
""", 0))

    # ------------------------------------------------------------ notice list pages (lateral)
    rows = {
        1: [(201, "추석 연휴 휴관 안내"), (202, "관람 예약 시스템 점검"), (203, "어린이 도슨트 모집 결과")],
        2: [(204, "가을 음악회 안내"), (205, "보도자료: 불법 도박 광고 차단 협약"), (206, "소장품 기증 캠페인")],
        3: [(207, "시설 개선 공사 안내")],
    }
    for n in (1, 2, 3):
        trs = "".join(f'<tr><td>{i}</td><td class="notice-table__subject"><a href="n-{i}.html">{t}</a></td></tr>' for i, t in rows[n])
        if n == 1:
            trs += '<tr><td>-</td><td class="notice-table__subject">관람 안내<span style="opacity:0">스포츠 배팅 첫충 매충 가입코드 PLAY3</span></td></tr>'
        if n == 2:
            trs += '<tr><td>-</td><td class="notice-table__subject">기증 안내<span class="clip-hide">먹튀 없는 메이저놀이터 추천 코드 SAFE88</span></td></tr>'
        if n == 3:
            trs += '<tr><td>-</td><td class="notice-table__subject">тото 가입 즉시 매충 10% 지급</td></tr>'
        pager = " ".join(f'<a class="pager__link" href="list-{k}.html">{k}</a>' for k in (1, 2, 3))
        write(f"notice/list-{n}.html", page(f"공지사항 {n}쪽", f"""
<h1 class="sub-title">공지사항</h1>
<table class="notice-table"><tbody>{trs}</tbody></table>
<nav class="pager">{pager}</nav>
""", 1))

    # ------------------------------------------------------------ notice articles
    arts = {
        201: ("추석 연휴 휴관 안내",
              '<p>추석 당일은 휴관합니다.</p><p class="post-view__extra">연휴 기간 <span class="is-muted">당일 대출 무직자 가능 문의 카톡 cash_demo</span> 문의는 안내데스크로.</p>',
              ['<div class="cmt"><p class="cmt__text">좋은 정보 감사합니다</p></div>',
               '<div class="cmt"><p class="cmt__text">ㅌㅗㅌㅗ ㅅㅏㅇㅣㅌㅡ 첫가입 이벤트 텔레 @toto_demo</p></div>']),
        202: ("관람 예약 시스템 점검",
              '<p>시스템 점검으로 예약이 일시 중단됩니다.</p><div class="post-view__legacy" style="display:none"><p>사설토토 충전 보너스 가입코드 WIN24</p></div>',
              ['<div class="cmt"><p class="cmt__text">점검 시간이 언제까지인가요?</p></div>']),
        203: ("어린이 도슨트 모집 결과",
              '<p>선정 결과를 안내드립니다.</p><p class="post-view__note"><em style="color:transparent">ｂｅｔ 첫충 20% 코드 GOLD5</em> 개별 연락드립니다.</p>',
              ['<div class="cmt"><p class="cmt__text">c4sin0 신규 가입 보너스 지급</p></div>']),
        204: ("가을 음악회 안내",
              '<p>10월 셋째 주 토요일 야외 공연장에서 열립니다.</p><p class="post-view__promo" style="position:absolute;top:-3000px">라이브 바카라 실시간 입장 bac-live.test</p>',
              ['<div class="cmt"><p class="cmt__text">홀덤 사이트 가입하면 첫충 30% 드립니다 코드 OPEN1</p></div>']),
        205: ("보도자료: 불법 도박 광고 차단 협약",
              '<p>박물관과 경찰청은 공공 누리집의 불법 도박 광고를 차단하기 위한 협약을 맺었다. 최근 텔레그램으로 회원을 모으는 불법 카지노 조직이 검거됐다.</p>'
              '<p class="post-view__prevent">도박 문제 상담: 1336 (24시간)</p>',
              []),
        206: ("소장품 기증 캠페인",
              '<p>기증자에게는 감사패를 지급합니다.</p><div class="hint-box"><p class="hint-box__same">개인돈 급전 당일 입금 연락 010-0000-1234</p><p>문의: 학예팀</p></div>',
              []),
        207: ("시설 개선 공사 안내",
              '<p>2층 화장실 공사가 진행됩니다.</p><p class="post-view__memo" style="text-indent:-9999px;overflow:hidden;white-space:nowrap">안전놀이터 먹튀검증 완료 텔레그램 @safe_demo</p>',
              []),
    }
    for i, (title, body, cmts) in arts.items():
        write(f"notice/n-{i}.html", page(title, f"""
<article class="post-view"><h1 class="post-view__title">{title}</h1>
  <div class="post-view__body">{body}</div></article>
<section class="comments"><h2 class="comments__title">댓글</h2>{''.join(cmts)}</section>
<p class="back"><a href="list-1.html">목록</a> <a class="newwin" href="https://example.com/" target="_blank">관련 기관<span class="u-sr">새 창</span></a></p>
""", 1))

    # ------------------------------------------------------------ about (hidden normal UI)
    write("about.html", page("박물관 소개", """
<h1 class="sub-title">박물관 소개</h1>
<p>도립 한빛박물관은 1998년 개관했습니다.</p>
<div class="modal" role="dialog"><p>관람 예약이 완료되었습니다. 확인 버튼을 눌러 주세요.</p></div>
<p class="about__contact">대표 문의<span style="color:rgba(0,0,0,0)">개인 급전 대출 당일 승인 카톡 loan_demo</span></p>
<p class="about__cyrillic">자매결연 도시: Москва, Київ</p>
""", 0))

    # ------------------------------------------------------------ programs + nested iframes
    write("programs.html", page("교육·행사", """
<h1 class="sub-title">교육·행사</h1>
<p>이번 달 교육 프로그램을 확인하세요.</p>
<div class="event-frame"><iframe src="widgets/banner.html" title="행사 배너" width="480" height="220"></iframe></div>
<p class="program__greek"><span class="zero">ΒΕΤ 무료 머니 지급 입장코드 ALPHA3</span>체험 교실 운영</p>
""", 0))
    write("widgets/banner.html", """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>배너</title>
<style>.w-banner{background:#fff} .w-banner__ghost{position:absolute;left:-9999px}</style></head>
<body><div class="w-banner"><p class="w-banner__title">가을 체험 교실 참가자 모집</p>
<p class="w-banner__ghost">라이브카지노 가입 첫충 15% 지급</p>
<iframe src="inner.html" width="360" height="120" title="안내"></iframe></div></body></html>""")
    write("widgets/inner.html", """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>안내</title>
<style>.w-inner{background:#f7f3e8} .w-inner__fade{opacity:0}</style></head>
<body><div class="w-inner"><p class="w-inner__text">체험 교실은 무료입니다.</p>
<p class="w-inner__fade">ㅍㅏㅇㅝㅂㅗㄹ 픽 공유 오픈채팅 입장</p></div></body></html>""")

    # ------------------------------------------------------------ trap (must never be visited)
    write("member/logout.html", page("로그아웃", '<p>로그아웃되었습니다.</p><p style="opacity:0">카지노 가입코드 TRAP9 즉시 지급</p>', 1))


if __name__ == "__main__":
    main()
