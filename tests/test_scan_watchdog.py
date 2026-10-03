"""A scan always ends and writes its results (regression for the 2026-10-02 hang).

Before: a worker had try/finally but no except. Any unexpected error ended that worker;
after the last one the queue never emptied, the scan waited forever past its time
budget and result.json was never written."""
import asyncio
import json
import time

import pytest

pytest.importorskip("playwright.async_api")

from clearweb import scanner  # noqa: E402
from testsites.server import start  # noqa: E402


@pytest.fixture(scope="module")
def site():
    srv = start("crawl", 0)
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def _scan(site, tmp_path, monkeypatch, budget=600.0, **cfg_kw):
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    cfg = scanner.ScanConfig(total_budget_s=budget, **cfg_kw)
    sc = scanner.Scan(site, cfg, tmp_path)
    return sc


def _record(sc, tmp_path):
    return json.loads((tmp_path / "runs" / sc.run_id / "scan.json").read_text(encoding="utf-8"))


def test_errors_in_every_page_do_not_stop_the_scan(site, tmp_path, monkeypatch):
    sc = _scan(site, tmp_path, monkeypatch, concurrency=4, concurrency_auto=False)
    orig = scanner.Scan._scan_page
    calls = {"n": 0}

    async def flaky(self, ctx, url, depth, findings, excluded):
        calls["n"] += 1
        if depth >= 1 and calls["n"] % 2 == 0:          # more failures than there are workers
            raise RuntimeError("simulated internal error")
        return await orig(self, ctx, url, depth, findings, excluded)

    monkeypatch.setattr(scanner.Scan, "_scan_page", flaky)
    t = time.monotonic()
    sc.run()
    assert time.monotonic() - t < 120
    assert sc.progress.state == "done"
    rec = _record(sc, tmp_path)
    failed = [pg for pg in rec["pages"] if pg["status"] == "failed"]
    assert len(failed) >= 4 + 1                        # more failures than there are workers
    assert all("내부 오류" in pg["error"] for pg in failed)
    assert any(pg["status"] == "ok" for pg in rec["pages"] if pg["depth"] and pg["depth"] >= 1)
    assert (tmp_path / "result.json").is_file()


def test_a_page_that_never_returns_is_cut_at_the_time_budget(site, tmp_path, monkeypatch):
    sc = _scan(site, tmp_path, monkeypatch, budget=12.0)
    orig = scanner.Scan._scan_page

    async def stuck(self, ctx, url, depth, findings, excluded):
        if depth >= 1:
            await asyncio.sleep(3600)                   # hangs outside the per-page time limit
        return await orig(self, ctx, url, depth, findings, excluded)

    monkeypatch.setattr(scanner.Scan, "_scan_page", stuck)
    t = time.monotonic()
    sc.run()
    took = time.monotonic() - t
    assert took < 12.0 + 15, took                       # budget + closing the browser
    rec = _record(sc, tmp_path)
    assert any("전체 시간 예산 도달" in e for e in rec["errors"])
    assert any(pg["status"] == "budget_cut" and "시간 한도로 중단" in pg["error"] for pg in rec["pages"])
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["meta"]["entry_url"]


def test_malformed_link_is_skipped_not_fatal():
    pol = scanner.Policy("https://www.example.go.kr/", 50, 2, 10)
    with pytest.raises(ValueError):
        pol.admit("http://[www.example.go.kr/a", 1)     # the policy itself raises; the worker skips it


def test_continue_from_where_the_page_limit_stopped(site, tmp_path, monkeypatch):
    """A scan cut by its limit can be continued; together they equal one full scan."""
    from clearweb.crawl import compare_key
    full = _scan(site, tmp_path / "full", monkeypatch)
    full.run()
    want = {(f["url"], f["location"], f["technique"]) for f in _record(full, tmp_path / "full")["findings"]}

    first = _scan(site, tmp_path, monkeypatch, max_pages=5)
    first.run()
    rec1 = _record(first, tmp_path)
    assert rec1["resume"]["pending"], "links left by the page limit must be kept"
    time.sleep(1.1)                                     # run ids are per second

    cont = scanner.Scan(rec1["entry_url"], scanner.ScanConfig(), tmp_path, resume=rec1)
    cont.run()
    rec2 = _record(cont, tmp_path)
    assert rec2["continued_from"] == rec1["run_id"] and rec2["elapsed_total_sec"] >= rec2["elapsed_sec"]
    got = {(f["url"], f["location"], f["technique"]) for f in rec2["findings"]}
    assert got == want
    keys = [compare_key(pg["requested_url"]) for pg in rec2["pages"]]
    assert len(keys) == len(set(keys)), "no page is visited twice"
    assert not rec2.get("resume")                       # nothing left
    res = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert {(f["url"], f["location"], f["technique"]) for f in res["findings"]} == want


def test_timeout_without_message_fails_the_frame_not_the_page(tmp_path, monkeypatch):
    """A timeout has an empty message. Recording it used to raise IndexError inside the error
    handler, so one slow frame made the whole page 'failed' (seen on www.mois.go.kr)."""
    srv = start("dev", 0)
    try:
        sc = _scan(f"http://127.0.0.1:{srv.server_port}/", tmp_path, monkeypatch, max_pages=1)
        orig = scanner.Scan._ev

        async def slow_collect(self, target, js, arg=None):
            if js is scanner.COLLECT_JS and getattr(target, "parent_frame", 1) is None:
                raise asyncio.TimeoutError()               # main frame collection never answers
            return await orig(self, target, js, arg)

        monkeypatch.setattr(scanner.Scan, "_ev", slow_collect)
        sc.run()
    finally:
        srv.shutdown()
    rec = _record(sc, tmp_path)
    page = rec["pages"][0]
    assert page["status"] in ("ok", "partial"), page
    main = page["frames"][0]
    assert main["status"] == "failed" and main["error"] == "시간 초과(응답 없음)"
    assert any(f["status"] == "ok" for f in page["frames"][1:])     # iframes are still checked
    assert page["links_found"] > 0


def test_err_helper():
    assert scanner._err(asyncio.TimeoutError()) == "시간 초과(응답 없음)"
    assert scanner._err(ValueError("")) == "ValueError"
    assert scanner._err(RuntimeError("first\nsecond")) == "first"
