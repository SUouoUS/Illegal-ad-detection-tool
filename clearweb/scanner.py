"""Browser side of a scan: render pages, walk frames, collect, detect, locate, write.

Scope: the entry page, same-site sub pages reached by links (BFS, see crawl.Policy),
and every (nested) iframe inside each page. Safe "load more" controls are clicked;
state-changing links and form submissions are never used.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag

from playwright.async_api import Browser, Frame, Page, async_playwright

from . import VERSION, ocr, result_writer
from .crawl import Policy
from .detect import analyze_frame, judge_image
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
  // paging controls: explicitly named containers, or a number/next/prev link among other number links
  const PAGER = '[class*=paging],[class*=pagination],[class*=pager],[class*=paginate],[class*=page-nav],[class*=pagenav],[class*=page_nav],[class*=page-num],[class*=pagenum],[id*=paging],[id*=pagination],[id*=pager],nav[aria-label*="페이지"],nav[aria-label*="page" i]';
  const NUMLIKE = /^\\s*(\\d{1,4}|다음|이전|처음|마지막|맨\\s*앞|맨\\s*뒤|next|prev(ious)?|first|last|[<>«»‹›]{1,2})\\s*$/i;
  const isPager = (el) => {
    if (el.closest(PAGER)) return true;
    if (!NUMLIKE.test((el.textContent || '').trim()) || !el.parentElement) return false;
    return Array.from(el.parentElement.querySelectorAll('a')).filter((a) => /^\\s*\\d{1,4}\\s*$/.test(a.textContent || '')).length >= 2;
  };
  const out = new Map();
  const add = (u, el) => {
    try {
      if (!u) return;
      const abs = new URL(u, document.baseURI).href;
      const a = area(el), pg = isPager(el);
      const prev = out.get(abs);
      if (!prev) out.set(abs, [a, pg]);
      else out.set(abs, [prev[0] !== 'main' && a === 'main' ? a : prev[0], prev[1] || pg]);
    } catch (e) {}
  };
  for (const a of document.querySelectorAll('a[href], area[href]')) add(a.getAttribute('href'), a);
  const rx = /(?:location(?:\\.href)?\\s*=|location\\.(?:assign|replace)\\(|window\\.open\\()\\s*['"]([^'"]+)['"]/g;
  for (const el of document.querySelectorAll('[onclick]')) {
    const code = el.getAttribute('onclick') || '';
    let m; while ((m = rx.exec(code))) add(m[1], el);
  }
  for (const el of document.querySelectorAll('[data-href], [data-url], [data-link]')) add(el.dataset.href || el.dataset.url || el.dataset.link, el);
  return Array.from(out, ([url, v]) => [url, v[0], v[1]]).slice(0, 3000);
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

# <img> candidates of a document for OCR (entry page only). Picks the URL the browser
# chose (currentSrc), else the largest srcset candidate, else src; common lazy-loading
# attributes replace placeholders. DOM visibility is recorded separately from the ad
# judgement. Elements are kept in window.__cwNodes for selector building.
IMG_JS = """() => {
  const imgs = Array.from(document.images);
  const abs = (u) => { try { return u ? new URL(u, document.baseURI).href : ''; } catch (e) { return ''; } };
  const bestSrcset = (ss) => {
    let best = '', score = -1;
    for (const part of (ss || '').split(',')) {
      const [u, d] = part.trim().split(/\\s+/);
      if (!u) continue;
      const v = d ? parseFloat(d) * (d.endsWith('x') ? 1000 : 1) : 1;
      if (v > score) { score = v; best = u; }
    }
    return best;
  };
  const LAZY = ['data-src', 'data-original', 'data-lazy-src', 'data-lazy', 'data-url', 'data-echo'];
  const placeholder = (u) => !u || /^data:image\\/(gif|svg)/i.test(u) && u.length < 400 || /(blank|spacer|placeholder|loading|transparent)\\.(gif|png|svg)/i.test(u);
  return imgs.map((im, k) => {
    // the image the browser actually selected (currentSrc) comes first; the largest srcset
    // candidate is kept as an alternative that is tried only when the first one is not suspect
    const big = bestSrcset(im.getAttribute('srcset'));
    let u = im.currentSrc || im.getAttribute('src') || big || '';
    const lazyAttr = LAZY.map((a) => im.getAttribute(a)).find(Boolean) || bestSrcset(im.getAttribute('data-srcset'));
    let via = im.currentSrc ? 'currentSrc' : (im.getAttribute('src') ? 'src' : 'srcset');
    if (placeholder(u) && lazyAttr) { u = lazyAttr; via = 'lazy-attr'; }
    else if (placeholder(u) && big) { u = big; via = 'srcset'; }
    const alt = big && abs(big) !== abs(u) ? abs(big) : '';
    const r = im.getBoundingClientRect();
    const hidden = [];
    let op = 1;
    for (let e = im; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none') { hidden.push('display:none'); break; }
      if (cs.visibility === 'hidden') hidden.push('visibility:hidden');
      op *= parseFloat(cs.opacity) || 0;
    }
    if (op <= 0.05) hidden.push('opacity:' + op.toFixed(2));
    if (r.width > 0 && (r.right + scrollX <= 0 || r.bottom + scrollY <= 0)) hidden.push('화면 밖 좌표');
    return { k, url: u.startsWith('data:') ? u : abs(u), via, shown: abs(im.currentSrc || ''), alt_url: alt, alt: im.getAttribute('alt') || '',
             rendered: [Math.round(r.width), Math.round(r.height)], hidden: Array.from(new Set(hidden)) };
  });
}"""

# Bot-protection / challenge pages (e.g. Cloudflare Turnstile): the real content was not
# served. Recognised only to report the reason clearly - they are never bypassed.
CHALLENGE_JS = """() => ({
  title: document.title || '',
  text: (document.body ? document.body.innerText : '').trim().length,
  frames: Array.from(document.querySelectorAll('iframe')).map(f => f.src || '').filter(Boolean),
})"""
CHALLENGE_FRAME = re.compile(r"challenges\.cloudflare\.com|/cdn-cgi/challenge-platform|hcaptcha\.com/captcha|captcha-delivery\.com", re.I)
CHALLENGE_TITLE = re.compile(r"just a moment|attention required|checking your browser|ddos protection|잠시만 기다|보안 확인", re.I)

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
    ocr_enabled: bool = False        # text in <img> of every visited page and its frames (optional)
    ocr_limits: ocr.OcrLimits = field(default_factory=ocr.OcrLimits)


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
    images_total: int = 0          # OCR: image candidates on the entry page
    images_done: int = 0
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
        self.images: list[dict] = []
        self.extra_ads: list[dict] = []          # ad text without any official technique (not in result.json)
        self.ocr_record: dict = {"enabled": self.cfg.ocr_enabled, "ran": False}
        self._img_fetched: dict[str, tuple[bytes | None, str]] = {}   # url -> (bytes, reason), whole scan
        self._img_done: dict[str, dict] = {}                          # sha1 -> OCR result, whole scan
        self._img_seen: set[tuple[str, str]] = set()                  # (page url, location)
        self._ocr_stats = {"ocr_sec": 0.0, "download_sec": 0.0, "cache_hits": 0, "spent_sec": 0.0, "processed": 0}
        self._ocr_cov = {"pages_collected": 0, "frames_collected": 0, "img_elements": 0, "pages_not_collected": 0}
        self._ocr_lock: asyncio.Lock | None = None

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
                if pages and all(pg["status"] in ("failed", "blocked", "http_error") for pg in pages):
                    state = "failed"
                    reason = "검사하지 못함 — 본문을 받지 못함(광고 없음이 아님): " + pages[0]["error"][:120]
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
            extra_path = None
            extra_file = self.out_dir / "result_extra.json"
            if self.ocr_record.get("ran"):
                extra = result_writer.build_extra(self.entry_url, started, finished, self._extra_findings(), elapsed)
                extra_path = result_writer.write_extra(extra, extra_file)
            elif extra_file.exists():
                # a result_extra.json left by an earlier scan must not look like this scan's result
                extra_file.unlink()
                self.ocr_record["removed_stale_extra"] = str(extra_file)
            self.record = {
                "tool_version": VERSION, "run_id": self.run_id, "entry_url": self.entry_url,
                "state": state, "end_reason": reason,
                "started_at": doc["meta"]["started_at"], "finished_at": doc["meta"]["finished_at"],
                "elapsed_sec": doc["meta"]["elapsed_sec"], "result_path": str(path),
                "pages": pages, "findings": findings, "excluded": excluded, "errors": p.errors,
                "crawl": self._crawl_record(),
                "images": self.images, "ocr": self.ocr_record,
                "extra_ads": self.extra_ads, "collection": self._collection_summary(pages),
                "result_extra_path": str(extra_path) if extra_path else "",
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

    # -------------------------------------------------------------- OCR (visited pages and frames)
    async def _collect_images(self, frame: Frame, page_url: str, chain: list[str]) -> list[dict]:
        """<img> candidates of one frame, with a verified selector. `chain` is the iframe
        path from the page to this frame (empty for the top document)."""
        cands = await self._ev(frame, "() => { const r = (" + IMG_JS + ")(); window.__cwNodes = Array.from(document.images); return r; }")
        if cands:
            sels = await self._ev(frame, SELECTOR_JS, [c["k"] for c in cands])
            by = {x["index"]: x for x in sels}
            for c in cands:
                sel = by.get(c["k"], {})
                c["selector"] = sel.get("selector") or ""
                c["selector_verified"] = bool(sel.get("verified"))
                c["page_url"] = page_url
                c["frame_chain"] = list(chain)
                c["frame_url"] = frame.url
        return cands

    async def _fetch_image(self, ctx, url: str, referer: str) -> tuple[bytes | None, str]:
        lim = self.cfg.ocr_limits
        if url.startswith("data:"):
            import base64
            head, _, body = url.partition(",")
            try:
                data = base64.b64decode(body) if ";base64" in head else body.encode("latin-1")
            except Exception:  # noqa: BLE001
                return None, "data URL을 해석하지 못함"
            return (data, "") if len(data) <= lim.max_bytes else (None, f"파일 크기 한도 초과({len(data):,}B)")
        if not url.startswith(("http://", "https://")):
            return None, "지원하지 않는 주소 형식(blob 등)"
        try:
            resp = await ctx.request.get(url, headers={"Referer": referer}, timeout=lim.download_timeout_s * 1000,
                                         max_redirects=5, fail_on_status_code=False)
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0]
            return None, "내려받기 시간 초과" if "Timeout" in msg else f"내려받기 실패: {msg[:80]}"
        try:
            if resp.status >= 400:
                return None, f"HTTP {resp.status}"
            size = int(resp.headers.get("content-length") or 0)
            if size > lim.max_bytes:
                return None, f"파일 크기 한도 초과({size:,}B)"
            data = await resp.body()
            if len(data) > lim.max_bytes:
                return None, f"파일 크기 한도 초과({len(data):,}B)"
            ctype = resp.headers.get("content-type", "")
            if "svg" in ctype:
                return None, "SVG 이미지는 지원하지 않음"
            return data, ""
        finally:
            await resp.dispose()

    async def _ocr_stage(self, ctx, rec: dict, candidates: list[dict] | None):
        """OCR the <img> candidates of one visited page (top document and frames).

        Runs after the page's text scan, one page at a time. Download and OCR caches, the
        image-count limits and the OCR time budget are shared by the whole scan. Failures
        are recorded per image and never affect the text results already collected."""
        lim = self.cfg.ocr_limits
        p = self.progress
        ok, msg = ocr.engine_status()
        self.ocr_record.update({"engine": ocr.ENGINE, "engine_detail": msg, "limits": lim.as_dict(), "available": ok,
                                "scope": "방문한 페이지의 img(최상위 문서와 iframe 안). CSS 배경·canvas·영상·광고 네트워크 제외"})
        if not ok:
            self.ocr_record["error"] = msg
            return
        if candidates is None:                       # page not loaded / blocked: nothing was collected
            self._ocr_cov["pages_not_collected"] += 1
            self.ocr_record.setdefault("not_collected", []).append({"url": rec.get("url", ""), "why": rec.get("error", "") or rec.get("status", "")})
            return
        self.ocr_record["ran"] = True
        self._ocr_cov["pages_collected"] += 1
        self._ocr_cov["frames_collected"] += len({tuple(c.get("frame_chain", [])) for c in candidates}) if candidates else 0
        self._ocr_cov["img_elements"] += len(candidates)
        img_dir = runs_dir() / self.run_id / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        p.images_total += len(candidates)
        t0 = time.monotonic()
        on_page = 0
        for c in candidates:
            location = " >>> ".join(c.get("frame_chain", []) + [c.get("selector", "")])
            key = (c.get("page_url", ""), location)
            if key in self._img_seen:
                p.images_done += 1
                continue
            self._img_seen.add(key)
            item = {"page_url": c.get("page_url", ""), "selector": c.get("selector", ""), "location": location,
                    "frame_chain": c.get("frame_chain", []), "frame_url": c.get("frame_url", ""), "page_depth": rec.get("depth"),
                    "selector_verified": c.get("selector_verified", False), "img_url": c.get("url", ""),
                    "src_via": c.get("via", ""), "shown_src": c.get("shown", ""), "alt_url": c.get("alt_url", ""),
                    "alt": c.get("alt", ""), "rendered": c.get("rendered"),
                    "dom_hidden": c.get("hidden", []), "status": "ok", "reason": "", "timing": {}}
            self.images.append(item)
            p.images_done += 1
            spent = self._ocr_stats["spent_sec"] + (time.monotonic() - t0)
            if self.cancel.is_set():
                item.update(status="stopped", reason="사용자 중단")
                continue
            if spent > lim.total_budget_s or self._remaining() < 15:
                item.update(status="timeout", reason=f"이미지 검사 전체 시간 한도({lim.total_budget_s:g}초) 또는 전체 검사 시간 예산 도달")
                continue
            if on_page >= lim.max_images:
                item.update(status="skipped", reason=f"페이지당 이미지 수 한도({lim.max_images}개) 초과")
                continue
            if self._ocr_stats["processed"] >= lim.max_total_images:
                item.update(status="skipped", reason=f"전체 이미지 수 한도({lim.max_total_images}개) 초과")
                continue
            if not item["img_url"]:
                item.update(status="skipped", reason="이미지 주소 없음")
                continue
            on_page += 1
            self._ocr_stats["processed"] += 1
            try:
                await asyncio.wait_for(self._ocr_one(ctx, item, img_dir), timeout=2 * lim.download_timeout_s + 30)
            except asyncio.TimeoutError:
                item.update(status="timeout", reason="이미지 한 장 처리 시간 초과")
            except Exception as e:  # noqa: BLE001
                item.update(status="failed", reason=f"처리 오류: {str(e).splitlines()[0][:120]}")
        self._ocr_stats["spent_sec"] += time.monotonic() - t0
        st = self._ocr_stats
        self.ocr_record.update({
            "total_sec": round(st["spent_sec"], 2), "ocr_sec": round(st["ocr_sec"], 3), "download_sec": round(st["download_sec"], 3),
            "cache_hits": st["cache_hits"], "processed": st["processed"], "coverage": dict(self._ocr_cov),
            "counts": {k: sum(1 for i in self.images if i["status"] == k) for k in ("ok", "skipped", "failed", "timeout", "stopped")},
            "levels": {lv: sum(1 for i in self.images if i.get("level") == lv) for lv in ("suspect", "uncertain", "none")}})

    RESULT_KEYS = ("status", "reason", "natural", "prep_note", "scale", "preview", "ocr_text", "ocr_blocks",
                   "level", "verdict", "sha1", "bytes")

    async def _ocr_source(self, ctx, url: str, referer: str, img_dir: Path, timing: dict) -> dict:
        """Download (cached by URL) and OCR (cached by content hash) one image source."""
        lim = self.cfg.ocr_limits
        st = self._ocr_stats
        t = time.monotonic()
        if url not in self._img_fetched:
            self._img_fetched[url] = await self._fetch_image(ctx, url, referer)
        data, why = self._img_fetched[url]
        timing["download"] = round(timing.get("download", 0) + time.monotonic() - t, 3)
        st["download_sec"] += time.monotonic() - t
        if data is None:
            return {"status": "failed" if not why.startswith(("파일 크기", "SVG", "지원하지")) else "skipped", "reason": why}
        h = ocr.sha1(data)
        if h in self._img_done:                      # same image content: reuse OCR result
            st["cache_hits"] += 1
            return {**self._img_done[h], "cache_hit": True}
        res: dict = {"status": "ok", "reason": "", "sha1": h, "bytes": len(data)}
        t = time.monotonic()
        try:
            prep = await asyncio.to_thread(ocr.prepare, data, lim)
        except ValueError as e:
            res.update(status="failed", reason=str(e))
            self._img_done[h] = res
            return res
        timing["prepare"] = round(timing.get("prepare", 0) + time.monotonic() - t, 3)
        w0, h0 = prep.orig_size
        res.update(natural=[w0, h0], prep_note=prep.note, scale=round(prep.scale, 3))
        if w0 < lim.min_natural[0] or h0 < lim.min_natural[1]:
            res.update(status="skipped", reason=f"아이콘 크기 이미지({w0}×{h0})")
            self._img_done[h] = res
            return res
        (img_dir / f"{h}.jpg").write_bytes(prep.thumb_jpeg)
        res["preview"] = f"{h}.jpg"
        t = time.monotonic()
        lines = await ocr.recognize(prep.png)
        timing["ocr"] = round(timing.get("ocr", 0) + time.monotonic() - t, 3)
        st["ocr_sec"] += time.monotonic() - t
        blocks = ocr.blocks(lines)
        for b in blocks:                              # back to original image pixels
            for k in ("x", "y", "w", "h"):
                b[k] = round(b[k] / prep.scale, 1)
        verdict = judge_image(blocks)
        res.update(ocr_text="\n".join(b["text"] for b in blocks), ocr_blocks=blocks, level=verdict["level"], verdict=verdict)
        self._img_done[h] = res
        return res

    async def _ocr_one(self, ctx, item: dict, img_dir: Path):
        referer = item.get("frame_url") or item["page_url"]
        res = await self._ocr_source(ctx, item["img_url"], referer, img_dir, item["timing"])
        # srcset: the displayed image was read first; a larger candidate is tried only if that
        # one is not already suspect (small previews are often unreadable)
        alt = item.get("alt_url")
        if alt and res.get("level") != "suspect" and not self.cancel.is_set():
            res2 = await self._ocr_source(ctx, alt, referer, img_dir, item["timing"])
            rank = {"suspect": 2, "uncertain": 1}
            if res2.get("status") == "ok" and rank.get(res2.get("level"), 0) > rank.get(res.get("level"), 0) or res.get("status") != "ok" and res2.get("status") == "ok":
                item["first_source"] = {"img_url": item["img_url"], "status": res.get("status"), "level": res.get("level"),
                                        "ocr_text": res.get("ocr_text", "")}
                item["img_url"], item["src_via"] = alt, "srcset(더 큰 후보)"
                res = res2
        item.update({k: v for k, v in res.items() if k in self.RESULT_KEYS or k == "cache_hit"})

    def _extra_findings(self) -> list[dict]:
        """result_extra.json rows: only images whose own text block meets the ad rules."""
        out = []
        for im in self.images:
            if im.get("level") != "suspect" or not im.get("selector"):
                continue
            b = im["ocr_blocks"][im["verdict"]["block"]]["text"] if im["verdict"].get("block") is not None else im.get("ocr_text", "")
            out.append({"url": im["page_url"], "location": im.get("location") or im["selector"],
                        "evidence_text": f"OCR 추출문: {b} (이미지: {im['img_url'][:300]})",
                        "extra_finding": "IMAGE_TEXT_AD(이미지 속 광고 의심 문구)"})
        return out

    def _collection_summary(self, pages: list[dict]) -> dict:
        """What was actually collected, so '0 findings' can be told apart from 'not scanned'."""
        st = lambda k: sum(1 for pg in pages if pg["status"] == k)  # noqa: E731
        o = self.ocr_record
        return {
            "text": {"pages": len(pages), "ok": st("ok") + st("partial"), "duplicate": st("duplicate"),
                     "blocked": st("blocked"), "http_error": st("http_error"), "failed": st("failed"), "stopped": st("stopped"),
                     "elements": self.progress.elements},
            "images": {"enabled": bool(o.get("enabled")), "ran": bool(o.get("ran")), "error": o.get("error", ""),
                       "found": len(self.images), **{k: sum(1 for i in self.images if i["status"] == k)
                                                    for k in ("ok", "failed", "skipped", "timeout", "stopped")}},
            "extra_ads": {"text": len(self.extra_ads),
                          "image": sum(1 for i in self.images if i.get("level") in ("suspect", "uncertain"))},
        }

    def _crawl_record(self) -> dict:
        pol = getattr(self, "policy", None)
        if pol is None:
            return {}
        return {"limits": {k: getattr(self.cfg, k) for k in ("max_pages", "max_depth", "per_pattern", "total_budget_s", "concurrency")},
                "pages_admitted": pol.admitted, "skipped_count": dict(pol.skipped_n), "skipped_sample": pol.skipped}

    async def _run(self, pages, findings, excluded):
        p = self.progress
        self.policy = pol = Policy(self.entry_url, self.cfg.max_pages, self.cfg.max_depth, self.cfg.per_pattern)
        self._ocr_lock = asyncio.Lock()
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
                            candidates = rec.pop("_images", None)
                            if self.cfg.ocr_enabled and rec["status"] != "duplicate":
                                async with self._ocr_lock:
                                    await self._ocr_stage(ctx, rec, candidates)
                            if self.cfg.follow_links and not self.cancel.is_set():
                                for link, area, *rest in links:
                                    d = pol.child_depth(rec["url"], link, depth, pager=bool(rest and rest[0]))
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
            if rec["status"] in ("failed", "stopped", "http_error", "blocked"):
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
        why = await self._challenge_reason(page, rec.get("http_status"))
        if why:
            rec["status"], rec["error"] = "blocked", why
            return
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
        if self.cfg.ocr_enabled:
            rec.setdefault("_images", [])            # collected per frame in _walk
        if rec["status"] == "ok" and rec["error"]:
            rec["status"] = "partial"

    async def _challenge_reason(self, page: Page, status) -> str:
        try:
            info = await self._ev(page.main_frame, CHALLENGE_JS)
        except Exception:  # noqa: BLE001
            return ""
        frame = any(CHALLENGE_FRAME.search(u) for u in info["frames"] + [f.url for f in page.frames])
        title = bool(CHALLENGE_TITLE.search(info["title"]))
        # only explicit evidence of a security check (challenge frame or challenge title);
        # a short 403/429/503 page alone is reported as a plain response error
        if (status in (403, 429, 503) and (frame or title)) or (title and frame):
            code = f"HTTP {status} · " if status else ""
            return f"{code}보안 확인(봇 차단) 화면이 표시되어 본문을 받지 못함(자동 검사 불가 사이트)"
        return ""

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
                    rec["links"].append((page.url, "main", False))
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
            visible: list[dict] = []
            cands, excl = await asyncio.to_thread(analyze_frame, nodes, True, visible)
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
            if visible:
                vsels = await self._ev(frame, SELECTOR_JS, [x["index"] for x in visible])
                vby = {x["index"]: x for x in vsels}
                known = {(e["url"], e["location"]) for e in self.extra_ads}
                for x in visible:
                    sel = vby.get(x["index"], {})
                    if not sel.get("selector"):
                        continue
                    loc = " >>> ".join(chain + [sel["selector"]])
                    if (page_url, loc) in known:
                        continue
                    known.add((page_url, loc))
                    self.extra_ads.append({"kind": "text", "url": page_url, "location": loc,
                                           "evidence_text": x["evidence"], "restored_text": x["restored"],
                                           "ad_reasons": x["ad_reasons"], "excluded_why": x["excluded_why"],
                                           "selector_verified": bool(sel.get("verified")), "frame_chain": list(chain)})
            if self.cfg.ocr_enabled:
                try:
                    imgs = await self._collect_images(frame, page_url, chain)
                    finfo["imgs"] = len(imgs)
                    rec.setdefault("_images", []).extend(imgs)
                except Exception as e:  # noqa: BLE001 - OCR must never cost the text results
                    finfo["image_collect_error"] = str(e).splitlines()[0]
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
