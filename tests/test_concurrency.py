"""Pages at a time, adjusted to the PC (clearweb/concurrency.py).

Memory is never really exhausted here: free memory comes from controlled values."""
import asyncio
import json

import pytest

from clearweb import concurrency as conc
from clearweb.concurrency import Controller, Window


def w(pages, seconds=60, timeouts=0):
    return Window(pages, seconds, timeouts)


# ---------------------------------------------------------------- planning
def test_plan_separates_cpu_ceiling_and_memory_cap():
    assert conc.plan(2, 8000) == {"processors": 2, "free_mb_start": 8000, "cpu_cap": 4, "mem_cap_start": 4,
                                  "max": 4, "start": 4}
    assert conc.plan(8, 8000)["cpu_cap"] == 6 and conc.plan(12, 8000)["cpu_cap"] == 8
    low = conc.plan(12, 1436)              # 2026-10-03 museum run: 1.4 GB free at start
    assert low["cpu_cap"] == 8 and low["mem_cap_start"] == conc.MIN_LIMIT and low["start"] == conc.MIN_LIMIT
    assert conc.plan(12, None)["mem_cap_start"] == 8


def test_memory_cap_counts_pages_already_running():
    # 6 pages running, 2 GB still free: 6 + (2048 - 1536) // 250 = 8, not (2048-1536)//250 = 2
    assert conc.mem_cap_for(2048, 6) == 8
    assert conc.mem_cap_for(2048, 0) == 2


def test_free_memory_is_read_on_windows():
    mb = conc.free_memory_mb()
    assert mb is None or mb > 0


# ---------------------------------------------------------------- scenarios
def test_low_at_start_then_recovers():
    c = Controller(2, 8, mem_cap=2)
    c.memory(5000, active=2, t=60)          # room for more, first sighting: wait
    assert c.mem_cap == 2
    c.decide(w(20), 60)
    assert c.limit == 2                     # cap still 2
    c.memory(5000, active=2, t=120)         # second sighting in a row: cap rises
    assert c.mem_cap == 8 and "메모리 회복으로 상한 2→8" in c.history[-1]["why"]
    assert c.decide(w(20), 120)[0] == 3     # pages grow one step per window, not at once
    assert "처리량 확인 후 동시 처리 2→3" in c.history[-1]["why"]
    c.decide(w(28), 180)
    assert c.limit == 4


def test_enough_then_short():
    c = Controller(6, 8, mem_cap=8)
    c.memory(1700, active=6, t=60)          # 6 + (1700-1536)//250 = 6 -> cap 6, limit stays 6
    assert (c.mem_cap, c.limit) == (6, 6)
    c.memory(1300, active=6, t=120)         # 6 + (1300-1536)//250 = 6 - 1 = 5 -> limit 6 -> 5 at once
    assert (c.mem_cap, c.limit) == (5, 5) and "메모리 부족으로 동시 처리 6→5" in c.history[-1]["why"]
    assert c.emergency(800, t=125)          # below 1 GB: cut within seconds
    assert c.limit == 3 and "메모리 부족으로 동시 처리 5→3" in c.history[-1]["why"]
    assert not c.emergency(700, t=130)      # not again within 10 s
    assert c.emergency(700, t=136) and c.limit == conc.MIN_LIMIT


def test_flapping_memory_does_not_flap_the_limit():
    c = Controller(4, 8, mem_cap=4)
    # 4 pages running; free memory jumps around "room for one more page" (1786 MB):
    # 2000 MB would allow 5, 1780 MB allows 4 -> no raise (needs one page of margin)
    for k, free in enumerate([2000, 1780, 2000, 1780, 2000, 1780]):
        c.memory(free, active=4, t=60 * k)
        c.decide(w(20), 60 * k)
    assert c.mem_cap == 4 and c.limit == 4
    assert not [h for h in c.history if "메모리" in h["why"]]
    # a dip below the running pages' room cuts once, and the cap does not bounce straight back
    c.memory(1500, active=4, t=400)                  # 4 + (1500-1536)//250 = 3
    assert (c.mem_cap, c.limit) == (3, 3)
    c.memory(2100, active=3, t=460)                  # first sighting of room: wait
    assert c.mem_cap == 3
    c.memory(1700, active=3, t=520)                  # room gone again: streak reset, no change
    c.memory(2100, active=3, t=580)
    assert c.mem_cap == 3


