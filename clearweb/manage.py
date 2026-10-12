"""Result management for day-to-day use: review marks, CSV export and a printable report.

Review marks are the inspector's own decisions ("광고 확인" / "오탐" / "미검토" + memo). They are
kept apart in runs/<id>/reviews.json and never change the automatic result files
(result.json / result_extra.json), which stay exactly what the detector produced.
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
from datetime import datetime
from pathlib import Path

STATUSES = {"unreviewed": "미검토", "confirmed": "광고 확인", "false_positive": "오탐"}
TECH = {"HOMOGLYPH": "닮은꼴 글자 위장", "JAMO": "자모 분해", "TRANSPARENT": "투명 텍스트", "OFFSCREEN": "화면 밖 은닉"}


def official_key(url: str, location: str) -> str:
    return f"{url}\u0000{location}"


def extra_key(kind: str, url: str, location: str) -> str:
    return f"extra:{kind}\u0000{url}\u0000{location}"


def load_reviews(run_dir: Path) -> dict:
    f = run_dir / "reviews.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}


def save_review(run_dir: Path, key: str, status: str, memo: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("status")
    reviews = load_reviews(run_dir)
    if status == "unreviewed" and not memo.strip():
        reviews.pop(key, None)
    else:
        reviews[key] = {"status": status, "memo": memo.strip()[:1000],
                        "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    tmp = run_dir / "reviews.json.tmp"
    tmp.write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, run_dir / "reviews.json")
    return reviews


def rows(rec: dict, reviews: dict) -> list[dict]:
    """One row per element (official: techniques together) and per extra suspect."""
    out = []
    groups: dict[str, dict] = {}
    official = {(f["url"], f["location"], f["technique"]) for f in rec.get("result", {}).get("findings", [])}
    for f in rec.get("findings", []):
        if (f["url"], f["location"], f["technique"]) not in official:
            continue
        k = official_key(f["url"], f["location"])
        g = groups.setdefault(k, {"구분": "공식 결과(result.json)", "url": f["url"], "location": f["location"],
                                  "techniques": [], "evidence_text": f["evidence_text"],
                                  "restored_text": f.get("restored_text", ""), "reasons": f.get("ad_reasons", []), "key": k})
        if f["technique"] not in g["techniques"]:
            g["techniques"].append(f["technique"])
    out.extend(groups.values())
    kinds = {"text": "추가 의심(숨김 없음)", "seo": "추가 의심(검색엔진·속성 문구)", "link": "추가 의심(숨겨진 광고 링크)"}
    for x in rec.get("extra_ads", []):
        kind = x.get("kind", "text")
        k = extra_key(kind, x["url"], x["location"])
        out.append({"구분": kinds.get(kind, "추가 의심"), "url": x["url"], "location": x["location"], "techniques": [],
                    "evidence_text": x["evidence_text"], "restored_text": x.get("restored_text", ""),
                    "reasons": x.get("ad_reasons", []), "key": k})
    for im in rec.get("images", []):
        if im.get("level") not in ("suspect", "uncertain"):
            continue
        k = extra_key("image", im["page_url"], im.get("location") or im.get("selector", ""))
        out.append({"구분": "추가 의심(이미지 속 글자)" + ("" if im["level"] == "suspect" else " · 불확실"),
                    "url": im["page_url"], "location": im.get("location") or im.get("selector", ""), "techniques": [],
                    "evidence_text": (im.get("ocr_text") or "").replace("\n", " / "), "restored_text": "",
                    "reasons": [(im.get("verdict") or {}).get("note", "")], "key": k})
    for r in out:
        rv = reviews.get(r["key"], {})
        r["review"] = STATUSES.get(rv.get("status", "unreviewed"), "미검토")
        r["memo"] = rv.get("memo", "")
        r["reviewed_at"] = rv.get("at", "")
    return out


def to_csv(rec: dict, reviews: dict, cmp: dict | None = None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["번호", "구분", "은닉·위장 기법", "원문", "복원 문구", "광고로 의심한 이유", "페이지 주소", "위치(선택자)",
                "이전 검사 대비", "검토", "메모", "검토 시각"])
    new = set(cmp["new"]) if cmp else set()
    n = 0
    for n, r in enumerate(rows(rec, reviews), 1):
        w.writerow([n, r["구분"], ", ".join(TECH.get(t, t) for t in r["techniques"]), r["evidence_text"],
                    r["restored_text"] if r["restored_text"] != r["evidence_text"] else "",
                    " / ".join(x for x in r["reasons"] if x), r["url"], r["location"],
                    ("신규" if r["key"] in new else "이전에도 있음") if cmp else "", r["review"], r["memo"], r["reviewed_at"]])
    for r in (cmp or {}).get("gone", []):
        n += 1
        w.writerow([n, r["구분"], ", ".join(TECH.get(t, t) for t in r["techniques"]), r["evidence_text"], "", "",
                    r["url"], r["location"], "사라짐(이번 검사에서 없어짐)", "", "", ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")          # BOM so Excel reads Korean correctly


def report_html(rec: dict, reviews: dict, page_summary: str, cmp: dict | None = None) -> str:
    e = html.escape
    rs = rows(rec, reviews)
    new = set(cmp["new"]) if cmp else set()
    n_off = sum(1 for r in rs if r["구분"].startswith("공식"))
    cnt = {s: sum(1 for r in rs if r["review"] == s) for s in STATUSES.values()}
    items = []
    for n, r in enumerate(rs, 1):
        tech = ", ".join(TECH.get(t, t) for t in r["techniques"]) or "-"
        restored = (f"<div class='k'>복원 문구</div><div class='v'>{e(r['restored_text'])}</div>"
                    if r["restored_text"] and r["restored_text"] != r["evidence_text"] else "")
        memo = f"<div class='k'>메모</div><div class='v'>{e(r['memo'])}</div>" if r["memo"] else ""
        tag = "<span class='rv'>신규</span>" if r["key"] in new else ""
        items.append(f"""<section class="item"><h3>{n}. {e(r['구분'])} <span class="rv rv-{e(r['review'])}">{e(r['review'])}</span>{tag}</h3>
