"""Score OCR image results in a scan record against testsites/ocr/expected.json.

Unit: one <img> element of the test page = one case (not one image file; the same file
used by two elements counts twice). Cases marked "limit" in expected.json are listed but
not counted.
* judgement: counted for every non-limit case; success when the recorded level
  (suspect / uncertain / none, or "failed" when the image could not be loaded) equals
  the expected level.
* extraction: counted only for non-limit cases that have expected keywords; success when
  ALL keywords appear in the OCR text (whitespace and case ignored). This measures whether
  the words needed for the judgement were read, not character accuracy.
usage: python tools/score_ocr.py [runs/<id>/scan.json]
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def main() -> int:
    rec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(sorted(glob.glob(str(ROOT / "runs/*/scan.json")))[-1])
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    exp = json.loads((ROOT / "testsites/ocr/expected.json").read_text(encoding="utf-8"))["images"]
    o = rec.get("ocr", {})
    print(f"record {rec_path.parent.name} | engine: {o.get('engine_detail', '-')} | total {o.get('total_sec')}s "
          f"(OCR {o.get('ocr_sec')}s, download {o.get('download_sec')}s, reused {o.get('cache_hits')})")
    ext_ok = ext_n = lvl_ok = lvl_n = 0
    for im in rec.get("images", []):
        name = next((k for k in exp if im["img_url"].endswith(k)), None)
        if not name:
            continue
        e = exp[name]
        text = im.get("ocr_text", "")
        got = im.get("level") or im["status"]
        if im["status"] in ("failed", "timeout", "skipped", "stopped"):
            got = "failed" if im["status"] == "failed" else im["status"]
        kw_ok = all(norm(k) in norm(text) for k in e["keywords"]) if e["keywords"] else None
        tag = "한계" if e.get("limit") else ""
        if not e.get("limit"):
            if kw_ok is not None:
                ext_n += 1
                ext_ok += bool(kw_ok)
            lvl_n += 1
            lvl_ok += got == e["level"]
        mark = "OK " if got == e["level"] else "XX "
        print(f"{mark}{name:22s} {im['selector'][:34]:34s} extract={'-' if kw_ok is None else ('yes' if kw_ok else 'NO')} "
              f"level={got:9s} (expect {e['level']}) {tag} {im.get('reason', '')[:40]} {'[재사용]' if im.get('cache_hit') else ''}"
              f"\n      OCR: {text.replace(chr(10), ' / ')[:90]}")
    print(f"\nextraction: {ext_ok}/{ext_n}   judgement: {lvl_ok}/{lvl_n}   "
          "(unit: img element; limit cases excluded; extraction only where keywords are expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
