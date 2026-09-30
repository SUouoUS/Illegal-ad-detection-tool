"""Text in images: layout grouping, per-image judgement, result_extra schema, and the
Windows OCR engine on committed test images (skipped where the engine is unavailable)."""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from clearweb import ocr, result_writer
from clearweb.detect import judge_image

IMG = Path(__file__).resolve().parent.parent / "testsites" / "ocr" / "img"


def word(t, x, y, w=None, h=30):
    return {"text": t, "x": x, "y": y, "w": w or 20 * len(t), "h": h}


def test_close_lines_form_one_block_far_captions_do_not():
    lines = [{"text": "카지노 첫충", "words": [word("카지노", 20, 20), word("첫충", 90, 20)]},
             {"text": "가입코드 TEST77", "words": [word("가입코드", 20, 60), word("TEST77", 110, 60)]},
             {"text": "오늘의 운세", "words": [word("오늘의", 400, 300), word("운세", 470, 300)]}]
    bl = ocr.blocks(lines)
    assert [b["text"] for b in bl] == ["카지노 첫충 가입코드 TEST77", "오늘의 운세"]


def test_words_far_apart_on_one_row_are_split():
    lines = [{"text": "카지노 오늘의날씨", "words": [word("카지노", 10, 10), word("오늘의날씨", 600, 10)]}]
    assert [b["text"] for b in ocr.blocks(lines)] == ["카지노", "오늘의날씨"]


def B(*texts):
    return [{"text": t} for t in texts]


def test_image_levels():
    assert judge_image(B("카지노 첫충 30% 지급 가입코드 TEST77"))["level"] == "suspect"
    assert judge_image(B("온라인 카지노", "오늘의 운세", "가입코드 TEST77"))["level"] == "uncertain"   # only when joined
    assert judge_image(B("카지노"))["level"] == "uncertain"                                      # topic word alone
    for normal in (B("실손보험 가입 이벤트", "보험료 10% 할인 상담 신청"), B("AI 비서 서비스 출시", "지금 무료 체험하고 혜택 받으세요"),
                   B("게임사, 50억 원 규모 현금배당 예고", "결승 진출 선수에게 포상 휴가 지급"), B("불법 도박은 범죄입니다", "도박문제 상담 1336")):
        assert judge_image(normal)["level"] == "none", normal


def test_ocr_shapes_are_never_jamo_or_homoglyph_findings():
    v = judge_image(B("ㅋㅏㅈㅣㄴㅗ 가입코드 TEST77"))
    assert "은닉 기법으로 판단하지 않음" in v["note"]


def test_result_extra_schema(tmp_path):
    t0 = datetime(2026, 10, 24, 10, 0).astimezone()
    doc = result_writer.build_extra("http://x/", t0, t0 + timedelta(seconds=3), [
        {"url": "http://x/", "location": "figure.a > img", "evidence_text": "OCR 추출문: …", "extra_finding": "IMAGE_TEXT_AD"},
        {"url": "http://x/", "location": "figure.a > img", "evidence_text": "dup", "extra_finding": "IMAGE_TEXT_AD"}])
    assert len(doc["findings"]) == 1 and doc["findings"][0]["technique"] == "ETC"
    assert result_writer.validate_extra(doc) == []
    p = result_writer.write_extra(doc, tmp_path / "result_extra.json")
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")


def test_prepare_limits():
    lim = ocr.OcrLimits()
    big = ocr.prepare((IMG / "big.png").read_bytes(), lim)
    assert max(big.width, big.height) == lim.max_side and big.orig_size == (6000, 3000)
    with pytest.raises(ValueError):
        ocr.prepare(b"not an image", lim)


engine_ok, engine_msg = ocr.engine_status()


@pytest.mark.skipif(not engine_ok, reason=f"OCR engine unavailable: {engine_msg}")
def test_windows_ocr_reads_committed_images():
    lim = ocr.OcrLimits()

    async def read(name):
        return " ".join(line["text"] for line in await ocr.recognize(ocr.prepare((IMG / name).read_bytes(), lim).png))

    ko = asyncio.run(read("ad_ko.png"))
    assert "카지노" in ko and "TEST77" in ko
    assert "도박" in asyncio.run(read("prevention.png"))


# ---------------------------------------------------------------- collection range, limits, stop
def _scan_with_fake_ocr(monkeypatch, tmp_path, **limits):
    from clearweb import scanner
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    monkeypatch.setattr(scanner.ocr, "engine_status", lambda: (True, "fake engine"))
    cfg = scanner.ScanConfig(ocr_enabled=True)
    for k, v in limits.items():
        setattr(cfg.ocr_limits, k, v)
    sc = scanner.Scan("http://x/", cfg, tmp_path)
    sc._t0 = __import__("time").monotonic()

    async def fake_one(ctx, item, img_dir):
        item.update(level="none", ocr_text="", verdict={"level": "none"})

    monkeypatch.setattr(sc, "_ocr_one", fake_one)
    return sc


def _cand(page, sel, chain=()):
    return {"k": 0, "url": f"http://x/img/{sel}.png", "selector": sel, "page_url": page, "frame_chain": list(chain), "frame_url": page}


def test_images_of_sub_pages_and_frames_keep_their_own_location(monkeypatch, tmp_path):
    sc = _scan_with_fake_ocr(monkeypatch, tmp_path)
    chain = ['iframe[src="http://x/f.html"]']
    asyncio.run(sc._ocr_stage(None, {"url": "http://x/", "depth": 0}, [_cand("http://x/", "img.a")]))
    asyncio.run(sc._ocr_stage(None, {"url": "http://x/sub", "depth": 1},
                              [_cand("http://x/sub", "img.a"), _cand("http://x/sub", "img.b", chain), _cand("http://x/sub", "img.a")]))
    locs = [(i["page_url"], i["location"]) for i in sc.images]
    assert locs == [("http://x/", "img.a"), ("http://x/sub", "img.a"), ("http://x/sub", 'iframe[src="http://x/f.html"] >>> img.b')]
    assert sc.ocr_record["coverage"]["pages_collected"] == 2


def test_image_limits_are_per_page_and_per_scan(monkeypatch, tmp_path):
    sc = _scan_with_fake_ocr(monkeypatch, tmp_path, max_images=2, max_total_images=3)
    asyncio.run(sc._ocr_stage(None, {"url": "http://x/", "depth": 0}, [_cand("http://x/", f"img.a{k}") for k in range(3)]))
    asyncio.run(sc._ocr_stage(None, {"url": "http://x/b", "depth": 1}, [_cand("http://x/b", f"img.b{k}") for k in range(2)]))
    assert [i["status"] for i in sc.images] == ["ok", "ok", "skipped", "ok", "skipped"]
    assert "페이지당" in sc.images[2]["reason"] and "전체 이미지 수" in sc.images[4]["reason"]


def test_stop_and_unloaded_pages_are_recorded_not_hidden(monkeypatch, tmp_path):
    sc = _scan_with_fake_ocr(monkeypatch, tmp_path)
    asyncio.run(sc._ocr_stage(None, {"url": "http://x/blocked", "status": "blocked", "error": "보안 확인"}, None))
    assert sc.ocr_record["not_collected"][0]["url"] == "http://x/blocked" and not sc.images
    sc.cancel.set()
    asyncio.run(sc._ocr_stage(None, {"url": "http://x/", "depth": 0}, [_cand("http://x/", "img.a")]))
    assert sc.images[0]["status"] == "stopped"