<div class="grid"><div class="k">은닉·위장 기법</div><div class="v">{e(tech)}</div>
<div class="k">원문</div><div class="v raw">{e(r['evidence_text'])}</div>{restored}
<div class="k">의심한 이유</div><div class="v">{e(' / '.join(x for x in r['reasons'] if x)) or '-'}</div>
<div class="k">페이지</div><div class="v">{e(r['url'])}</div>
<div class="k">위치</div><div class="v code">{e(r['location'])}</div>{memo}</div></section>""")
    meta = rec.get("result", {}).get("meta", {})
    cmp_row, gone_html = "", ""
    if cmp:
        cmp_row = (f"<tr><td>이전 검사 대비</td><td>{e((cmp.get('prev_started_at') or '')[:16].replace('T', ' '))} 검사와 비교: "
                   f"신규 {len(cmp['new'])} · 사라짐 {len(cmp['gone'])} · 확인 못 함 {len(cmp['unknown'])} · 그대로 {cmp['same']}</td></tr>")
        if cmp["gone"]:
            gone_html = "<h2>사라진 항목 (이전 검사에는 있었고 이번에 같은 페이지에서 없어짐)</h2>" + "".join(
                f"<section class='item'><div class='grid'><div class='k'>구분</div><div class='v'>{e(g['구분'])}</div>"
                f"<div class='k'>원문</div><div class='v raw'>{e(g['evidence_text'])}</div><div class='k'>페이지</div><div class='v'>{e(g['url'])}</div>"
                f"<div class='k'>위치</div><div class='v code'>{e(g['location'])}</div></div></section>" for g in cmp["gone"])
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>ClearWeb 점검 보고서 {e(rec.get('run_id', ''))}</title>
<style>
body{{font-family:"Pretendard","Malgun Gothic",sans-serif;color:#1d2530;margin:32px auto;max-width:960px;padding:0 16px;line-height:1.55;font-size:14px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #ccd3dc;padding-bottom:4px}}
h3{{font-size:15px;margin:0 0 8px}} .sub{{color:#5b6672;margin:0 0 16px}}
table.sum{{border-collapse:collapse;width:100%}} table.sum td{{border:1px solid #d8dee6;padding:6px 8px;vertical-align:top}}
table.sum td:first-child{{width:150px;background:#f4f6f9;font-weight:600}}
.item{{border:1px solid #d8dee6;border-radius:6px;padding:12px 14px;margin:10px 0;break-inside:avoid}}
.grid{{display:grid;grid-template-columns:120px 1fr;gap:4px 12px}} .k{{color:#5b6672}} .v{{word-break:break-all}}
.raw{{white-space:pre-wrap}} .code{{font-family:Consolas,monospace;font-size:12.5px}}
.rv{{font-size:12px;font-weight:600;padding:1px 8px;border-radius:10px;border:1px solid #9aa6b2;margin-left:6px}}
.rv-광고\\ 확인{{border-color:#b42318;color:#b42318}} .rv-오탐{{border-color:#5b6672;color:#5b6672}}
.note{{color:#5b6672;font-size:12.5px}} @media print{{body{{margin:0}} .noprint{{display:none}}}}
</style></head><body>
<p class="noprint"><button onclick="window.print()">인쇄 / PDF로 저장</button></p>
<h1>공공 웹사이트 불법광고 점검 보고서</h1><p class="sub">ClearWeb {e(str(rec.get('tool_version', '')))} · 작성 {e(datetime.now().strftime('%Y-%m-%d %H:%M'))}</p>
<h2>점검 개요</h2><table class="sum">
<tr><td>점검 대상</td><td>{e(rec.get('entry_url', ''))}</td></tr>
<tr><td>점검 시각</td><td>{e(meta.get('started_at', ''))} ~ {e(meta.get('finished_at', ''))} ({e(str(meta.get('elapsed_sec', '')))}초)</td></tr>
<tr><td>점검 결과</td><td>공식 결과 {n_off}개 요소(기법별 {len(rec.get('result', {}).get('findings', []))}건) · 추가 의심 {len(rs) - n_off}건</td></tr>
<tr><td>검토 현황</td><td>광고 확인 {cnt['광고 확인']} · 오탐 {cnt['오탐']} · 미검토 {cnt['미검토']}</td></tr>
<tr><td>점검 범위</td><td>{e(rec.get('end_reason', ''))}</td></tr>
<tr><td>페이지 처리</td><td>{e(page_summary or '모두 정상')}</td></tr>
{cmp_row}
</table>{gone_html}
<h2>발견 항목</h2>{''.join(items) or '<p>점검한 범위에서 발견된 항목이 없습니다.</p>'}
<p class="note">공식 결과는 숨김·위장 기법이 확인된 광고이며 result.json과 같습니다. 추가 의심은 참고 항목이고 불법으로 확정한 것이 아닙니다. 검토 표시와 메모는 점검자가 남긴 판단으로, 결과 파일에는 반영되지 않습니다.</p>
</body></html>"""


