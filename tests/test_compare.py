"""Comparison with the previous scan of the same address (manage.compare / find_previous)."""
import json

from clearweb import manage

A, B, C = "http://site.example/a", "http://site.example/b", "http://site.example/c"


def _rec(run_id, findings, pages, extra=()):
    fs = [{"url": u, "location": loc, "technique": t, "evidence_text": f"{loc} 문구"} for u, loc, t in findings]
    return {"run_id": run_id, "started_at": "2026-10-12T10:00:00", "entry_url": A,
            "findings": fs, "result": {"findings": [dict(f) for f in fs]},
            "extra_ads": [{"kind": "text", "url": u, "location": loc, "evidence_text": "x"} for u, loc in extra],
            "pages": [{"url": u, "status": s} for u, s in pages]}


def test_new_gone_unknown_same():
    prev = _rec("20261011-100000", [(A, "div.x", "OFFSCREEN"), (A, "div.old", "TRANSPARENT"), (C, "p.c", "JAMO")],
                [(A, "ok"), (C, "ok")], extra=[(B, "span.e")])
    cur = _rec("20261012-100000", [(A, "div.x", "OFFSCREEN"), (A, "div.x", "TRANSPARENT"), (B, "p.n", "HOMOGLYPH")],
               [(A + "/", "ok"), (B, "ok"), (C, "failed")])
    c = manage.compare(cur, prev)
    assert c["prev_run"] == "20261011-100000"
    assert c["same"] == 1                                                  # div.x (techniques grouped per element)
    assert c["new"] == [manage.official_key(B, "p.n")]
    assert {g["location"] for g in c["gone"]} == {"div.old", "span.e"}     # pages A, B checked again
    assert [g["location"] for g in c["unknown"]] == ["p.c"]                # page C failed this time
    csv = manage.to_csv(cur, {}, c).decode("utf-8-sig")
    assert "신규" in csv and "사라짐" in csv


def test_find_previous(tmp_path):
    def save(rec):
        d = tmp_path / rec["run_id"]
        d.mkdir()
        (d / "scan.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    save(_rec("20261010-100000", [], [(A, "ok")]))
    save(_rec("20261011-100000", [], [(A, "failed")]))                     # checked nothing -> skipped
    other = _rec("20261011-120000", [], [(A, "ok")])
    other["entry_url"] = "http://other.example/"
    save(other)                                                            # different address -> skipped
    cur = _rec("20261012-100000", [], [(A, "ok")])
    save(cur)
    assert manage.find_previous(tmp_path, cur)["run_id"] == "20261010-100000"
    assert manage.find_previous(tmp_path, _rec("20261009-000000", [], [])) is None
