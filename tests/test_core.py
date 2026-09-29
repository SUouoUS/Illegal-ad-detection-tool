import json
from datetime import datetime, timedelta

import pytest

from clearweb import result_writer
from clearweb.detect import adjudge, obfuscation
from clearweb.textnorm import restore
from clearweb.urlnorm import clean, compare_key


# ---------------------------------------------------------------- URL rules
def test_query_is_kept_and_sorted():
    assert clean("http://A.go.kr/view?name=test&id=1024#top") == "http://a.go.kr/view?id=1024&name=test"
    assert compare_key("https://a.go.kr/view?id=1024") != compare_key("https://a.go.kr/view?id=1180")


def test_compare_key_ignores_scheme_slash_fragment():
    assert compare_key("http://a.go.kr/board/") == compare_key("https://a.go.kr/board#x")
    assert compare_key("http://a.go.kr/v?id=1&name=t") == compare_key("https://a.go.kr/v?name=t&id=1")


def test_tracking_like_params_are_not_dropped():
    assert "utm_source=x" in clean("http://a.kr/p?utm_source=x&id=3")


# ---------------------------------------------------------------- restoration
@pytest.mark.parametrize("raw,expected", [
    ("ㅋㅏㅈㅣㄴㅗ ㅂㅗㄴㅓㅅㅡ", "카지노 보너스"),
    ("ㅌ ㅗ ㅌ ㅗ 사이트", "토토 사이트"),
    ("ㅇㅏㄴㅈㅓㄴㄴㅗㄹㅇㅣㅌㅓ", "안전놀이터"),
    ("먹튀", "먹튀"),
    ("ｍｅｇａ－ＢＥＴ", "mega－BET"),
    ("саsinо", "casino"),
    ("c4sino", "casino"),
])
def test_restore(raw, expected):
    assert restore(raw).text == expected


@pytest.mark.parametrize("raw", ["ㅋㅋㅋ 좋은 정보 ㅎㅎ", "ㅠㅠ 슬퍼요", "공지（필독）３０％", "@promo_777"])
def test_restore_leaves_normal_text(raw):
    assert restore(raw).text == raw


def test_raw_text_is_preserved():
    r = restore("ㅋㅏㅈㅣㄴㅗ")
    assert r.raw == "ㅋㅏㅈㅣㄴㅗ" and r.changes and r.changes[0].kind == "jamo"


# ---------------------------------------------------------------- ad judgement
def J(t, **kw):
    return adjudge(t, restore(t).text, **kw)


@pytest.mark.parametrize("t", [
    "ㅋㅏㅈㅣㄴㅗ ㅂㅗㄴㅓㅅㅡ 코드 LUCKY7",
    "ｍｅｇａ－ＢＥＴ 첫충 30％ 지급",
    "먹튀검증 안전놀이터 상담 텔레그램 @promo_777",
    "무료 슬롯 체험 바로가기",
])
def test_official_examples_are_ads(t):
    assert J(t, concealed=True).is_ad


@pytest.mark.parametrize("t", [
    "불법 도박은 범죄입니다. 도박 문제는 1336에서 무료로 상담하세요.",
    "경찰, 불법 온라인 카지노 조직 검거… 텔레그램으로 회원 모집",
    "사설토토는 불법입니다. 발견 시 112 신고 바랍니다.",
    "본문 바로가기",
    "새창 열림",
    "청소년 도박 예방 교육 자료 안내",
    "오피스텔 매매 안내",
    "외화 환전 서비스 운영 시간",
])
def test_benign_texts_are_not_ads(t):
    assert not J(t, concealed=True).is_ad


def test_homoglyph_needs_letter_disguise():
    assert "HOMOGLYPH" in obfuscation("ｍｅｇａ－ＢＥＴ 첫충 30％ 지급", True)
    assert "HOMOGLYPH" not in obfuscation("공지（필독）３０％ 할인", True)
    assert "HOMOGLYPH" not in obfuscation("Русский", False)


def test_jamo_needs_real_composition():
    assert "JAMO" in obfuscation("ㅋㅏㅈㅣㄴㅗ 가입", True)
    assert "JAMO" not in obfuscation("ㅋㅋㅋ 카지노 가입", True)


# ---------------------------------------------------------------- result file
def _doc(findings):
    t0 = datetime(2026, 10, 24, 10, 0, 0).astimezone()
    return result_writer.build("https://x.go.kr/", t0, t0 + timedelta(seconds=12.5), findings)


def test_zero_findings_written(tmp_path):
    p = result_writer.write(_doc([]), tmp_path / "result.json")
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    d = json.loads(raw.decode("utf-8"))
    assert d["findings"] == [] and set(d) == {"meta", "findings"}
    assert d["meta"]["topic"] == "TOPIC" and d["meta"]["elapsed_sec"] == 12.5


def test_duplicates_merged_and_ids_unique(tmp_path):
    f = {"url": "https://x.go.kr/v?id=1", "location": "div.a > span", "technique": "JAMO", "evidence_text": "e"}
    d = _doc([f, dict(f), {**f, "technique": "TRANSPARENT"}])
    assert len(d["findings"]) == 2
    assert len({x["id"] for x in d["findings"]}) == 2
    assert all(x["is_violation"] is True for x in d["findings"])
    assert result_writer.validate(d) == []


def test_invalid_technique_rejected():
    d = _doc([])
    d["findings"].append({"id": "f_1", "url": "u", "is_violation": True, "location": "a", "evidence_text": "", "technique": "ETC"})
    assert result_writer.validate(d)
