"""The final state must be published only after result.json and the run record exist."""
import json
import threading
import time

from clearweb import result_writer, scanner


def test_state_is_final_only_after_results_are_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")

    async def no_browser(self, pages, findings, excluded):
        pages.append({"status": "ok", "url": "http://x/", "error": "", "frames": []})

    monkeypatch.setattr(scanner.Scan, "_run", no_browser)
    real_write = result_writer.write

    def slow_write(doc, path):
        time.sleep(0.6)                      # widen the save window
        return real_write(doc, path)

    monkeypatch.setattr(scanner.result_writer, "write", slow_write)
    sc = scanner.Scan("http://x/", out_dir=tmp_path)
    seen = []
    t = threading.Thread(target=sc.run)
    t.start()
    while t.is_alive():
        p = sc.progress
        seen.append((p.state, p.result_path, (tmp_path / "runs" / sc.run_id / "scan.json").exists()))
        time.sleep(0.02)
    t.join()
    final = [x for x in seen if x[0] not in ("idle", "running")]
    assert any(x[0] == "running" for x in seen)
    # whenever a final state was visible, the path and the run record were already there
    assert all(path and record for _, path, record in final)
    assert sc.progress.state == "done"
    assert sc.progress.elapsed_sec == sc.record["result"]["meta"]["elapsed_sec"]


def test_result_extra_is_always_rewritten_for_this_scan(tmp_path, monkeypatch):
    """result_extra.json is written by every scan (visible text ads are an extra type even with
    the image check off), so a file left by an earlier scan never remains."""
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")

    async def no_browser(self, pages, findings, excluded):
        pages.append({"status": "ok", "url": "http://x/", "error": "", "frames": []})

    monkeypatch.setattr(scanner.Scan, "_run", no_browser)
    (tmp_path / "result_extra.json").write_text('{"meta": {}, "findings": [{"id": "x_001"}]}', encoding="utf-8")
    sc = scanner.Scan("http://x/", out_dir=tmp_path)           # OCR off (default)
    sc.run()
    extra = json.loads((tmp_path / "result_extra.json").read_text(encoding="utf-8"))
    assert extra["findings"] == [] and extra["meta"]["entry_url"] == "http://x/"
    assert (tmp_path / "result.json").exists()


def test_visible_ads_go_to_result_extra(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")

    async def no_browser(self, pages, findings, excluded):
        pages.append({"status": "ok", "url": "http://x/", "error": "", "frames": []})
        self.extra_ads.append({"kind": "text", "url": "http://x/", "location": "div.side > p.promo",
                               "evidence_text": "카지노 가입코드 WIN24 첫충 20%", "selector_verified": True})
        self.extra_ads.append({"kind": "text", "url": "http://x/", "location": "div.x", "evidence_text": "토토",
                               "selector_verified": False})          # unverified location: not submitted
    monkeypatch.setattr(scanner.Scan, "_run", no_browser)
    sc = scanner.Scan("http://x/", out_dir=tmp_path)
    sc.run()
    extra = json.loads((tmp_path / "result_extra.json").read_text(encoding="utf-8"))
    assert [(f["location"], f["technique"], f["extra_finding"]) for f in extra["findings"]] == [
        ("div.side > p.promo", "ETC", scanner.Scan.VISIBLE_AD)]
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["findings"] == []
    assert result_writer.validate_extra(extra) == []
