"""Result management: review marks, CSV export, printable report, run list (붙임1 '결과 안내·관리 기능').

Review marks are stored apart and never change the automatic result files."""
import json
import socket
import threading
import time
import urllib.request

import pytest

from clearweb import manage

REC = {
    "run_id": "20261012-100000", "tool_version": "0.2.1", "entry_url": "https://www.example.go.kr/",
    "state": "done", "end_reason": "완료", "started_at": "2026-10-12T10:00:00+09:00", "elapsed_sec": 12.3, "pages": [],
    "findings": [
        {"url": "https://www.example.go.kr/a", "location": "p.x > span", "technique": "TRANSPARENT",
         "evidence_text": "카지노 가입코드 WIN24", "restored_text": "카지노 가입코드 WIN24", "ad_reasons": ["주제어: 카지노(도박)"]},
        {"url": "https://www.example.go.kr/a", "location": "p.x > span", "technique": "JAMO",
         "evidence_text": "카지노 가입코드 WIN24", "restored_text": "카지노 가입코드 WIN24", "ad_reasons": ["주제어: 카지노(도박)"]},
    ],
    "extra_ads": [{"kind": "text", "url": "https://www.example.go.kr/b", "location": "div.side > p", "evidence_text": "토토 바로가기 010-0000-1111",
                   "ad_reasons": ["보조 주제어: 토토(도박)"]}],
    "images": [],
    "result": {"meta": {"started_at": "2026-10-12T10:00:00+09:00", "finished_at": "2026-10-12T10:00:12+09:00", "elapsed_sec": 12.3},
               "findings": [{"url": "https://www.example.go.kr/a", "location": "p.x > span", "technique": "TRANSPARENT"},
                            {"url": "https://www.example.go.kr/a", "location": "p.x > span", "technique": "JAMO"}]},
}


def test_rows_group_techniques_and_include_extra():
    rows = manage.rows(REC, {})
    assert [(r["구분"][:2], r["techniques"]) for r in rows] == [("공식", ["TRANSPARENT", "JAMO"]), ("추가", [])]
    assert all(r["review"] == "미검토" for r in rows)


def test_review_saved_apart_and_csv_report(tmp_path):
    k = manage.official_key("https://www.example.go.kr/a", "p.x > span")
    manage.save_review(tmp_path, k, "confirmed", "담당자 확인, 삭제 요청")
    manage.save_review(tmp_path, manage.extra_key("text", "https://www.example.go.kr/b", "div.side > p"), "false_positive")
    rv = manage.load_reviews(tmp_path)
    assert rv[k]["status"] == "confirmed" and rv[k]["memo"].startswith("담당자") and rv[k]["at"]
    with pytest.raises(ValueError):
        manage.save_review(tmp_path, k, "deleted")
    manage.save_review(tmp_path, k, "unreviewed")                     # back to unreviewed without memo: removed
    assert k not in manage.load_reviews(tmp_path)
    csv = manage.to_csv(REC, manage.load_reviews(tmp_path)).decode("utf-8")
    assert csv.startswith("﻿번호,구분,") and "투명 텍스트, 자모 분해" in csv and "오탐" in csv
    html = manage.report_html(REC, manage.load_reviews(tmp_path), "모두 정상")
    assert "불법광고 점검 보고서" in html and "광고 확인 0 · 오탐 1 · 미검토 1" in html
    assert "<script" not in html.replace("onclick", "")               # evidence text is escaped


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_endpoints(tmp_path, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    from clearweb import server
    run = tmp_path / REC["run_id"]
    run.mkdir()
    (run / "scan.json").write_text(json.dumps(REC, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(server, "runs_dir", lambda: tmp_path)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/info", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    try:
        key = manage.official_key("https://www.example.go.kr/a", "p.x > span")
        req = urllib.request.Request(f"{base}/api/scans/{REC['run_id']}/reviews", method="PUT",
                                     data=json.dumps({"key": key, "status": "confirmed", "memo": "확인"}).encode(),
                                     headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())[key]["status"] == "confirmed"
        rec = json.loads(urllib.request.urlopen(f"{base}/api/scans/{REC['run_id']}").read())
        assert rec["reviews"][key]["memo"] == "확인"
        lst = json.loads(urllib.request.urlopen(f"{base}/api/scans").read())
        assert lst[0]["items"] == 2 and lst[0]["reviewed"] == 1
        csv = urllib.request.urlopen(f"{base}/api/scans/{REC['run_id']}/export.csv")
        assert "attachment" in csv.headers["Content-Disposition"] and "광고 확인" in csv.read().decode("utf-8")
        assert "점검 보고서" in urllib.request.urlopen(f"{base}/api/scans/{REC['run_id']}/report").read().decode("utf-8")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/api/scans/..%2F..%2Fx/report")   # run id is checked
        # the result files are never touched by reviews
        assert not (run / "result.json").exists()
    finally:
        srv.should_exit = True
        th.join(5)
