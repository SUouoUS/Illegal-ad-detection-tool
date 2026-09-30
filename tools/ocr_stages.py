"""Where does each expected image drop out? (diagnosis from an existing scan record)

Stages, per <img> element expected by expected.json:
  1 not found      no record for the element (page/frame not scanned for images)
  2 not loaded     found, but download/decoding failed, skipped, timed out or stopped
  3 not read       loaded, but the expected keywords are not in the OCR text
  4 not judged     keywords read, but the level is lower than expected
  5 not linked     judged "suspect", but missing from result_extra.json
  ok               level as expected (and linked when suspect)
"limit" cases are listed but counted separately. "over" = level higher than expected.

usage: python tools/ocr_stages.py [runs/<id>/scan.json] [--expected testsites/ocr/expected.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANK = {"none": 0, "uncertain": 1, "suspect": 2}
LABEL = {1: "1 후보 미발견", 2: "2 내려받기·로딩 실패", 3: "3 글자 추출 실패", 4: "4 판정 근거 부족",
         5: "5 결과 연결 누락", "ok": "정상", "over": "기대보다 높은 판정", "failed_ok": "정상(로드 실패를 실패로 기록)"}


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def stage_of(im: dict, e: dict, extra_keys: set) -> object:
    want = e["level"]
    if im["status"] != "ok":
        return "failed_ok" if want == "failed" and im["status"] == "failed" else 2
    if e["keywords"] and not all(norm(k) in norm(im.get("ocr_text", "")) for k in e["keywords"]):
        return 3
    got = im.get("level", "none")
    if want in RANK and RANK[got] < RANK[want]:
        return 4
    if want in RANK and RANK[got] > RANK[want]:
        return "over"
    if got == "suspect" and (im["page_url"], im.get("location") or im["selector"]) not in extra_keys:
        return 5
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record", nargs="?")
    ap.add_argument("--expected", default=str(ROOT / "testsites/ocr/expected.json"))
    a = ap.parse_args()
    rec_path = Path(a.record) if a.record else Path(sorted(glob.glob(str(ROOT / "runs/*/scan.json")))[-1])
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    exp = json.loads(Path(a.expected).read_text(encoding="utf-8"))["images"]
    extra_keys = set()
    xp = rec.get("result_extra_path")
    if xp and Path(xp).is_file():
        extra_keys = {(f["url"], f["location"]) for f in json.loads(Path(xp).read_text(encoding="utf-8"))["findings"]}

    o, cov = rec.get("ocr", {}), rec.get("ocr", {}).get("coverage", {})
    pages = rec.get("pages", [])
    print(f"record {rec_path.parent.name} | pages visited {len(pages)} | OCR ran: {o.get('ran')} | "
          f"image records {len(rec.get('images', []))} | OCR stage {o.get('total_sec')}s")
    if cov:
        print(f"coverage: {json.dumps(cov, ensure_ascii=False)}")
    total, limits = Counter(), Counter()
    by_where: dict[str, Counter] = {}
    for name, e in exp.items():
        recs = [im for im in rec.get("images", []) if im["img_url"].endswith(name)]
        want_n = e.get("count", 1)
        stages = [stage_of(im, e, extra_keys) for im in recs] + [1] * max(0, want_n - len(recs))
        for st in stages:
            (limits if e.get("limit") else total)[st] += 1
            by_where.setdefault(e.get("where", "entry"), Counter())[st if not e.get("limit") else "limit"] += 1
        worst = [s for s in stages if s not in ("ok", "failed_ok")]
        if worst or e.get("limit"):
            text = (recs[0].get("ocr_text", "") if recs else "").replace("\n", " / ")[:60]
            why = (recs[0].get("reason") or (recs[0].get("verdict") or {}).get("note", "")) if recs else "기록 없음"
            print(f"  {name:22s} [{e.get('where', 'entry'):6s}] 기대 {e['level']:9s} → "
                  f"{', '.join(LABEL[s] for s in stages)}{' (한계 사례)' if e.get('limit') else ''} | {why[:40]} | OCR: {text}")
    print("\n단계별 합계(한계 사례 제외, 단위: img 요소):")
    for k in (1, 2, 3, 4, 5, "over", "ok", "failed_ok"):
        if total[k]:
            print(f"  {LABEL[k]:24s} {total[k]}")
    print("위치별:", {w: {str(LABEL.get(k, k)): v for k, v in c.items()} for w, c in by_where.items()})
    if limits:
        print("한계 사례:", {str(LABEL.get(k, k)): v for k, v in limits.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
