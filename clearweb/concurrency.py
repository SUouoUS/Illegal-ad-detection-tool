"""How many pages to check at the same time, adjusted to the PC while scanning.

Measured on recordings (docs/PROGRESS.md, 2026-10-02): on a 12-logical-processor laptop,
8 pages at a time were 1.2-1.5x faster than 4 with no failures; with fewer processors the
gain shrank or vanished, and even with 2 processors 4 was better than 2. So:

  * start at 4 (less only when free memory is short),
  * CPU ceiling from logical processors (<=4: 4, 5-8: 6, >=9: 8), fixed for the scan,
  * memory cap re-evaluated while scanning: pages already running + what the free memory
    can still hold (ClearWeb's own pages must not lower it), down at once, up slowly,
  * once a minute: raise by one while pages per minute keep improving, step back when they
    drop, cut when pages start timing out; below 1 GB free, cut within seconds.
Lowering the limit only stops new pages from starting; running pages finish.

Only the number of logical processors and the amount of free memory are read (no names,
serial numbers or other identifying data), and nothing leaves the PC.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import time
from dataclasses import dataclass, field

PAGE_MB = 250            # memory one more open page costs (measured 200-250 MB incl. browser overhead)
RESERVE_MB = 1536        # memory left for Windows and other programs
LOW_MB = 1024            # below this, cut at once
MIN_LIMIT = 2
START = 4
RAISE_STREAK = 2         # memory must show room this many checks in a row before the cap rises
CUT_GAP_S = 10.0         # at least this long between two emergency cuts


def free_memory_mb() -> int | None:
    """Free physical memory (Windows), or None when it cannot be read."""
    try:
        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return int(st.ullAvailPhys // 2**20)
    except Exception:  # noqa: BLE001 - not Windows, or the call is unavailable
        return None


def ceiling_for(processors: int) -> int:
    return 4 if processors <= 4 else 6 if processors <= 8 else 8


def mem_cap_for(free_mb: int, active: int, margin_mb: int = 0) -> int:
    """Pages the memory allows: the pages already running (their memory is already in use and
    must not count against them) plus the pages the free memory can still hold."""
    return max(MIN_LIMIT, active + (free_mb - RESERVE_MB - margin_mb) // PAGE_MB)


def plan(processors: int | None = None, free_mb: int | None = None) -> dict:
    """Ceilings and starting number for this PC. The CPU ceiling is fixed for the scan; the
    memory cap is only a starting value, re-evaluated while scanning."""
    processors = processors or os.cpu_count() or 4
    cpu = ceiling_for(processors)
    mem = cpu if free_mb is None else min(cpu, mem_cap_for(free_mb, 0))
    return {"processors": processors, "free_mb_start": free_mb, "cpu_cap": cpu, "mem_cap_start": mem,
            "max": cpu, "start": max(MIN_LIMIT, min(START, mem))}


class Limiter:
    """At most `limit` pages at a time; the limit can change while pages are running.
    Lowering it does not stop running pages - new pages wait for a free slot."""

    def __init__(self, limit: int):
        self.limit, self.active = limit, 0
        self._cond = asyncio.Condition()

    async def acquire(self):
        async with self._cond:
            await self._cond.wait_for(lambda: self.active < self.limit)
            self.active += 1

    async def release(self):
        async with self._cond:
            self.active -= 1
            self._cond.notify_all()

    async def set_limit(self, n: int):
        async with self._cond:
            self.limit = n
            self._cond.notify_all()


@dataclass
class Window:
    pages: int           # pages finished in the window
    seconds: float
    timeouts: int        # of those, pages that ran out of time


@dataclass
class Controller:
    """Pages at a time from measurements (no I/O: easy to test).

    limit   - pages allowed to run now
    cpu_cap - ceiling from the logical processors (fixed)
    mem_cap - ceiling from memory, re-evaluated during the scan
    ceiling - a raise that lowered throughput is not tried again"""
    limit: int
    cpu_cap: int
    mem_cap: int | None = None
    prev_rate: float | None = None
    last_step: int = 0               # +1 after a raise, -1 after a step down, 0 otherwise
    ceiling: int | None = None
    free_last: int | None = None
    history: list = field(default_factory=list)
    _streak: int = 0
    _last_cut: float = -1e9

    def __post_init__(self):
        if self.mem_cap is None:
            self.mem_cap = self.cpu_cap

    @property
    def top(self) -> int:
        return min(self.cpu_cap, self.mem_cap, self.ceiling or self.cpu_cap)

    def _log(self, t, kind, old, new, why, rate=None):
        changed_limit = kind == "동시 처리"
        self.history.append({"t": round(t, 1), "kind": kind, "from": old, "to": new,
                             "limit": old if changed_limit else self.limit, "next": new if changed_limit else self.limit,
                             "mem_cap": self.mem_cap, "cpu_cap": self.cpu_cap, "free_mb": self.free_last,
                             "rate_per_min": rate, "why": why})

    def emergency(self, free_mb: int | None, t: float) -> bool:
        """Checked every few seconds: free memory below LOW_MB cuts at once."""
        if free_mb is None:
            return False
        self.free_last = free_mb
        if free_mb >= LOW_MB or self.limit <= MIN_LIMIT or t - self._last_cut < CUT_GAP_S:
            return False
        old = self.limit
        self.limit = max(MIN_LIMIT, min(old - 1, old * 3 // 4))
        self.mem_cap, self._streak, self._last_cut, self.last_step = self.limit, 0, t, -1
        self._log(t, "동시 처리", old, self.limit, f"메모리 부족으로 동시 처리 {old}→{self.limit} (남은 메모리 {free_mb}MB)")
        return True

    def memory(self, free_mb: int | None, active: int, t: float):
        """Re-evaluate the memory cap (once per window): down at once; up only with one page of
        extra margin, seen RAISE_STREAK checks in a row (no flapping at the boundary)."""
        if free_mb is None:
            return
        self.free_last = free_mb
        down = min(self.cpu_cap, mem_cap_for(free_mb, active))
        up = min(self.cpu_cap, mem_cap_for(free_mb, active, margin_mb=PAGE_MB))
        if down < self.mem_cap:
            old_cap, old = self.mem_cap, self.limit
            self.mem_cap, self._streak = down, 0
            if self.limit > down:
                self.limit, self.last_step = down, -1
                self._log(t, "동시 처리", old, down, f"메모리 부족으로 동시 처리 {old}→{down} (남은 메모리 {free_mb}MB)")
            else:
                self._log(t, "메모리 상한", old_cap, down, f"메모리 여유 감소로 상한 {old_cap}→{down} (남은 메모리 {free_mb}MB)")
        elif up > self.mem_cap:
            self._streak += 1
            if self._streak >= RAISE_STREAK:
                old_cap = self.mem_cap
                self.mem_cap, self._streak = up, 0
                self._log(t, "메모리 상한", old_cap, up, f"메모리 회복으로 상한 {old_cap}→{up} (남은 메모리 {free_mb}MB)")
        else:
            self._streak = 0

    def decide(self, w: Window, t: float = 0.0) -> tuple[int, str]:
        """Throughput step (once per window), within the current ceilings."""
        rate = round(w.pages / max(1.0, w.seconds) * 60.0, 1)
        old, new, why = self.limit, self.limit, ""
        if w.pages and w.timeouts / w.pages > 0.2:
            new = max(MIN_LIMIT, old * 3 // 4)
            why = f"시간 초과 증가({w.timeouts}/{w.pages}페이지)로 동시 처리 {old}→{new}"
        elif self.last_step > 0 and self.prev_rate and rate < self.prev_rate * 0.9:
            new = old - 1
            self.ceiling = new
            why = f"늘렸더니 처리량 감소({self.prev_rate:.1f}→{rate:.1f}페이지/분)로 동시 처리 {old}→{new}"
        elif w.pages and old < self.top:
            new = old + 1
            why = f"처리량 확인 후 동시 처리 {old}→{new}"
        self.last_step = (new > old) - (new < old)
        self.prev_rate = rate
        if new != old:
            self.limit = new
            self._log(t, "동시 처리", old, new, why, rate)
        return new, why or "유지"


async def run_controller(ctl: Controller, limiter: Limiter, counters: dict, done: asyncio.Event,
                         window_s: float, progress=None, read_free=None, tick_s: float = 5.0):
    """Every tick: emergency memory check. Every window: memory cap, then throughput step.
    `read_free` can be replaced in tests (default: the real free memory)."""
    read_free = read_free or free_memory_mb
    t0 = time.monotonic()
    last_pages, last_to, t_win = counters["pages"], counters["timeouts"], t0
    while not done.is_set():
        try:
            await asyncio.wait_for(done.wait(), timeout=tick_s)
            return
        except asyncio.TimeoutError:
            pass
        now = time.monotonic()
        free = read_free()
        ctl.emergency(free, now - t0)
        if now - t_win >= window_s:
            w = Window(counters["pages"] - last_pages, now - t_win, counters["timeouts"] - last_to)
            last_pages, last_to, t_win = counters["pages"], counters["timeouts"], now
            ctl.memory(free, limiter.active, now - t0)
            ctl.decide(w, now - t0)
        if ctl.limit != limiter.limit:
            await limiter.set_limit(ctl.limit)
        if progress is not None:
            progress.concurrency, progress.concurrency_mem_cap, progress.free_mb_last = ctl.limit, ctl.mem_cap, ctl.free_last
