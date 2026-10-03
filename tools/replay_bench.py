"""Record a real site once, then replay it offline for repeatable speed comparisons.

Live sites vary a lot between runs (the same settings gave 3.4 s or 11.8 s per page), so
speed changes are compared on a recording instead: every response is stored in a HAR file
during one real scan, and later runs check the SAME list of pages with all requests
answered from that file (unrecorded requests are refused, nothing goes to the network).

  python tools/replay_bench.py record <url> <dir> [--pages 200] [--depth 10]
  python tools/replay_bench.py replay <dir> [--label name] [--repeat 1] [--expand legacy]
  python tools/replay_bench.py compare <dir>

<dir> holds site.har, pages.json (pages to check) and replay results. Keep it outside the
repository: recordings are real collected data and are not committed.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clearweb import scanner  # noqa: E402


def _hook(har: Path, record: bool):
    """Answer browser requests from the HAR (or record into it). The scanner's own request
    filter keeps blocking images/ads; everything it lets through goes to the HAR."""
    orig_launch = scanner.Scan._launch

    async def launch(self, pw):
        browser = await orig_launch(self, pw)
        orig_new = browser.new_context

        async def new_context(**kw):
            ctx = await orig_new(**kw)
            await ctx.route_from_har(str(har), update=record, not_found="fallback" if record else "abort",
                                     update_content="embed", update_mode="minimal")
            return ctx
        browser.new_context = new_context
        return browser

    async def block(route):
        req = route.request
        if req.resource_type in scanner.BLOCK_TYPES or scanner._blocked_host(req.url):
            await route.abort()
        else:
            await route.fallback()          # continue_() would bypass the HAR handler
    scanner.Scan._launch = launch
    scanner._block_heavy = block


async def _legacy_expand(self, page, rec):
    """The 'more' clicking used up to 0.1.3 (every control clicked on every page, network
    quiet waited after each click), kept here only to measure the change against it."""
    from urllib.parse import urldefrag
    try:
        await self._ev(page.main_frame, scanner.SCROLL_JS)
    except Exception:  # noqa: BLE001
        pass
    start = urldefrag(page.url)[0]
    clicks = 0
    for _ in range(4):
        if clicks >= self.cfg.expand_clicks or self.cancel.is_set():
            break
        try:
            labels = await self._ev(page.main_frame, scanner.EXPAND_JS)
        except Exception:  # noqa: BLE001
            break
        if not labels:
            break
        for k, label in enumerate(labels):
            if clicks >= self.cfg.expand_clicks:
                break
            label = label[0] if isinstance(label, list) else label
            try:
                h = await page.main_frame.evaluate_handle(
                    "k => { const e = window.__cwExpand[k]; window.__cwClicked.add(e); return e; }", k)
                await h.as_element().click(timeout=2000, no_wait_after=True)
                clicks += 1
                rec["expanded"].append(label)
            except Exception:  # noqa: BLE001
                continue
            try:
                await page.wait_for_load_state("networkidle", timeout=800)
            except Exception:  # noqa: BLE001
                pass
            if urldefrag(page.url)[0] != start:
                rec["links"].append((page.url, "main", False))
                rec["expanded"].append(f"(이동 발생: {label})")
                await page.goto(start, wait_until="domcontentloaded", timeout=self.cfg.nav_timeout_ms)
                await self._settle(page)
                return
        await self._quiet(page)


def record(url: str, d: Path, pages: int, depth: int):
    d.mkdir(parents=True, exist_ok=True)
    _hook(d / "site.har", record=True)
    scanner.runs_dir = lambda: d / "runs"
    # the HAR is written when the context closes: give a large recording time to finish
    cfg = scanner.ScanConfig(max_pages=pages, max_depth=depth, total_budget_s=3600, close_timeout_s=600)
    rec = scanner.Scan(url, cfg, d / "record_out").run()
    ok = [p["requested_url"] for p in rec["pages"] if p["status"] in ("ok", "partial")]
    json.dump({"entry_url": rec["entry_url"], "pages": ok}, open(d / "pages.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    try:
        json.load(open(d / "site.har", encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"recording is incomplete (not valid JSON: {e}) - record again")
    print(f"recorded {len(ok)} pages into {d / 'site.har'} ({(d / 'site.har').stat().st_size / 1e6:.1f} MB)")


class _Sampler:
    """Memory and CPU of this process and its children (the browser), sampled every second.
    Development measurement only (psutil is not a product dependency)."""

    def __init__(self, cores: int | None = None):
        import threading
        import psutil
        self.ps, self.me, self.cores = psutil, psutil.Process(), cores
        self.mem, self.rend, self.nrend, self.cpu, self.stop = [], [], [], [], threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.ps.cpu_percent(None)
        while not self.stop.wait(1.0):
            # the Playwright driver (node) holds the whole recording in replay; the product has no
            # recording, so it is left out. Renderers = one per open page.
            total, rend, nrend = 0, 0, 0
            for pr in [self.me] + self.me.children(recursive=True):
                try:
                    if self.cores and len(pr.cpu_affinity()) != self.cores:
                        pr.cpu_affinity(list(range(self.cores)))   # Chrome's GPU process resets it
                    if pr.name().lower().startswith("node"):
                        continue
                    rss = pr.memory_info().rss
                    total += rss
                    if "--type=renderer" in " ".join(pr.cmdline()):
                        rend += rss
                        nrend += 1
                except Exception:  # noqa: BLE001 - process ended between listing and reading
                    pass
            self.mem.append(total / 2**20)
            self.rend.append(rend / 2**20)
            self.nrend.append(nrend)
            self.cpu.append(self.ps.cpu_percent(None))

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.t.join(3)

    def summary(self):
        per = [r / n for r, n in zip(self.rend, self.nrend) if n]
        return {"mem_peak_mb": round(max(self.mem, default=0)), "mem_mean_mb": round(st.mean(self.mem)) if self.mem else 0,
                "renderer_mb_mean": round(st.mean(per)) if per else 0, "renderer_mb_p90": round(sorted(per)[int(len(per) * .9)]) if per else 0,
                "renderers_max": max(self.nrend, default=0),
                "cpu_mean_pct": round(st.mean(self.cpu), 1) if self.cpu else 0,
                "cpu_p90_pct": round(sorted(self.cpu)[int(len(self.cpu) * .9)], 1) if self.cpu else 0}


def _limit_cores(n: int):
    """Run this process (and the browser it starts, which inherits the mask) on the first n
    logical CPUs - a way to imitate a smaller PC."""
    import psutil
    psutil.Process().cpu_affinity(list(range(n)))


def replay(d: Path, label: str, repeat: int, legacy_expand: bool = False, concurrency: int | None = None,
           cores: int | None = None):
    if cores:
        _limit_cores(cores)
    _hook(d / "site.har", record=False)
    if legacy_expand:
        scanner.Scan._expand = _legacy_expand
    meta = json.load(open(d / "pages.json", encoding="utf-8"))
    out = d / "replays" / label
    out.mkdir(parents=True, exist_ok=True)
    scanner.runs_dir = lambda: out / "runs"
    results = []
    for k in range(repeat):
        cfg = scanner.ScanConfig(follow_links=False, max_pages=len(meta["pages"]), total_budget_s=3600)
        if concurrency:
            cfg.concurrency, cfg.concurrency_auto = concurrency, False
        fake = {"run_id": "replay", "pages": [], "findings": [], "excluded": [], "images": [], "extra_ads": [],
                "resume": {"pending": [[i, u, 1] for i, u in enumerate(meta["pages"])], "policy": {}, "analyzed": []}}
        t = time.monotonic()
        with _Sampler(cores) as smp:
            rec = scanner.Scan(meta["entry_url"], cfg, out, resume=fake).run()
        wall = time.monotonic() - t
        pg = [p for p in rec["pages"] if p.get("timing")]
        stages = {}
        for p in pg:
            for s, v in p["timing"].items():
                stages.setdefault(s, []).append(v)
        res = {"label": label, "run": k + 1, "wall_sec": round(wall, 1), "pages": len(rec["pages"]),
               "checked": sum(1 for p in rec["pages"] if p["status"] in ("ok", "partial")),
               "page_sec_mean": round(st.mean(p["elapsed_sec"] for p in pg), 2) if pg else None,
               "stage_sum": {s: round(sum(v), 1) for s, v in stages.items()},
               "clicks": sum(len(p.get("expanded", [])) for p in rec["pages"]),
               "findings": sorted([f["url"], f["location"], f["technique"]] for f in rec["result"]["findings"]),
               "extra": sorted([x["url"], x["location"]] for x in rec["extra_ads"]),
               "concurrency": cfg.concurrency, "cores": cores or None,
               "failed": sum(1 for p in rec["pages"] if p["status"] in ("failed", "stopped")),
               "timeouts": sum(1 for p in rec["pages"] if "시간 초과" in (p.get("error") or "")),
               **smp.summary()}
        results.append(res)
        print(f"{label} #{k + 1}: wall {res['wall_sec']}s, checked {res['checked']}/{len(meta['pages'])}, "
              f"page mean {res['page_sec_mean']}s, clicks {res['clicks']}, stages {res['stage_sum']}, "
              f"findings {len(res['findings'])}, extra {len(res['extra'])}, failed {res['failed']}, "
              f"mem peak {res['mem_peak_mb']}MB (renderer {res['renderer_mb_mean']}MB x{res['renderers_max']}), "
              f"cpu mean {res['cpu_mean_pct']}%")
    json.dump(results, open(out / "results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def compare(d: Path):
    rows = []
    for f in sorted((d / "replays").glob("*/results.json")):
        rs = json.load(open(f, encoding="utf-8"))
        rows.append((f.parent.name, rs))
    if not rows:
        print("no replays")
        return
    base_find = {tuple(x) for x in rows[0][1][0]["findings"]} | {tuple(x) for x in rows[0][1][0]["extra"]}
    print(f"{'label':16s} {'wall(s)':>14s} {'page mean':>10s} {'clicks':>7s} {'checked':>8s}  same findings as {rows[0][0]}")
    for name, rs in rows:
        walls = [r["wall_sec"] for r in rs]
        if not rs[0]["checked"]:
            print(f"{name:16s} no page checked (see {name}/runs)")
            continue
        fs = {tuple(x) for x in rs[0]["findings"]} | {tuple(x) for x in rs[0]["extra"]}
        print(f"{name:16s} {st.mean(walls):8.1f}±{(st.pstdev(walls) if len(walls) > 1 else 0):4.1f} "
              f"{st.mean(r['page_sec_mean'] for r in rs):10.2f} {rs[0]['clicks']:7d} {rs[0]['checked']:8d}  "
              f"{'yes' if fs == base_find else 'NO: ' + str(len(fs ^ base_find)) + ' differ'}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("url")
    r.add_argument("dir", type=Path)
    r.add_argument("--pages", type=int, default=200)
    r.add_argument("--depth", type=int, default=10)
    p = sub.add_parser("replay")
    p.add_argument("dir", type=Path)
    p.add_argument("--label", default="current")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=None, help="pages checked at the same time")
    p.add_argument("--cores", type=int, default=None, help="use only the first N logical CPUs (imitate a smaller PC)")
    p.add_argument("--expand", choices=["current", "legacy"], default="current",
                   help="legacy = the 'more' clicking of 0.1.3 and before (for before/after measurements)")
    c = sub.add_parser("compare")
    c.add_argument("dir", type=Path)
    a = ap.parse_args()
    if a.cmd == "record":
        record(a.url, a.dir, a.pages, a.depth)
    elif a.cmd == "replay":
        replay(a.dir, a.label, a.repeat, a.expand == "legacy", a.concurrency, a.cores)
    else:
        compare(a.dir)


if __name__ == "__main__":
    main()
