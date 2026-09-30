// ClearWeb screen. All page-derived text is inserted with textContent only.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ORDER = ["JAMO", "HOMOGLYPH", "TRANSPARENT", "OFFSCREEN"];
  const TECH = { HOMOGLYPH: "닮은꼴 문자", JAMO: "자모 분해", TRANSPARENT: "투명 텍스트", OFFSCREEN: "화면 밖 은닉" };
  // wording used in the one-sentence reason: [joining form, final form]
  const PHRASE = {
    JAMO: ["자모를 분해하고", "자모를 분해한"],
    HOMOGLYPH: ["닮은꼴 문자로 위장하고", "닮은꼴 문자로 위장한"],
    TRANSPARENT: ["투명 처리로 숨기고", "투명 처리로 숨긴"],
    OFFSCREEN: ["화면 밖으로 숨기고", "화면 밖으로 숨긴"],
  };
  const STATE = {
    idle: "미검사", running: "검사 중", done: "완료", zero: "완료 · 탐지 0건",
    partial: "완료 · 일부 실패", stopped: "중단됨", failed: "점검 실패", unscanned: "검사하지 못함",
  };
  let timer = null, groups = [], loadedRun = null, lastRec = null, pendingRun = null, loadFailures = 0;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const mmss = (s) => {
    s = Math.max(0, Math.floor(s || 0));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };

  // ------------------------------------------------------------ status
  function displayState(p, rec) {
    if (rec && rec.pages.length && rec.pages.every((x) => ["failed", "blocked", "http_error"].includes(x.status))) return "unscanned";
    if (p.state === "done") {
      const failed = rec ? rec.pages.filter((x) => !["ok", "duplicate"].includes(x.status)).length : p.pages_failed;
      if (failed > 0) return "partial";
      const imgHits = rec ? (rec.images || []).filter((x) => x.level === "suspect" || x.level === "uncertain").length + (rec.extra_ads || []).length : 0;
      if ((rec ? rec.result.findings.length : p.findings) === 0 && !imgHits) return "zero";
    }
    return p.state || "idle";
  }

  function renderStatus(p, rec) {
    const ds = displayState(p, rec);
    const st = $("st-state");
    st.textContent = STATE[ds] || ds;
    st.dataset.state = ds === "zero" ? "done" : ds === "unscanned" ? "failed" : ds;
    const running = p.state === "running";
    // 대기 = pages that will really be checked; links beyond the page limit are counted separately
    $("st-summary").textContent = ds === "idle" ? "" : `페이지 ${p.pages_done ?? 0}개 점검` +
      (running && p.pages_queued ? ` · 대기 ${p.pages_queued}개` : "") +
      (p.pages_limit_excluded ? ` · 페이지 수 한도로 제외 ${p.pages_limit_excluded}개` : "") +
      (running && p.images_total ? ` · 이미지 ${p.images_done}/${p.images_total}` : "") +
      ` · 경과 ${mmss(p.elapsed_sec)}`;

    // problems stay visible; everything else goes to "검사 상세"
    const alert = $("st-alert");
    let msg = "", bad = false;
    if (p.state === "failed") { msg = p.end_reason || "검사 중 오류가 발생했습니다."; bad = true; }
    else if (p.state === "stopped") msg = "중단되어 점검하지 못한 영역이 있을 수 있습니다.";
    else if (p.pages_failed > 0) msg = `불러오지 못한 페이지 ${p.pages_failed}개. 자세한 내용은 검사 상세를 참고하세요.`;
    else if (p.end_reason && /제한|시간/.test(p.end_reason)) msg = p.end_reason;
    if (running && p.stop_requested) msg = "중단 요청됨…";
    alert.hidden = !msg;
    alert.textContent = msg;
    alert.classList.toggle("bad", bad);

    $("st-toggle").hidden = ds === "idle";
    $("sd-current").textContent = running ? (p.current_url || "-") : "-";
    $("sd-pages").textContent = `점검 ${p.pages_done ?? 0} · 실패 ${p.pages_failed ?? 0} · 대기 ${p.pages_queued ?? 0}` +
      (p.pages_skipped ? ` · 한도로 미방문 ${p.pages_skipped}` : "");
    $("sd-frames").textContent = `${p.frames_done ?? 0}개${p.frames_failed ? ` (실패 ${p.frames_failed})` : ""}`;
    $("sd-reason").textContent = p.end_reason || "-";
    $("sd-result").textContent = p.result_path || "-";

    $("btn-start").disabled = running;
    $("btn-stop").hidden = !running;
    $("btn-stop").disabled = !!p.stop_requested;
    $("url").readOnly = running;
  }

  $("st-toggle").addEventListener("click", () => {
    const open = $("st-toggle").getAttribute("aria-expanded") !== "true";
    $("st-toggle").setAttribute("aria-expanded", String(open));
    $("st-detail").hidden = !open;
  });

  async function poll() {
    let p;
    try { p = await (await fetch("/api/status")).json(); }
    catch (e) {
      $("st-alert").hidden = false;
      $("st-alert").textContent = "프로그램과 연결이 끊어졌습니다.";
      return;
    }
    // just started: the scan thread may not have reported yet - keep waiting for it
    if (pendingRun && (p.run_id !== pendingRun || p.state === "idle")) return;
    pendingRun = null;
    if (p.state === "running") {
      renderStatus(p, null);
      $("counts").textContent = p.findings ? `지금까지 기법별 검출 ${p.findings}건` : "";
      $("empty").hidden = false;
      $("empty").textContent = "검사 중입니다. 완료되면 결과가 표시됩니다.";
      return;
    }
    if (p.state === "idle") { stopPolling(); renderStatus(p, null); return; }
    if (p.run_id && loadedRun !== p.run_id) {
      // the run record should exist once the state is final; if it cannot be read yet,
      // keep polling instead of leaving the screen half-updated
      let rec = null;
      try {
        const r = await fetch(`/api/scans/${p.run_id}`, { cache: "no-store" });
        if (r.ok) rec = await r.json();
      } catch (e) { rec = null; }
      if (!rec) {
        loadFailures += 1;
        renderStatus(p, null);
        $("empty").hidden = false;
        if (loadFailures > 40) {
          stopPolling();
          $("empty").textContent = `검사 기록을 불러오지 못했습니다.${p.result_path ? ` 결과 파일: ${p.result_path}` : ""}`;
        } else {
          $("empty").textContent = "검사 결과를 불러오는 중입니다.";
        }
        return;
      }
      stopPolling();
      loadFailures = 0;
      loadedRun = p.run_id;
      renderStatus(p, rec);
      renderRun(rec);
      return;
    }
    stopPolling();
    renderStatus(p, lastRec);
  }
  let polling = false;
  const pollOnce = async () => {
    if (polling) return;            // never overlap two polls
    polling = true;
    try { await poll(); } finally { polling = false; }
  };
  const startPolling = () => { stopPolling(); loadFailures = 0; pollOnce(); timer = setInterval(pollOnce, 700); };
  const stopPolling = () => { if (timer) clearInterval(timer); timer = null; };

  // ------------------------------------------------------------ list
  function groupFindings(rec) {
    // one row per element (url + location); several techniques listed together
    const inResult = new Set(rec.result.findings.map((f) => `${f.url}\u0000${f.location}\u0000${f.technique}`));
    const map = new Map();
    for (const f of rec.findings) {
      if (!inResult.has(`${f.url}\u0000${f.location}\u0000${f.technique}`)) continue;
      const k = `${f.url}\u0000${f.location}`;
      if (!map.has(k)) map.set(k, { url: f.url, location: f.location, items: [] });
      const g = map.get(k);
      if (!g.items.some((x) => x.technique === f.technique)) g.items.push(f);
    }
    for (const g of map.values()) g.items.sort((a, b) => ORDER.indexOf(a.technique) - ORDER.indexOf(b.technique));
    return Array.from(map.values());
  }

  function pathLabel(u, oneHost) {
    try {
      const x = new URL(u);
      const path = x.pathname + x.search;
      return oneHost ? path : x.host + path;
    } catch (e) { return u; }
  }
  const frameNote = (f) => (f.frame_chain && f.frame_chain.length
    ? (f.frame_chain.length === 1 ? " · 삽입 프레임 안" : ` · ${f.frame_chain.length}단계 삽입 프레임 안`) : "");

  function renderRun(rec) {
    lastRec = rec;
    groups = groupFindings(rec);
    const list = $("results");
    list.replaceChildren();
    $("counts").textContent = groups.length ? `의심 요소 ${groups.length}개 · 기법별 검출 ${rec.result.findings.length}건` : "";
    const empty = $("empty");
    empty.hidden = groups.length > 0;
    if (!groups.length) {
      const failed = rec.pages.filter((x) => !["ok", "duplicate"].includes(x.status)).length;
      empty.textContent = rec.state === "failed" ? "페이지를 불러오지 못해 점검하지 못했습니다. 광고가 없다는 뜻이 아닙니다."
        : rec.state === "stopped" ? "중단되기 전까지 탐지된 항목이 없습니다."
        : failed ? "탐지된 항목은 없지만 일부 페이지를 불러오지 못했습니다."
        : "점검한 범위에서 탐지된 항목이 없습니다.";
    }
    const onePage = new Set(groups.map((g) => g.url)).size <= 1;
    const oneHost = new Set(groups.map((g) => { try { return new URL(g.url).host; } catch (e) { return g.url; } })).size <= 1;
    groups.forEach((g, i) => {
      const li = el("li");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.tabIndex = i === 0 ? 0 : -1;
      li.append(el("span", "r-text", g.items[0].evidence_text));
      const types = el("span", "r-types");
      for (const it of g.items) types.append(el("span", "", TECH[it.technique] || it.technique));
      li.append(types);
      // the path only helps when results span several pages; frame notes always help
      const where = (onePage ? "" : pathLabel(g.url, oneHost)) + frameNote(g.items[0]);
      if (where) {
        const path = el("span", "r-path", where.replace(/^ · /, ""));
        path.title = g.url;
        li.append(path);
      }
      li.addEventListener("click", () => select(i));
      li.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" && i + 1 < groups.length) { e.preventDefault(); select(i + 1, true); }
        if (e.key === "ArrowUp" && i > 0) { e.preventDefault(); select(i - 1, true); }
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(i); }
      });
      list.append(li);
    });
    renderPages(rec);
    renderSkipped(rec);
    renderExcluded(rec);
    renderImages(rec);
    renderCollection(rec);
    if (groups.length) select(0);
    else if (extraRows.length) { if (extraRows[0].kind === "text") selectExtraText(0); else selectImage(extraRows[0].i, 0); }
    else clearDetail();
  }

  function renderPages(rec) {
    const t = $("pages");
    const tb = t.querySelector("tbody");
    tb.replaceChildren();
    t.hidden = !rec.pages.length;
    for (const pg of rec.pages) {
      const tr = el("tr");
      const label = { ok: "정상", partial: "일부 로딩", http_error: "응답 오류", failed: "실패", stopped: "중단", duplicate: "중복", blocked: "보안 확인 차단" }[pg.status] || pg.status;
      tr.append(el("td", pg.status === "ok" ? "s-ok" : ["partial", "duplicate"].includes(pg.status) ? "s-warn" : "s-bad", label));
      tr.append(el("td", "", pg.url));
      const fr = pg.frames.filter((f) => f.status !== "ok").length;
      const notes = [pg.error];
      if (pg.frames.length > 1 || fr) notes.push(`프레임 ${pg.frames.length}개${fr ? ` (실패 ${fr})` : ""}`);
      if (pg.expanded && pg.expanded.length) notes.push(`더보기 ${pg.expanded.length}회 펼침`);
      tr.append(el("td", "", notes.filter(Boolean).join(" · ")));
      tb.append(tr);
    }
  }

  function renderCollection(rec) {
    const c = rec.collection;
    if (!c) { $("sd-text").textContent = "-"; $("sd-img").textContent = "-"; return; }
    const t = c.text;
    const miss = t.blocked + t.http_error + t.failed + t.stopped;
    $("sd-text").textContent = t.ok ? `본문 수집 ${t.ok}페이지 (요소 ${t.elements.toLocaleString()}개)` + (miss ? ` · 검사하지 못한 페이지 ${miss}개` : "")
      : `본문을 수집하지 못함 (검사하지 못한 페이지 ${miss}개)`;
    const im = c.images;
    $("sd-img").textContent = !im.enabled ? "꺼짐" : !im.ran ? `실행하지 못함 · ${im.error}`
      : `이미지 ${im.found}개(페이지 ${(rec.ocr.coverage || {}).pages_collected ?? "-"}곳에서 수집) · OCR 성공 ${im.ok} · 실패 ${im.failed} · 한도·건너뜀 ${im.skipped + im.timeout}` +
        (im.stopped ? ` · 중단 ${im.stopped}` : "") +
        ((rec.ocr.coverage || {}).pages_not_collected ? ` · 이미지를 수집하지 못한 페이지 ${rec.ocr.coverage.pages_not_collected}개` : "");
  }

  function renderSkipped(rec) {
    const c = (rec.crawl && rec.crawl.skipped_count) || {};
    const parts = Object.entries(c).map(([k, n]) => `${k} ${n}개`);
    $("sd-skipped").textContent = parts.length ? parts.join(" · ") : "없음";
  }

  function renderExcluded(rec) {
    const xs = rec.excluded || [];
    $("excluded-box").hidden = !xs.length;
    $("excluded-count").textContent = `(${xs.length})`;
    const ul = $("excluded");
    ul.replaceChildren();
    for (const x of xs) {
      const li = el("li", "", x.text);
      li.append(el("span", "x-why", ` — ${x.reason.replace(/^예방·보도·안내 문맥: /, "예방·안내 문맥(")}${/^예방·보도·안내 문맥: /.test(x.reason) ? ")" : ""}`));
      ul.append(li);
    }
  }

  // ------------------------------------------------------------ reason sentence
  // Built only from what the detector recorded (ad_reasons / techniques).
  function parseReasons(lines) {
    const out = { strong: [], weak: [], cta: [], contact: [], code: [], shortHidden: false };
    const key = { "주제어": "strong", "보조 주제어": "weak", "유도 표현": "cta", "연락·접속 수단": "contact", "가입·추천 코드": "code" };
    for (const line of lines || []) {
      const m = line.match(/^([^:]+):\s*(.*)$/);
      if (m && key[m[1]]) out[key[m[1]]] = m[2].split(/,\s*/).filter(Boolean);
      else if (/위장됨|의도적으로 숨김/.test(line)) out.shortHidden = true;
    }
    return out;
  }
  const bare = (t) => t.replace(/\(.*$/, "").trim();
  // keep the most specific items: drop an item contained in a longer one (or the reverse)
  const dedupe = (xs, keepShort) => {
    const u = Array.from(new Set(xs.filter(Boolean)));
    return u.filter((a) => !u.some((b) => b !== a && (keepShort ? a.includes(b) : b.includes(a))));
  };

  function reasonSentence(items) {
    const r = parseReasons(items[0].ad_reasons);
    const techs = items.map((x) => x.technique);
    const phrase = techs.map((t, k) => PHRASE[t][k === techs.length - 1 ? 1 : 0]).join(" ");
    const topics = dedupe(r.strong.map(bare), false).slice(0, 2);
    const weak = dedupe(r.weak.map(bare), false).slice(0, 2);
    const topic = (topics.length ? topics : weak).join("·") || "불법광고";
    const cta = dedupe(r.cta.filter((c) => !/^[%％]$/.test(c)), false).slice(0, 3);
    const ids = r.contact.filter((c) => /[@./\d]/.test(c));
    const contact = dedupe(ids.length ? ids : r.contact, true).slice(0, 2);
    const codes = r.code.map((c) => c.replace(/^\s*/, "")).slice(0, 2);
    const parts = [];
    if (cta.length) parts.push(`${cta.join("·")} 표현`);
    if (codes.length) parts.push(`가입·추천 코드(${codes.join(", ")})`);
    if (contact.length) parts.push(`접속·연락 수단(${contact.join(", ")})`);
    if (parts.length) return `${phrase} ${topic} 문구에 ${parts.join("과 ")}이 포함되어 있습니다.`;
    if (r.shortHidden) return `${phrase} 짧은 ${topic} 문구입니다. 주제어를 글자 변형으로 위장한 것으로 판단했습니다.`;
    const all = topics.concat(weak);
    return `${phrase} 문구에 ${all.join("·") || topic} 등 불법광고 주제어가 함께 쓰였습니다.`;
  }

  // ------------------------------------------------------------ detail
  function clearDetail() {
    $("detail-empty").hidden = false;
    $("detail-body").hidden = true;
    $("img-detail").hidden = true;
  }

  // ------------------------------------------------------------ images (OCR)
  const LEVEL = { suspect: "광고 의심", uncertain: "불확실(근거 부족)", none: "해당 없음" };
  const STATUS = { ok: "처리됨", skipped: "건너뜀", failed: "실패", timeout: "시간 초과", stopped: "중단" };
  let imgItems = [];
  const previewUrl = (rec, im) => (im.preview ? `/api/scans/${rec.run_id}/images/${im.preview}` : "");

  // One list for everything that looked like an ad but is not an official finding:
  // plain-text ads without a concealment technique, and OCR image results.
  let extraRows = [];

  function renderImages(rec) {
    const imgs = rec.images || [];
    const texts = rec.extra_ads || [];
    const o = rec.ocr || {};
    imgItems = imgs.filter((im) => im.level === "suspect" || im.level === "uncertain")
      .sort((a, b) => (a.level === "suspect" ? 0 : 1) - (b.level === "suspect" ? 0 : 1));
    extraRows = texts.map((t) => ({ kind: "text", item: t })).concat(imgItems.map((im, i) => ({ kind: "image", i })));
    const box = $("img-section");
    box.hidden = !extraRows.length && !o.enabled;
    const list = $("img-results");
    list.replaceChildren();
    const n = (lv) => imgs.filter((x) => x.level === lv).length;
    const bits = [`텍스트 ${texts.length}`];
    if (o.enabled) bits.push(`이미지 의심 ${n("suspect")} · 불확실 ${n("uncertain")}`);
    $("img-counts").textContent = bits.join(" · ");
    const empty = $("img-empty");
    empty.hidden = extraRows.length > 0;
    empty.textContent = o.enabled && !o.ran ? `이미지 검사를 하지 못했습니다. ${o.error || ""}` : "추가로 의심되는 광고가 없습니다.";
    const onePage = new Set(texts.map((t) => t.url)).size <= 1;
    extraRows.forEach((row, r) => {
      const li = el("li", row.kind === "image" ? "r-img" : "");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.tabIndex = -1;
      if (row.kind === "text") {
        const t = row.item;
        li.append(el("span", "r-text", t.evidence_text));
        li.append(el("span", "r-types r-kind", "텍스트 · 숨김 없음"));
        li.append(el("span", "r-path", (onePage ? "" : pathLabel(t.url, true) + " · ") + t.location));
        li.addEventListener("click", () => selectExtraText(r));
      } else {
        const im = imgItems[row.i];
        const th = el("img", "thumb");
        th.alt = "";
        if (im.preview) th.src = previewUrl(rec, im);
        li.append(th);
        const blk = im.verdict && im.verdict.block != null ? im.ocr_blocks[im.verdict.block].text : (im.ocr_text || "").replace(/\n/g, " ");
        li.append(el("span", "r-text", blk));
        li.append(el("span", "r-types lv-" + im.level, `이미지 · ${LEVEL[im.level]}`));
        const multi = new Set(imgs.map((x) => x.page_url)).size > 1;
        li.append(el("span", "r-path", (multi ? pathLabel(im.page_url, true) + " · " : "") + (im.location || im.selector)));
        li.addEventListener("click", () => selectImage(row.i, r));
      }
      li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); li.click(); } });
      list.append(li);
    });
    $("img-log-box").hidden = !o.enabled;
    const tb = $("img-log").querySelector("tbody");
    tb.replaceChildren();
    $("img-log-count").textContent = `(${imgs.length})`;
    $("img-engine").textContent = o.ran
      ? `엔진: ${o.engine_detail || o.engine} · 전체 ${o.total_sec}초 (OCR ${o.ocr_sec}초, 내려받기 ${o.download_sec}초, 같은 이미지 재사용 ${o.cache_hits}회)`
      : (o.error || "");
    for (const im of imgs) {
      const tr = el("tr");
      const res = im.status === "ok" ? (LEVEL[im.level] || "처리됨") : STATUS[im.status] || im.status;
      tr.append(el("td", im.status === "ok" ? (im.level === "none" ? "s-ok" : "s-warn") : "s-bad", res));
      const where = pathLabel(im.page_url, true) + (im.frame_chain && im.frame_chain.length ? ` (삽입 프레임 ${im.frame_chain.length}단계 안)` : "");
      tr.append(el("td", "", `${im.img_url || "(주소 없음)"} — ${where}`));
      const notes = [im.reason, im.cache_hit ? "같은 이미지 결과 재사용" : "", im.prep_note, im.verdict && im.verdict.note].filter(Boolean);
      tr.append(el("td", "", notes.join(" · ")));
      tb.append(tr);
    }
  }

  function markExtraRow(r) {
    $("results").querySelectorAll("li").forEach((x) => x.setAttribute("aria-selected", "false"));
    $("img-results").querySelectorAll("li").forEach((x, k) => { x.setAttribute("aria-selected", k === r ? "true" : "false"); x.tabIndex = k === r ? 0 : -1; });
  }

  function extraSentence(t) {
    const r = parseReasons(t.ad_reasons);
    const topics = dedupe(r.strong.map(bare), false).slice(0, 2);
    const topic = (topics.length ? topics : dedupe(r.weak.map(bare), false).slice(0, 2)).join("·") || "불법광고";
    const cta = dedupe(r.cta.filter((c) => !/^[%％]$/.test(c)), false).slice(0, 3);
    const parts = [];
    if (cta.length) parts.push(`${cta.join("·")} 표현`);
    if (r.code.length) parts.push(`가입·추천 코드(${r.code.slice(0, 2).join(", ")})`);
    if (r.contact.length) parts.push(`접속·연락 수단(${dedupe(r.contact, true).slice(0, 2).join(", ")})`);
    return parts.length ? `${topic} 관련 단어와 ${parts.join(", ")}이 함께 있어 광고로 의심됩니다.` : `${topic} 관련 단어가 있어 광고로 의심됩니다.`;
  }

  function selectExtraText(r) {
    markExtraRow(r);
    const t = extraRows[r].item;
    $("detail-empty").hidden = true;
    $("img-detail").hidden = true;
    $("detail-body").hidden = false;
    $("d-title").textContent = t.evidence_text;
    $("d-title").title = t.evidence_text;
    $("d-type-label").textContent = "구분:";
    $("d-types").textContent = "추가 의심 광고 · 숨김·위장 없음";
    const same = !t.restored_text || t.restored_text === t.evidence_text;
    $("d-raw").textContent = t.evidence_text;
    $("d-restored-wrap").hidden = same;
    $("d-restored").textContent = same ? "" : t.restored_text;
    $("d-why").textContent = extraSentence(t);
    $("d-excl-wrap").hidden = false;
    $("d-excl").textContent = t.excluded_why;
    const a = $("d-url");
    a.textContent = t.url;
    a.href = /^https?:/i.test(t.url) ? t.url : "#";
    $("d-frame-note").textContent = frameNote(t);
    $("t-ad").replaceChildren(...(t.ad_reasons || []).map((x) => labeled(x)));
    $("t-hide").replaceChildren(el("li", "", "해당 없음 — 숨김·위장 기법이 확인되지 않음"));
    $("t-changes-label").hidden = true;
    $("t-changes").hidden = true;
    $("t-loc").textContent = t.location;
    $("t-frame").textContent = t.frame_chain && t.frame_chain.length ? t.frame_chain.join("  ›  ") : "최상위 문서";
    $("t-verified").textContent = t.selector_verified ? "수집한 문서에서 이 선택자가 해당 요소 하나만 가리킴을 확인함" : "유일성 확인 실패 · 위치를 직접 확인하세요";
    $("t-verified").className = t.selector_verified ? "" : "warn-text";
  }

  function imageSentence(im) {
    const v = im.verdict || {};
    if (im.level === "uncertain") return `근거가 부족해 불확실로 분류했습니다. ${v.note || ""}`.trim();
    const r = parseReasons(v.reasons);
    const topics = dedupe(r.strong.map(bare), false).slice(0, 2);
    const topic = (topics.length ? topics : dedupe(r.weak.map(bare), false).slice(0, 2)).join("·") || "불법광고";
    const cta = dedupe(r.cta.filter((c) => !/^[%％]$/.test(c)), false).slice(0, 3);
    const parts = [];
    if (cta.length) parts.push(`${cta.join("·")} 표현`);
    if (r.code.length) parts.push(`가입·추천 코드(${r.code.slice(0, 2).join(", ")})`);
    if (r.contact.length) parts.push(`접속·연락 수단(${dedupe(r.contact, true).slice(0, 2).join(", ")})`);
    const found = parts.length ? `${topic} 관련 단어와 ${parts.join(", ")}이 함께 확인됩니다.` : `${topic} 관련 단어가 확인됩니다.`;
    return `이미지 속 한 문구 영역에서 ${found} 이미지 글자 인식 결과이므로 확정 판정이 아닙니다.`;
  }

  function selectImage(i, row) {
    markExtraRow(row != null ? row : extraRows.findIndex((x) => x.kind === "image" && x.i === i));
    const im = imgItems[i];
    if (!im) return clearDetail();
    $("detail-empty").hidden = true;
    $("detail-body").hidden = true;
    $("img-detail").hidden = false;
    const hit = im.verdict ? im.verdict.block : null;
    const blocks = im.ocr_blocks || [];
    $("i-title").textContent = hit != null ? blocks[hit].text : (blocks[0] ? blocks[0].text : "");
    $("i-level").textContent = LEVEL[im.level];
    $("i-level").className = "lv-" + im.level;
    const pv = $("i-preview");
    pv.hidden = !im.preview;
    if (im.preview) pv.src = previewUrl(lastRec, im);
    $("i-blocks").replaceChildren(...blocks.map((b, k) => el("li", k === hit ? "hit" : "", b.text)));
    $("i-why").textContent = imageSentence(im);
    $("i-excl").textContent = "이미지 속 글자는 공식 4종 기법(숨김·위장 텍스트)의 대상이 아니어서 result.json에서 제외" +
      (im.level === "suspect" ? " · ‘광고 의심’은 추가 탐지 파일(result_extra.json)에 기록" : " · 근거가 부족해 추가 탐지 파일에도 기록하지 않음");
    $("i-reasons").replaceChildren(...((im.verdict && im.verdict.reasons) || []).map((x) => labeled(x)));
    const a = $("i-page");
    a.textContent = im.page_url;
    a.href = /^https?:/i.test(im.page_url) ? im.page_url : "#";
    $("i-src").textContent = im.img_url;
    $("i-loc").textContent = im.location || im.selector;
    const proc = [
      ["처리 결과", `${STATUS[im.status] || im.status}${im.reason ? ` · ${im.reason}` : ""}`],
      ["원본 크기", im.natural ? `${im.natural[0]}×${im.natural[1]}px${im.bytes ? ` · ${Math.round(im.bytes / 1024)}KB` : ""}` : "-"],
      ["OCR 전처리", im.prep_note || "크기 조정 없음 · 명암 보정"],
      ["처리 시간", Object.entries(im.timing || {}).map(([k, v]) => `${{ download: "내려받기", prepare: "준비", ocr: "OCR" }[k] || k} ${v}초`).join(" · ") || "-"],
      ["화면 표시 상태", im.dom_hidden && im.dom_hidden.length ? `숨겨진 이미지(${im.dom_hidden.join(", ")}) — 광고성 판단과 별도로 기록` : "화면에 표시되는 이미지"],
      ["사용한 주소", `${im.src_via || "-"}${im.shown_src && im.shown_src !== im.img_url ? ` · 브라우저 표시 주소: ${im.shown_src}` : ""}` +
        (im.first_source ? ` · 먼저 읽은 이미지(${im.first_source.img_url})에서는 의심 근거가 없어 더 큰 후보를 확인함` : "")],
      ["프레임", im.frame_chain && im.frame_chain.length ? im.frame_chain.join("  ›  ") : "최상위 문서"],
      ["재사용", im.cache_hit ? "같은 내용의 이미지 OCR 결과를 재사용(위치는 이 요소)" : "아니오"],
      ["참고", (im.verdict && im.verdict.note) || "-"],
    ];
    $("i-proc").replaceChildren(...proc.flatMap(([k, v]) => [el("dt", "", k), el("dd", "", v)]));
  }

  $("i-copy").addEventListener("click", async () => {
    const btn = $("i-copy");
    try { await navigator.clipboard.writeText($("i-loc").textContent); btn.textContent = "복사됨"; }
    catch (e) { btn.textContent = "복사 실패"; }
    setTimeout(() => { btn.textContent = "선택자 복사"; }, 1400);
  });

  // "주제어: 카지노(도박)" -> <b>주제어:</b> 카지노(도박)
  function labeled(line) {
    const li = el("li");
    const m = line.match(/^([^:]{1,20}):\s*(.*)$/);
    if (m) li.append(el("b", "", `${m[1]}:`), ` ${m[2]}`);
    else li.textContent = line;
    return li;
  }

  function mergedChanges(items) {
    const kinds = { jamo: "자모 조합", homoglyph: "닮은꼴 문자", leet: "숫자·기호 대체" };
    const by = new Map();
    for (const it of items) for (const c of it.changes || []) {
      const k = kinds[c.kind] || c.kind;
      if (!by.has(k)) by.set(k, new Set());
      by.get(k).add(`${c.src} → ${c.dst}`);
    }
    return Array.from(by, ([k, set]) => `${k}: ${Array.from(set).join(", ")}`);
  }

  function select(i, focus) {
    $("img-results").querySelectorAll("li").forEach((r) => r.setAttribute("aria-selected", "false"));
    $("img-detail").hidden = true;
    $("d-excl-wrap").hidden = true;
    $("d-type-label").textContent = "불법 광고 유형:";
    const rows = $("results").querySelectorAll("li");
    rows.forEach((r, k) => { r.setAttribute("aria-selected", k === i ? "true" : "false"); r.tabIndex = k === i ? 0 : -1; });
    if (focus && rows[i]) rows[i].focus();
    const g = groups[i];
    if (!g) return clearDetail();
    const f0 = g.items[0];
    $("detail-empty").hidden = true;
    $("detail-body").hidden = false;

    // title = short summary of the selected item; 원문 below keeps the full text
    $("d-title").textContent = f0.evidence_text;
    $("d-title").title = f0.evidence_text;
    $("d-types").textContent = g.items.map((x) => TECH[x.technique]).join(" · ");
    const same = !f0.restored_text || f0.restored_text === f0.evidence_text;
    $("d-raw").textContent = f0.evidence_text;
    $("d-restored-wrap").hidden = same;
    $("d-restored").textContent = same ? "" : f0.restored_text;
    $("d-why").textContent = reasonSentence(g.items);

    const a = $("d-url");
    a.textContent = g.url;
    a.href = /^https?:/i.test(g.url) ? g.url : "#";
    $("d-frame-note").textContent = frameNote(f0);

    // technical detail (folded)
    $("t-ad").replaceChildren(...(f0.ad_reasons || []).map((x) => labeled(x)));
    const changeText = new Set((f0.changes || []).map((c) => `${c.src} → ${c.dst}`));
    $("t-hide").replaceChildren(...g.items.map((it) => {
      // drop lines that only repeat a character change (shown once under 글자 변환)
      // the technique name is already in the header, so avoid repeating it in the sentence
      const lines = (it.technique_reasons || []).filter((x) => !changeText.has(x) && !/^치환: /.test(x))
        .map((x) => x
          .replace(/^분해된 자모 조합: .*/, "분해된 글자를 조합하면 광고 문구가 됨")
          .replace(/^자모 조합 후 드러난 주제어: /, "조합하면 드러나는 주제어: ")
          .replace(/^닮은꼴 문자 복원 후 드러난 주제어: /, "복원하면 드러나는 주제어: "));
      const text = lines.join(" · ") || "-";
      // label only when several techniques share one element; plain weight either way
      return el("li", "", g.items.length > 1 ? `${TECH[it.technique]} — ${text}` : text);
    }));
    const ch = mergedChanges(g.items);
    $("t-changes-label").hidden = !ch.length;
    $("t-changes").hidden = !ch.length;
    $("t-changes").replaceChildren(...ch.map((x) => el("div", "", x)));
    $("t-loc").textContent = g.location;
    $("t-frame").textContent = f0.frame_chain && f0.frame_chain.length ? f0.frame_chain.join("  ›  ") : "최상위 문서";
    const ok = g.items.every((x) => x.selector_verified);
    $("t-verified").textContent = ok ? "수집한 문서에서 이 선택자가 해당 요소 하나만 가리킴을 확인함" : "유일성 확인 실패 · 위치를 직접 확인하세요";
    $("t-verified").className = ok ? "" : "warn-text";
  }

  $("d-copy").addEventListener("click", async () => {
    const btn = $("d-copy");
    const t = $("t-loc").textContent;
    let ok = false;
    try { await navigator.clipboard.writeText(t); ok = true; }
    catch (e) {
      const ta = el("textarea"); ta.value = t; document.body.append(ta); ta.select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
    }
    btn.textContent = ok ? "복사됨" : "복사 실패";
    setTimeout(() => { btn.textContent = "선택자 복사"; }, 1400);
  });

  // ------------------------------------------------------------ actions
  $("scan-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("url").value.trim();
    const options = { max_depth: $("opt-depth").value, max_pages: $("opt-pages").value, budget_min: $("opt-budget").value,
                      ocr: $("opt-ocr").checked };
    const r = await fetch("/api/scans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url, options }) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || "검사를 시작하지 못했습니다.");
      return;
    }
    pendingRun = (await r.json()).run_id;
    loadedRun = null; lastRec = null; groups = [];
    $("results").replaceChildren();
    $("counts").textContent = "";
    $("excluded-box").hidden = true;
    $("pages").hidden = true;
    clearDetail();
    startPolling();
  });

  $("btn-stop").addEventListener("click", async () => {
    $("btn-stop").disabled = true;
    await fetch("/api/scans/stop", { method: "POST" });
  });

  // ------------------------------------------------------------ help dialogs
  const help = $("dlg-help"), types = $("dlg-types");
  let helpOpener = null;
  const openHelp = (focusEl) => {
    help.showModal();
    (focusEl || help.querySelector("[data-close]")).focus();
  };
  $("btn-help").addEventListener("click", () => { helpOpener = $("btn-help"); openHelp(); });
  help.querySelector("[data-close]").addEventListener("click", () => help.close());
  help.addEventListener("close", () => {
    if (help.dataset.toTypes === "1") return;   // switching to the types window
    (helpOpener || $("btn-help")).focus();
  });
  $("btn-types").addEventListener("click", () => {
    // hide help while the types window is open so the two do not stack
    help.dataset.toTypes = "1";
    help.close();
    help.dataset.toTypes = "";
    types.showModal();
    types.querySelector("[data-close]").focus();
  });
  types.querySelector("[data-close]").addEventListener("click", () => types.close());
  // Esc or 닫기 on the types window returns to help, focused on the button that opened it
  types.addEventListener("close", () => openHelp($("btn-types")));
  // click on the dimmed area closes the window
  for (const d of [help, types]) {
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  }

  // ------------------------------------------------------------ scan settings
  const optsSummary = () => {
    $("opts-summary").textContent = `(깊이 ${$("opt-depth").value} · 최대 ${$("opt-pages").value}페이지 · ${$("opt-budget").value}분` +
      ($("opt-ocr").checked ? " · 이미지 글자 검사)" : ")");
  };
  fetch("/api/defaults").then((r) => r.json()).then((d) => {
    $("opt-depth").value = d.max_depth;
    $("opt-pages").value = d.max_pages;
    $("opt-budget").value = Math.round(d.budget_min);
    $("opt-ocr").checked = !!d.ocr;
    $("ocr-status").textContent = d.ocr_available ? "" : `사용할 수 없음: ${d.ocr_message}`;
    optsSummary();
  }).catch(() => {});
  for (const id of ["opt-depth", "opt-pages", "opt-budget", "opt-ocr"]) $(id).addEventListener("input", optsSummary);

  startPolling();
})();
