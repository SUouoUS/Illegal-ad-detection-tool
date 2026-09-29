"""Browser side of a scan: render pages, walk frames, collect, detect, locate, write.

Scope: the entry page, same-site sub pages reached by links (BFS, see crawl.Policy),
and every (nested) iframe inside each page. Safe "load more" controls are clicked;
state-changing links and form submissions are never used.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag

from playwright.async_api import Browser, Frame, Page, async_playwright

from . import VERSION, result_writer
from .crawl import Policy
from .detect import analyze_frame
from .urlnorm import compare_key
from .paths import resource_dir, result_dir, runs_dir, setup_browser_env

COLLECT_JS = (resource_dir() / "collector.js").read_text(encoding="utf-8")
SELECTOR_JS = (resource_dir() / "selector.js").read_text(encoding="utf-8")

# Selector for an <iframe>/<frame> element as seen from its parent document.
IFRAME_SEL_JS = """(el) => {
  const tag = el.localName;
  const attr = el.getAttribute('src');
  if (attr && !/^\\s*(about:|javascript:|data:)/i.test(attr)) {
    const abs = new URL(attr, document.baseURI).href;
    const same = Array.from(document.querySelectorAll(tag)).filter(f => {
      const a = f.getAttribute('src');
      try { return a && new URL(a, document.baseURI).href === abs; } catch (e) { return false; }
    });
    const base = `${tag}[src="${abs.replace(/"/g, '\\\\"')}"]`;
    if (same.length === 1) return { selector: base, verified: true, src: abs };
    const k = same.indexOf(el) + 1;
    return { selector: `${base}:nth-of-type(${Array.from(el.parentElement.children).filter(c => c.localName === tag).indexOf(el) + 1})`, verified: false, src: abs, note: `same src x${same.length} (#${k})` };
  }
  window.__cwNodes = [el];
  return { selector: null, verified: false, src: attr || '' };
}"""

# Links a user could follow from this document: href, area, onclick/data-* URLs.
LINKS_JS = """() => {
  const NAV = 'header, nav, footer, aside, [role=navigation], [role=banner], [role=contentinfo], [role=complementary]';
  const MAIN = 'main, article, [role=main], #content, #contents, .content, .contents, #container, .container, table, .board, .bbs';
  const area = (el) => el.closest(NAV) ? 'nav' : (el.closest(MAIN) ? 'main' : 'other');
  const out = new Map();
  const add = (u, el) => {
    try {
      if (!u) return;
      const abs = new URL(u, document.baseURI).href;
      const a = area(el);
      const prev = out.get(abs);
      if (!prev || (prev !== 'main' && a === 'main')) out.set(abs, a);
    } catch (e) {}
  };
  for (const a of document.querySelectorAll('a[href], area[href]')) add(a.getAttribute('href'), a);
  const rx = /(?:location(?:\\.href)?\\s*=|location\\.(?:assign|replace)\\(|window\\.open\\()\\s*['"]([^'"]+)['"]/g;
  for (const el of document.querySelectorAll('[onclick]')) {
    const code = el.getAttribute('onclick') || '';
    let m; while ((m = rx.exec(code))) add(m[1], el);
  }
  for (const el of document.querySelectorAll('[data-href], [data-url], [data-link]')) add(el.dataset.href || el.dataset.url || el.dataset.link, el);
  return Array.from(out, ([url, area]) => [url, area]).slice(0, 3000);
}"""

# Controls that reveal more content (comments, list items). Real links are crawled
# instead of clicked; anything that could change state is excluded.
EXPAND_JS = """() => {
  const MORE = /(더\\s*보기|댓글\\s*(보기|열기|펼치기|더)|답글\\s*(보기|열기)|펼치기|펼쳐\\s*보기|전체\\s*보기|load\\s*more|show\\s*more|view\\s*more|^more$)/i;
  const BAD = /(삭제|등록|작성|로그아웃|로그인|신고|탈퇴|수정|저장|전송|제출|확인|구매|결제|주문|좋아요|추천|공감|구독|신청|취소|다운로드|인쇄|공유|닫기|접기)/;
  window.__cwClicked = window.__cwClicked || new WeakSet();
  const out = [];
  for (const el of document.querySelectorAll('button, a, [role=button], summary, input[type=button]')) {
    if (window.__cwClicked.has(el) || el.disabled) continue;
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
    if (!t || t.length > 30 || !MORE.test(t) || BAD.test(t)) continue;
    if (el.tagName === 'A') { const h = el.getAttribute('href') || ''; if (h && !/^\\s*(#|javascript:)/i.test(h)) continue; }
    if (el.tagName === 'BUTTON' && el.closest('form') && (el.getAttribute('type') || 'submit') === 'submit') continue;
    if (!el.getClientRects().length) continue;
    out.push(el);
  }
  window.__cwExpand = out;
  return out.map(e => (e.innerText || e.value || '').trim().slice(0, 30));
}"""

SCROLL_JS = """async () => {
  const h = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  for (let k = 1; k <= 4; k++) { window.scrollTo(0, h() * k / 4); await new Promise(r => setTimeout(r, 120)); }
  window.scrollTo(0, 0);
}"""

# Resolve when the DOM has been quiet for `quiet` ms (or after `max` ms).
QUIET_JS = """([quiet, max]) => new Promise((resolve) => {
  let last = performance.now(); const t0 = last;
  const mo = new MutationObserver(() => { last = performance.now(); });
  mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  const tick = () => {
    const now = performance.now();
    if (now - last >= quiet || now - t0 >= max) { mo.disconnect(); resolve(Math.round(now - t0)); }
    else setTimeout(tick, 50);
  };
  setTimeout(tick, 50);
})"""


@dataclass
class ScanConfig:
    headless: bool = True
    nav_timeout_ms: int = 20000
    idle_wait_ms: int = 1000          # short network-quiet wait (not a hard requirement)
    load_wait_ms: int = 3000          # after DOMContentLoaded, wait for 'load' at most this long
    settle_ms: int = 300
    viewport: tuple[int, int] = (1920, 1080)
    block_resources: bool = True
    max_frame_depth: int = 6
    eval_timeout_s: float = 15.0     # one in-page script call
    page_budget_s: float = 90.0      # whole page incl. frames
    total_budget_s: float = 1500.0   # whole scan (official limit is 30 min)
    concurrency: int = 4
    max_pages: int = 50
    max_depth: int = 2
    per_pattern: int = 10            # pages per URL pattern (path with ids generalised + query names)
    quiet_ms: int = 500              # DOM quiet window after load
    quiet_max_ms: int = 2000         # ... but never wait longer than this
    expand_clicks: int = 12          # safe "load more" clicks per page
    follow_links: bool = True


@dataclass
class Progress:
    state: str = "idle"            # idle | running | done | stopped | failed
    entry_url: str = ""
    started_at: str = ""
    finished_at: str = ""
    elapsed_sec: float = 0.0
    pages_done: int = 0
    pages_failed: int = 0
    pages_queued: int = 0
    pages_skipped: int = 0         # links excluded by limits (depth, pattern, page count, time)
    pages_over_limit: int = 0      # queued links beyond the page limit (will not be visited)
    pages_limit_excluded: int = 0  # links excluded by the page limit (queued beyond it + dropped)
    frames_done: int = 0
    frames_failed: int = 0
    current_url: str = ""
    findings: int = 0
    elements: int = 0
    end_reason: str = ""
    result_path: str = ""
    run_id: str = ""
    errors: list[str] = field(default_factory=list)


class Scan:
    """One scan run. Thread-safe progress; cancel via stop()."""

    def __init__(self, entry_url: str, cfg: ScanConfig | None = None, out_dir: Path | None = None):
        self.entry_url = entry_url.strip()
        self.cfg = cfg or ScanConfig()
        self.out_dir = Path(out_dir) if out_dir else result_dir()
        self.progress = Progress(entry_url=self.entry_url)
        self.cancel = threading.Event()
        self.record: dict = {}
        self._t0 = 0.0
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.progress.run_id = self.run_id

    def stop(self):
        self.cancel.set()

    # -------------------------------------------------------------- entry
    def run(self) -> dict:
        """Blocking run (own event loop). Always writes result.json."""
        setup_browser_env()
        started = datetime.now().astimezone()
        self._t0 = time.monotonic()
        p = self.progress
        p.state, p.started_at = "running", result_writer.iso(started)
        pages: list[dict] = []
        findings: list[dict] = []
        excluded: list[dict] = []
        # The final state is decided here but published only after result.json and the run
        # record are on disk: the screen treats a non-running state as "results are ready".
        state, reason = "done", ""
        try:
            asyncio.run(self._run(pages, findings, excluded))
            limits = self._limit_notes()
            if self.cancel.is_set():
                state, reason = "stopped", "사용자 중단" + (f" · {limits}" if limits else "")
            else:
                failed = [pg for pg in pages if pg["status"] not in ("ok", "duplicate")]
                if pages and all(pg["status"] == "failed" for pg in pages):
                    state = "failed"
                    reason = "페이지를 불러오지 못해 점검하지 못함(광고 없음이 아님): " + pages[0]["error"][:120]
                else:
                    reason = "완료" if not failed else f"완료(일부 실패 {len(failed)}건)"
                    if limits:
                        reason += f" · {limits}"
        except Exception as e:  # noqa: BLE001 - result must still be written
            state, reason = "failed", f"오류: {e}"
            p.errors.append(traceback.format_exc(limit=5))
        p.current_url = "결과 저장 중"
        finished = datetime.now().astimezone()
        elapsed = time.monotonic() - self._t0
        try:
            doc = result_writer.build(self.entry_url, started, finished, findings, elapsed)
            path = result_writer.write(doc, self.out_dir / "result.json")
            self.record = {
                "tool_version": VERSION, "run_id": self.run_id, "entry_url": self.entry_url,
                "state": state, "end_reason": reason,
                "started_at": doc["meta"]["started_at"], "finished_at": doc["meta"]["finished_at"],
                "elapsed_sec": doc["meta"]["elapsed_sec"], "result_path": str(path),
                "pages": pages, "findings": findings, "excluded": excluded, "errors": p.errors,
                "crawl": self._crawl_record(),
                "result": doc,
            }
            rd = runs_dir() / self.run_id
            rd.mkdir(parents=True, exist_ok=True)
            tmp = rd / "scan.json.tmp"
            tmp.write_text(json.dumps(self.record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, rd / "scan.json")     # never expose a half-written record
        except Exception as e:
            p.state, p.end_reason = "failed", f"결과 파일 저장 실패: {e}"
            p.elapsed_sec = round(elapsed, 2)
            raise
        # publish: details first, state last
        p.finished_at, p.elapsed_sec, p.result_path = doc["meta"]["finished_at"], doc["meta"]["elapsed_sec"], str(path)
        p.findings = len(doc["findings"])
        p.current_url = ""
        p.end_reason = reason
        p.state = state
        return self.record

    LIMIT_REASONS = ("시간 예산 도달", "최대 페이지 수 제한", "같은 형태 주소 제한", "깊이 제한", "중단으로 미방문")
    visited = 0

    def _remaining(self) -> float:
        return self.cfg.total_budget_s - (time.monotonic() - self._t0)

    def _limit_notes(self) -> str:
        pol = getattr(self, "policy", None)
        if pol is None:
            return ""
        label = {"시간 예산 도달": "시간 예산 도달로 미방문", "최대 페이지 수 제한": "최대 페이지 수 제한으로 미방문",
                 "같은 형태 주소 제한": "같은 형태 주소 제한으로 미방문", "깊이 제한": "깊이 제한으로 미방문",
                 "중단으로 미방문": "중단으로 미방문"}
        notes = []
        for reason in self.LIMIT_REASONS:
            n = pol.skipped_n.get(reason, 0)
            if n:
                notes.append(f"{label[reason]} {n}개")
        return ", ".join(notes)

    def _crawl_record(self) -> dict:
        pol = getattr(self, "policy", None)
        if pol is None:
            return {}
        return {"limits": {k: getattr(self.cfg, k) for k in ("max_pages", "max_depth", "per_pattern", "total_budget_s", "concurrency")},
                "pages_admitted": pol.admitted, "skipped_count": dict(pol.skipped_n), "skipped_sample": pol.skipped}

    async def _run(self, pages, findings, excluded):
        p = self.progress
        self.policy = pol = Policy(self.entry_url, self.cfg.max_pages, self.cfg.max_depth, self.cfg.per_pattern)
        self.analyzed: set[str] = set()
        async with async_playwright() as pw:
            browser = await self._launch(pw)
            try:
                ctx = await browser.new_context(
                    viewport={"width": self.cfg.viewport[0], "height": self.cfg.viewport[1]},
                    ignore_https_errors=True, locale="ko-KR", accept_downloads=False,
                )
                if self.cfg.block_resources:
                    await ctx.route("**/*", _block_heavy)
                queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
                seq = 0
                queue.put_nowait((0.0, seq, self.entry_url, 0, False))
                p.pages_queued = 1
                in_flight: set[str] = set()
                self.visited = 0

                def update_counts():
                    slots = max(0, self.cfg.max_pages - self.visited - len(in_flight))
                    waiting = queue.qsize()
                    p.pages_queued = min(waiting, slots) + len(in_flight)   # pages that will really be checked
                    p.pages_over_limit = max(0, waiting - slots)
                    p.pages_skipped = sum(pol.skipped_n.get(r, 0) for r in self.LIMIT_REASONS)
                    p.pages_limit_excluded = p.pages_over_limit + pol.skipped_n.get("최대 페이지 수 제한", 0)

                async def worker():
                    nonlocal seq
                    while True:
                        item = await queue.get()
                        try:
                            if item[2] is None:
                                return
                            prio, _, url, depth, requeued = item
                            if not requeued and pol.is_superseded(url):
                                # a variant (extra query) of a page found later: check distinct pages first
                                seq += 1
                                queue.put_nowait((prio + 150, seq, url, depth, True))
                                continue
                            if self.cancel.is_set():
                                pol.note("중단으로 미방문", url)
                                continue
                            if self.visited >= self.cfg.max_pages:
                                pol.note("최대 페이지 수 제한", url)
                                continue
                            # keep a reserve for writing results (10 s, less for short test budgets)
                            if self._remaining() < min(10.0, self.cfg.total_budget_s * 0.1):
                                pol.note("시간 예산 도달", url)
                                continue
                            self.visited += 1
                            in_flight.add(url)
                            p.current_url = url
                            try:
                                rec = await self._scan_page(ctx, url, depth, findings, excluded)
                            finally:
                                in_flight.discard(url)
                            links = rec.pop("links", [])
                            rec["links_found"] = len(links)
                            pages.append(rec)
                            if depth == 0 and rec["status"] != "failed":
                                pol.allow_site_of(rec["url"])
                            if self.cfg.follow_links and not self.cancel.is_set():
                                for link, area in links:
                                    d = pol.child_depth(rec["url"], link, depth)
                                    u = pol.admit(link, d)
                                    if u:
                                        seq += 1
                                        queue.put_nowait((pol.priority(u, d, area), seq, u, d, False))
                        finally:
                            update_counts()
                            if in_flight:
                                p.current_url = next(iter(in_flight))
                            queue.task_done()

                workers = [asyncio.create_task(worker()) for _ in range(max(1, self.cfg.concurrency))]
                await queue.join()
                for _ in workers:
                    queue.put_nowait((float("inf"), 0, None, 0, True))
                await asyncio.gather(*workers)
                p.pages_over_limit = 0
                p.pages_queued = 0
                await ctx.close()
            finally:
                await browser.close()

    async def _launch(self, pw) -> Browser:
        try:
            return await pw.chromium.launch(headless=self.cfg.headless)
        except Exception as first:  # bundled Chromium missing -> installed Edge
            try:
                b = await pw.chromium.launch(headless=self.cfg.headless, channel="msedge")
                self.progress.errors.append("동봉 브라우저를 찾지 못해 설치된 Microsoft Edge로 검사함")
                return b
            except Exception:
                raise first

    # -------------------------------------------------------------- page
    async def _ev(self, target, js, arg=None):
        """evaluate() with a timeout: a frame that never answers must not hang the scan."""
        coro = target.evaluate(js) if arg is None else target.evaluate(js, arg)
        return await asyncio.wait_for(coro, timeout=self.cfg.eval_timeout_s)

    async def _scan_page(self, ctx, url, depth, findings, excluded) -> dict:
        """Run one page under a time budget; stop requests interrupt it immediately."""
        p = self.progress
        p.current_url = url
        rec = {"requested_url": url, "url": url, "depth": depth, "status": "ok", "error": "", "frames": [],
               "http_status": None, "expanded": [], "links": []}
        page: Page = await ctx.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        page.on("popup", lambda pp: asyncio.ensure_future(pp.close()))
        work = asyncio.create_task(self._page_work(page, url, rec, findings, excluded))
        loop = asyncio.get_running_loop()
        t_start = loop.time()
        budget = max(5.0, min(self.cfg.page_budget_s, self._remaining() - 5))
        deadline = loop.time() + budget
        interrupted = ""
        try:
            while not work.done():
                if self.cancel.is_set():
                    interrupted = "stopped"
                elif loop.time() > deadline:
                    interrupted = "timeout"
                if interrupted:
                    work.cancel()
                    break
                await asyncio.wait({work}, timeout=0.2)
            try:
                await work
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                rec["status"], rec["error"] = "failed", str(e).splitlines()[0]
            if interrupted == "stopped":
                rec["status"], rec["error"] = "stopped", "사용자 중단으로 점검을 끝내지 못함"
            elif interrupted == "timeout":
                rec["status"] = "failed"
                rec["error"] = f"페이지 처리 시간 초과({budget:.0f}초) — 일부만 점검됨"
            rec["elapsed_sec"] = round(loop.time() - t_start, 2)
            if rec["status"] in ("failed", "stopped", "http_error"):
                p.pages_failed += 1
            elif rec["status"] != "duplicate":
                p.pages_done += 1
        finally:
            try:
                await asyncio.wait_for(page.close(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        return rec

    async def _page_work(self, page: Page, url, rec, findings, excluded):
        t = time.monotonic()
        timing = rec.setdefault("timing", {})

        def lap(name):
            nonlocal t
            now = time.monotonic()
            timing[name] = round(now - t, 2)
            t = now
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.cfg.nav_timeout_ms)
        except Exception as e:
            # partial load is still worth analysing if the DOM exists
            rec["error"] = f"로딩 지연/실패: {str(e).splitlines()[0]}"
            resp = None
        if resp is not None:
            rec["http_status"] = resp.status
            if resp.status >= 400:
                rec["status"], rec["error"] = "http_error", f"HTTP {resp.status}"
        lap("load")
        if page.url.startswith(("chrome-error:", "about:blank")):
            # nothing was loaded: this is a collection failure, never "no ads"
            rec["status"] = "failed"
            return
        await self._settle(page)
        lap("settle")
        final = urldefrag(page.url)[0]
        rec["url"] = final
        key = compare_key(final)
        if key in self.analyzed:
            # redirected to a page that was already analysed: do not report it twice
            rec["status"] = "duplicate"
            rec["error"] = "이미 점검한 페이지로 이동됨"
            return
        self.analyzed.add(key)
        await self._expand(page, rec)
        lap("expand")
        await self._walk(page.main_frame, [], final, rec, findings, excluded, 0)
        lap("analyse")
        if rec["status"] == "ok" and rec["error"]:
            rec["status"] = "partial"

    async def _settle(self, page: Page):
        # 'load' and network quiet are waited for only briefly: ad-heavy pages never go quiet
        try:
            await page.wait_for_load_state("load", timeout=self.cfg.load_wait_ms)
        except Exception:
            pass
        # force lazy iframes to load
        for f in page.frames:
            try:
                await self._ev(f, "document.querySelectorAll('iframe[loading=lazy]').forEach(f => f.loading = 'eager')")
            except Exception:
                pass
        try:
            await page.wait_for_load_state("networkidle", timeout=self.cfg.idle_wait_ms)
        except Exception:
            pass
        await self._quiet(page)

    async def _quiet(self, page: Page):
        try:
            await self._ev(page.main_frame, QUIET_JS, [self.cfg.quiet_ms, self.cfg.quiet_max_ms])
        except Exception:  # noqa: BLE001
            await page.wait_for_timeout(self.cfg.settle_ms)

    async def _expand(self, page: Page, rec: dict):
        """Scroll for lazy content, then click safe 'load more' controls (main document)."""
        try:
            await self._ev(page.main_frame, SCROLL_JS)
        except Exception:  # noqa: BLE001
            pass
        start = urldefrag(page.url)[0]
        clicks = 0
        for _ in range(4):                       # new "more" buttons may appear after a click
            if clicks >= self.cfg.expand_clicks or self.cancel.is_set():
                break
            try:
                labels = await self._ev(page.main_frame, EXPAND_JS)
            except Exception:  # noqa: BLE001
                break
            if not labels:
                break
            for k, label in enumerate(labels):
                if clicks >= self.cfg.expand_clicks:
                    break
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
                    # the control navigated away: keep the target as a link, reload the page
                    rec["links"].append((page.url, "main"))
                    rec["expanded"].append(f"(이동 발생: {label})")
                    await page.goto(start, wait_until="domcontentloaded", timeout=self.cfg.nav_timeout_ms)
                    await self._settle(page)
                    return
            await self._quiet(page)

    async def _walk(self, frame: Frame, chain: list[str], page_url: str, rec, findings, excluded, depth):
        p = self.progress
        if self.cancel.is_set():
            return
        finfo = {"chain": list(chain), "frame_url": frame.url, "status": "ok", "elements": 0, "findings": 0}
        rec["frames"].append(finfo)
        if frame.url.startswith("chrome-error:"):
            finfo["status"] = "blocked"      # ad/analytics frame blocked, or failed to load
            return
        try:
            if self.cfg.follow_links:
                try:
                    rec["links"].extend(await self._ev(frame, LINKS_JS))
                except Exception:  # noqa: BLE001
                    pass
            data = await self._ev(frame, COLLECT_JS)
            nodes = data["nodes"]
            finfo["elements"] = len(nodes)
            p.elements += len(nodes)
            # CPU work off the event loop so other pages keep loading meanwhile
            cands, excl = await asyncio.to_thread(analyze_frame, nodes)
            if cands:
                sels = await self._ev(frame, SELECTOR_JS, [c.index for c in cands])
                sel_by = {s["index"]: s for s in sels}
                for c in cands:
                    s = sel_by.get(c.index, {})
                    if not s.get("selector"):
                        finfo.setdefault("unlocated", []).append(c.evidence[:80])
                        continue
                    loc = " >>> ".join(chain + [s["selector"]])
                    findings.append({
                        "url": page_url, "location": loc, "technique": c.technique,
                        "evidence_text": c.evidence, "restored_text": c.restored,
                        "ad_reasons": c.ad_reasons, "technique_reasons": c.tech_reasons,
                        "changes": c.changes, "selector_verified": bool(s.get("verified")),
                        "frame_url": frame.url, "frame_chain": list(chain),
                        "unit_key": f"{page_url}|{' >>> '.join(chain)}|{c.unit}",
                        "rect": next((n["rect"] for n in nodes if n["i"] == c.index), None),
                    })
                    finfo["findings"] += 1
            for x in excl:
                excluded.append({"url": page_url, "frame_chain": list(chain), **x})
            p.frames_done += 1
            p.findings = len({(f["url"], f["location"], f["technique"]) for f in findings})
        except Exception as e:  # noqa: BLE001
            finfo["status"], finfo["error"] = "failed", str(e).splitlines()[0]
            p.frames_failed += 1
            return
        if depth >= self.cfg.max_frame_depth:
            return
        for child in frame.child_frames:
            if self.cancel.is_set():
                return
            try:
                el = await asyncio.wait_for(child.frame_element(), timeout=self.cfg.eval_timeout_s)
                info = await self._ev(el, IFRAME_SEL_JS)
                if not info.get("selector"):
                    sels = await self._ev(frame, SELECTOR_JS, [0])
                    info["selector"] = sels[0]["selector"]
                    info["verified"] = sels[0]["verified"]
            except Exception as e:  # noqa: BLE001
                rec["frames"].append({"chain": list(chain), "frame_url": child.url, "status": "failed",
                                      "error": f"iframe 위치 확인 실패: {str(e).splitlines()[0]}"})
                p.frames_failed += 1
                continue
            await self._walk(child, chain + [info["selector"]], page_url, rec, findings, excluded, depth + 1)


# Ad-tech and analytics networks: their requests never carry page content we inspect,
# but they keep pages busy (no network quiet, endless DOM changes, many iframes).
BLOCK_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "googletagservices.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "adservice.google.com", "adsystem.com",
    "amazon-adsystem.com", "facebook.net", "connect.facebook.com", "scorecardresearch.com", "criteo.com",
    "criteo.net", "taboola.com", "outbrain.com", "adnxs.com", "rubiconproject.com", "pubmatic.com",
    "openx.net", "casalemedia.com", "moatads.com", "hotjar.com", "clarity.ms", "mixpanel.com",
    "segment.io", "newrelic.com", "nr-data.net", "wcs.naver.net", "wcs.naver.com", "adpost.naver.com",
    "tivan.naver.com", "veta.naver.com", "siape.veta.naver.com", "kakaoad.com", "adfit.kakao.com",
    "t1.kakaocdn.net/kas", "mobon.net", "dable.io", "tenping.kr", "realclick.co.kr", "adop.cc",
    "cauly.co.kr", "acecounter.com", "logger.co.kr", "beusable.net",
)
BLOCK_TYPES = ("image", "media", "font", "ping", "beacon")


def _blocked_host(url: str) -> bool:
    try:
        rest = url.split("://", 1)[1]
    except IndexError:
        return False
    host = rest.split("/", 1)[0].split(":")[0].lower()
    hostpath = host + "/" + (rest.split("/", 1)[1] if "/" in rest else "")
    return any(host == h or host.endswith("." + h) or ("/" in h and hostpath.startswith(h)) for h in BLOCK_HOSTS)


async def _block_heavy(route):
    req = route.request
    if req.resource_type in BLOCK_TYPES or _blocked_host(req.url):
        await route.abort()
    else:
        await route.continue_()


def run_scan(url: str, out_dir: Path | None = None, cfg: ScanConfig | None = None) -> dict:
    return Scan(url, cfg, out_dir).run()
