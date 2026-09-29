"""Score a result.json against an answer key, following the official rules.

* A finding is correct only when url, location and technique all match an answer.
* URL comparison ignores scheme, trailing '/', and '#...'; query values matter, order doesn't.
* location is compared by the element it resolves to in a real browser (not by text).
  A selector matching 0 or 2+ elements is wrong.
* Duplicate (url, location, technique) submissions count once.
* Schema-invalid findings are excluded (reported separately).

usage: python tools/score.py --answers testsites/dev/answers.json --result result.json --base http://127.0.0.1:8811/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clearweb.result_writer import TECHNIQUES, validate  # noqa: E402
from clearweb.urlnorm import compare_key  # noqa: E402
from clearweb.scanner import EXPAND_JS, QUIET_JS  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

FIND_IFRAME_JS = """([sel]) => {
  const m = sel.match(/^(.*?)(i?frame)\\[src="((?:[^"\\\\]|\\\\.)*)"\\](.*)$/);
  if (!m) return -1;
  const [, prefix, tag, src, rest] = m;
  const want = src.replace(/\\\\"/g, '"');
  let cands = Array.from(document.querySelectorAll(tag));
  cands = cands.filter(f => { const a = f.getAttribute('src'); try { return a && new URL(a, document.baseURI).href === want; } catch (e) { return false; } });
  if (prefix.trim() || rest.trim()) {
    cands = cands.filter(f => { f.setAttribute('data-score-probe', '1'); const q = `${prefix}${tag}[data-score-probe="1"]${rest}`; let ok = false; try { ok = Array.from(document.querySelectorAll(q)).includes(f); } catch (e) {} f.removeAttribute('data-score-probe'); return ok; });
  }
  window.__scoreHit = cands;
  return cands.length;
}"""

MARK_JS = """(el) => { window.__sid = window.__sid || 0; if (!el.__sid) el.__sid = ++window.__sid; return el.__sid; }"""


async def resolve(page, location: str):
    """Return (identity, error). identity is a tuple of per-level element ids."""
    parts = [p.strip() for p in location.split(">>>")]
    frame = page.main_frame
    ident = []
    for k, part in enumerate(parts):
        last = k == len(parts) - 1
        try:
            els = await frame.query_selector_all(part)
        except Exception as e:  # noqa: BLE001
            els, err = [], str(e).splitlines()[0]
        if not last and len(els) != 1:
            n = await frame.evaluate(FIND_IFRAME_JS, [part])
            if n == 1:
                els = [await frame.evaluate_handle("window.__scoreHit[0]")]
            elif n > 1:
                return None, f"iframe 선택자 '{part}'가 {n}개 요소와 일치"
        if len(els) == 0:
            return None, f"'{part}' 일치 요소 없음"
        if len(els) > 1:
            return None, f"'{part}'가 {len(els)}개 요소와 일치(특정 불가)"
        el = els[0]
        ident.append((frame.url, await el.evaluate(MARK_JS)))
        if not last:
            frame = await el.as_element().content_frame()
            if frame is None:
                return None, f"'{part}' 프레임 내용 없음"
    return tuple(ident), None


async def expand(page):
    """Reveal click-only content ('댓글 더보기') the same way the scanner does, so answers
    that exist only after expansion can be resolved."""
    for _ in range(4):
        try:
            labels = await page.main_frame.evaluate(EXPAND_JS)
        except Exception:  # noqa: BLE001
            return
        if not labels:
            return
        for k in range(len(labels)):
            try:
                h = await page.main_frame.evaluate_handle(
                    "k => { const e = window.__cwExpand[k]; window.__cwClicked.add(e); return e; }", k)
                await h.as_element().click(timeout=2000, no_wait_after=True)
                await page.wait_for_load_state("networkidle", timeout=1500)
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(300)


async def open_page(ctx, url):
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="load", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    for f in page.frames:
        try:
            await f.evaluate("document.querySelectorAll('iframe[loading=lazy]').forEach(f => f.loading = 'eager')")
        except Exception:  # noqa: BLE001
            pass
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(300)
    try:
        await page.main_frame.evaluate(QUIET_JS, [600, 3000])
    except Exception:  # noqa: BLE001
        pass
    await expand(page)
    return page


async def score(answers_path: Path, result_path: Path, base: str) -> dict:
    ans_doc = json.loads(answers_path.read_text(encoding="utf-8"))
    raw = result_path.read_bytes()
    report: dict = {"bom": raw.startswith(b"\xef\xbb\xbf")}
    res = json.loads(raw.decode("utf-8-sig"))
    report["schema_errors"] = validate(res)

    def sub(s: str) -> str:
        return s.replace("{BASE}", base)

    answers = [{**a, "url": urljoin(base, sub(a["url"])), "location": sub(a["location"])} for a in ans_doc["answers"]]
    findings = []
    invalid = []
    for f in res.get("findings", []):
        if not all(k in f for k in ("id", "url", "is_violation", "location", "evidence_text", "technique")) \
                or f.get("technique") not in TECHNIQUES or not isinstance(f.get("location"), str):
            invalid.append(f)
            continue
        findings.append(f)

    pages = defaultdict(list)
    for a in answers:
        pages[compare_key(a["url"])].append(("a", a))
    for f in findings:
        pages[compare_key(f["url"])].append(("f", f))

    ans_keys = {}
    sub_keys = {}
    fp_detail = []
    unresolved_answers = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        for key, items in pages.items():
            url = items[0][1]["url"]
            page = await open_page(ctx, url)
            for kind, it in items:
                ident, err = await resolve(page, it["location"])
                if kind == "a":
                    if ident is None:
                        unresolved_answers.append({**it, "error": err})
                        continue
                    ans_keys[(key, ident, it["technique"])] = it
                else:
                    k = (key, ident, it["technique"]) if ident else (key, ("UNRESOLVED", it["location"]), it["technique"])
                    if k not in sub_keys:
                        sub_keys[k] = {**it, "resolve_error": err}
            await page.close()
        await browser.close()

    tp = [k for k in sub_keys if k in ans_keys]
    fp = [k for k in sub_keys if k not in ans_keys]
    fn = [k for k in ans_keys if k not in sub_keys]
    for k in fp:
        f = sub_keys[k]
        why = f["resolve_error"] or "정답에 없는 요소·기법 조합"
        if not f["resolve_error"]:
            same_el = [a for (pk, ident, t), a in ans_keys.items() if pk == k[0] and ident == k[1]]
            if same_el:
                why = f"요소는 맞으나 기법 불일치(정답: {', '.join(a['technique'] for a in same_el)})"
        fp_detail.append({"url": f["url"], "location": f["location"], "technique": f["technique"],
                          "evidence_text": f["evidence_text"][:80], "why": why})

    by_tech = {}
    for t in sorted(TECHNIQUES):
        a_n = sum(1 for k in ans_keys if k[2] == t)
        s_n = sum(1 for k in sub_keys if k[2] == t)
        t_n = sum(1 for k in tp if k[2] == t)
        by_tech[t] = {"answers": a_n, "submitted": s_n, "correct": t_n}

    n_sub = len(sub_keys)
    report.update({
        "answers": len(ans_keys) + len(unresolved_answers),
        "submitted_valid_unique": n_sub,
        "submitted_raw": len(res.get("findings", [])),
        "invalid_items": len(invalid),
        "duplicates_merged": len(findings) - n_sub,
        "correct": len(tp),
        "precision": round(len(tp) / n_sub, 4) if n_sub else None,
        "recall": round(len(tp) / (len(ans_keys) + len(unresolved_answers)), 4) if answers else None,
        "elapsed_sec": res.get("meta", {}).get("elapsed_sec"),
        "by_technique": by_tech,
        "missed": [{"url": ans_keys[k]["url"], "location": ans_keys[k]["location"], "technique": k[2],
                    "note": ans_keys[k].get("note", "")} for k in fn],
        "false_positives": fp_detail,
        "unresolved_answers": unresolved_answers,
    })
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", type=Path, required=True)
    ap.add_argument("--result", type=Path, default=Path("result.json"))
    ap.add_argument("--base", required=True)
    ap.add_argument("--json", action="store_true", help="print machine-readable report")
    a = ap.parse_args()
    rep = asyncio.run(score(a.answers, a.result, a.base))
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    print(f"BOM: {rep['bom']}  schema errors: {len(rep['schema_errors'])}  invalid items: {rep['invalid_items']}")
    print(f"answers {rep['answers']} | submitted {rep['submitted_valid_unique']} (raw {rep['submitted_raw']}, dup merged {rep['duplicates_merged']}) | correct {rep['correct']}")
    print(f"precision {rep['precision']}  recall {rep['recall']}  elapsed {rep['elapsed_sec']}s")
    for t, v in rep["by_technique"].items():
        print(f"  {t:12s} answers {v['answers']:3d}  submitted {v['submitted']:3d}  correct {v['correct']:3d}")
    for m in rep["missed"]:
        print(f"  MISS  {m['technique']:11s} {m['location']}  {m['note']}")
    for f in rep["false_positives"]:
        print(f"  FP    {f['technique']:11s} {f['location']}  [{f['why']}]  {f['evidence_text']}")
    for u in rep["unresolved_answers"]:
        print(f"  ANSWER-UNRESOLVED {u['location']}: {u['error']}")
    for e in rep["schema_errors"]:
        print(f"  SCHEMA {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