def _compare_key(url: str) -> str:
    from .urlnorm import compare_key
    return compare_key(url)


def compare(rec: dict, prev: dict) -> dict:
    """Changes against the previous scan of the same address.
    new     - items that were not in the previous scan
    gone    - items of the previous scan whose page was checked again and no longer has them
              (likely removed / handled)
    unknown - items whose page was not checked this time (cannot tell)"""
    cur_rows = {r["key"]: r for r in rows(rec, {})}
    prev_rows = {r["key"]: r for r in rows(prev, {})}
    checked = {_compare_key(p.get("url", "")) for p in rec.get("pages", []) if p.get("status") in ("ok", "partial")}
    checked |= {_compare_key(p.get("requested_url", "")) for p in rec.get("pages", []) if p.get("status") in ("ok", "partial")}
    gone, unknown = [], []
    for k, r in prev_rows.items():
        if k in cur_rows:
            continue
        item = {"key": k, "구분": r["구분"], "url": r["url"], "location": r["location"],
                "evidence_text": r["evidence_text"], "techniques": r["techniques"]}
        (gone if _compare_key(r["url"]) in checked else unknown).append(item)
    return {"prev_run": prev.get("run_id"), "prev_started_at": prev.get("started_at"),
            "new": [k for k in cur_rows if k not in prev_rows], "gone": gone, "unknown": unknown,
            "same": sum(1 for k in cur_rows if k in prev_rows)}


def find_previous(runs: Path, rec: dict) -> dict | None:
    """The latest earlier scan of the same entry address that checked at least one page."""
    me, key = rec.get("run_id", ""), _compare_key(rec.get("entry_url", ""))
    for d in sorted((x for x in runs.iterdir() if x.is_dir() and x.name < me), reverse=True):
        f = d / "scan.json"
        if not f.is_file():
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _compare_key(r.get("entry_url", "")) == key and any(p.get("status") in ("ok", "partial") for p in r.get("pages", [])):
            return r
    return None
