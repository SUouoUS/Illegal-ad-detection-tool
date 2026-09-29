"""The final state must be published only after result.json and the run record exist."""
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
