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
    partial: "완료 · 일부 실패", stopped: "중단됨", failed: "점검 실패",
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
    if (p.state === "done") {
      const failed = rec ? rec.pages.filter((x) => !["ok", "duplicate"].includes(x.status)).length : p.pages_failed;
      if (failed > 0) return "partial";
      if ((rec ? rec.result.findings.length : p.findings) === 0) return "zero";
    }
    return p.state || "idle";
  }

  function renderStatus(p, rec) {
    const ds = displayState(p, rec);
    const st = $("st-state");
    st.textContent = STATE[ds] || ds;
    st.dataset.state = ds === "zero" ? "done" : ds;
    const running = p.state === "running";
    // 대기 = pages that will really be checked; links beyond the page limit are counted separately
    $("st-summary").textContent = ds === "idle" ? "" : `페이지 ${p.pages_done ?? 0}개 점검` +
      (running && p.pages_queued ? ` · 대기 ${p.pages_queued}개` : "") +
      (p.pages_limit_excluded ? ` · 페이지 수 한도로 제외 ${p.pages_limit_excluded}개` : "") +
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
    if (groups.length) select(0); else clearDetail();
  }

  function renderPages(rec) {
    const t = $("pages");
    const tb = t.querySelector("tbody");
    tb.replaceChildren();
    t.hidden = !rec.pages.length;
    for (const pg of rec.pages) {
      const tr = el("tr");
      const label = { ok: "정상", partial: "일부 로딩", http_error: "응답 오류", failed: "실패", stopped: "중단", duplicate: "중복" }[pg.status] || pg.status;
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
  }

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
    const options = { max_depth: $("opt-depth").value, max_pages: $("opt-pages").value, budget_min: $("opt-budget").value };
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
    $("opts-summary").textContent = `(깊이 ${$("opt-depth").value} · 최대 ${$("opt-pages").value}페이지 · ${$("opt-budget").value}분)`;
  };
  fetch("/api/defaults").then((r) => r.json()).then((d) => {
    $("opt-depth").value = d.max_depth;
    $("opt-pages").value = d.max_pages;
    $("opt-budget").value = Math.round(d.budget_min);
    optsSummary();
  }).catch(() => {});
  for (const id of ["opt-depth", "opt-pages", "opt-budget"]) $(id).addEventListener("input", optsSummary);

  startPolling();
})();