def test_throughput_drop_blocks_raises_even_with_memory():
    c = Controller(4, 8, mem_cap=8)
    c.decide(w(40), 60)                     # -> 5
    c.decide(w(30), 120)                    # lower than 40 by >10%: back to 4, ceiling 4
    assert c.limit == 4 and c.ceiling == 4
    for k in range(3, 8):
        c.memory(9000, active=4, t=60 * k)  # plenty of memory
        c.decide(w(30), 60 * k)
    assert c.limit == 4


def test_timeouts_cut():
    c = Controller(8, 8)
    assert c.decide(w(10, timeouts=4), 60)[0] == 6


# ---------------------------------------------------------------- the running loop
def test_controller_loop_raises_cap_when_memory_frees_up():
    async def run():
        ctl = Controller(2, 8, mem_cap=2)
        lim = conc.Limiter(2)
        counters = {"pages": 0, "timeouts": 0}
        free = iter([1400] * 3 + [6000] * 40)
        done = asyncio.Event()

        async def work():
            while not done.is_set():
                counters["pages"] += 1
                await asyncio.sleep(0.01)
        job = asyncio.create_task(work())
        loop = asyncio.create_task(conc.run_controller(ctl, lim, counters, done, window_s=0.1,
                                                       read_free=lambda: next(free), tick_s=0.05))
        await asyncio.sleep(1.2)
        done.set()
        await asyncio.gather(job, loop)
        return ctl, lim
    ctl, lim = asyncio.run(run())
    assert ctl.mem_cap == 8 and lim.limit == ctl.limit > 2
    assert any("메모리 회복" in h["why"] for h in ctl.history)


# ---------------------------------------------------------------- inside a scan
def _crawl_site():
    from testsites.server import start
    return start("crawl", 0)


def test_scan_auto_records_memory_and_manual_ignores_it(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from clearweb import scanner
    srv = _crawl_site()
    url = f"http://127.0.0.1:{srv.server_port}/"
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    try:
        readings = iter([1400] * 6 + [6000] * 1000)       # short at start, then freed
        auto = scanner.Scan(url, scanner.ScanConfig(concurrency_window_s=1.0, concurrency_tick_s=0.25), tmp_path / "a")
        auto._read_free = lambda: next(readings)
        auto.run()
        manual = scanner.Scan(url, scanner.ScanConfig(concurrency=4, concurrency_auto=False), tmp_path / "m")
        manual._read_free = lambda: 500                  # would cut an automatic scan at once
        manual.run()
    finally:
        srv.shutdown()
    a = json.loads((tmp_path / "runs" / auto.run_id / "scan.json").read_text(encoding="utf-8"))["concurrency"]
    assert a["mode"] == "자동" and a["start"] == 2 and a["cpu_cap"] >= 4 and a["free_mb_start"] == 1400
    assert a["free_mb_last"] == 6000 and a["mem_cap_last"] > 2
    assert any("메모리 회복" in h["why"] for h in a["history"]) and all("t" in h for h in a["history"])
    m = json.loads((tmp_path / "runs" / manual.run_id / "scan.json").read_text(encoding="utf-8"))["concurrency"]
    assert m["mode"] == "고정" and m["start"] == 4 and m["history"] == []


def test_limiter_follows_a_changing_limit():
    async def run():
        lim = conc.Limiter(2)
        active, peak = 0, 0

        async def job():
            nonlocal active, peak
            await lim.acquire()
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            await lim.release()

        await asyncio.gather(*(job() for _ in range(8)))
        first = peak
        peak = 0
        await lim.set_limit(4)
        await asyncio.gather(*(job() for _ in range(8)))
        return first, peak
    assert asyncio.run(run()) == (2, 4)
