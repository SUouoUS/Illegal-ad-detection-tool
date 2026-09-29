"""Content boundaries and evidence types (regression for the hidden news-list false positive)."""
from clearweb.detect import adjudge, analyze_frame
from clearweb.textnorm import restore


def J(t, **kw):
    return adjudge(t, restore(t).text, **kw)


# ---------------------------------------------------------------- evidence types
def test_letters_digits_names_are_not_contact_or_code():
    for t in ["‘SWC2026’ 유럽 컵, MALIGNANT 정상 등극", "RTX5090 탑재 노트북 출시", "LCK2026 결승 티켓 오픈"]:
        v = J(t, concealed=True)
        assert not v.contact and not v.code, t


def test_explicit_codes_and_identifiers():
    assert J("토토 가입코드 BET2026 입력 시 첫충 20% 지급").code
    assert J("ㅋㅏㅈㅣㄴㅗ ㅂㅗㄴㅓㅅㅡ 코드 LUCKY7").is_ad
    assert any("loan77" in c for c in J("급전 당일대출 카톡 loan77").contact)
    assert "@safe_toto" in " ".join(J("안전놀이터 텔레그램 @safe_toto").contact)
    assert not any("go.kr" in c for c in J("문의: help@example.go.kr").contact)   # e-mail is not a handle/domain


def test_hiding_alone_does_not_lower_the_threshold():
    assert not J("카지노", concealed=True).is_ad            # a hidden menu word
    assert J("ㅋㅏㅈㅣㄴㅗ", obfuscated=True).is_ad          # a disguised word is evidence


def test_single_news_items_are_not_ads():
    for t in ["게임사, ‘50억 원 규모 현금배당’ 사전 예고", "결승전 진출 8인에게 포상 휴가 지급",
              "LCK2026 결승 티켓 오픈, 배당률 예측 이벤트는 없어", "게임사 주주총회에서 현금배당 결정, 임직원 성과 보너스 지급"]:
        assert not J(t, concealed=True).is_ad, t


# ---------------------------------------------------------------- analysis units
def node(i, p, tag, text, cls="", inline=False, href="", signals=None):
    return {"i": i, "p": p, "tag": tag, "cls": cls, "inline": inline, "text": text, "href": href,
            "signals": signals or [], "rect": None, "own": True, "id": ""}


HID = [{"technique": "OFFSCREEN", "kind": "display-none", "root": 0, "detail": "display:none (ul)"}]


def hidden_list(items):
    nodes = [node(0, -1, "ul", " ".join(items), "list", signals=HID)]
    for k, t in enumerate(items):
        li, a = 1 + 2 * k, 2 + 2 * k
        nodes.append(node(li, 0, "li", t, "list_item", signals=HID))
        nodes.append(node(a, li, "a", t, "subject", inline=True, href=f"http://x/news/{k}", signals=HID))
    return nodes


def test_evidence_from_different_items_is_not_combined():
    items = ["게임사, ‘50억 원 규모 현금배당’ 사전 예고", "결승전 진출 8인에게 포상 휴가 지급", "‘SWC2026’ 유럽 컵 정상 등극"]
    cands, _ = analyze_frame(hidden_list(items))
    assert cands == []


def test_one_ad_item_among_news_is_found_at_the_item():
    items = ["인디 게임 페스티벌 수상작 발표", "바카라 무료체험 가입코드 VIP7 바로가기", "e스포츠 리그 개막전 일정 안내"]
    cands, _ = analyze_frame(hidden_list(items))
    assert [(c.index, c.technique) for c in cands] == [(4, "OFFSCREEN")]      # the <a> of item 2


def test_sentence_split_over_inline_pieces_is_one_unit():
    sig = [{"technique": "OFFSCREEN", "kind": "outside-document", "root": 0, "detail": "left:-9999px"}]
    text = "카지노 첫충 30% 문의 텔레 @casino_kr"
    nodes = [node(0, -1, "p", text, "promo-split", signals=sig),
             node(1, 0, "span", "카", inline=True, signals=sig),
             node(2, 0, "b", "지노", inline=True, signals=sig),
             node(3, 0, "span", "첫충 30%", inline=True, signals=sig),
             node(4, 0, "em", "문의 텔레 @casino_kr", inline=True, signals=sig)]
    cands, _ = analyze_frame(nodes)
    assert {(c.index, c.technique) for c in cands} == {(0, "OFFSCREEN")}


def test_hidden_piece_inside_visible_sentence_stays_the_unit():
    own = [{"technique": "TRANSPARENT", "kind": "opacity", "root": 1, "detail": "opacity:0"}]
    nodes = [node(0, -1, "p", "시는 점검 결과를 발표했다. 먹튀검증 안전놀이터 상담 텔레그램 @promo_777 점검은 계속된다.", "desc"),
             node(1, 0, "span", "먹튀검증 안전놀이터 상담 텔레그램 @promo_777", "promo-note", inline=True, signals=own)]
    cands, _ = analyze_frame(nodes)
    assert [(c.index, c.technique) for c in cands] == [(1, "TRANSPARENT")]
