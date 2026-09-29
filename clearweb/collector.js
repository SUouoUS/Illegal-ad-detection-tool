// ClearWeb in-frame collector.
// Evaluated by Playwright inside every frame. Returns one record per element that
// carries text (textContent up to MAX_TEXT chars), with hiding signals computed from
// the *computed* style of the element and its ancestors, plus layout geometry.
// Elements are kept in window.__cwNodes so selectors can be built later by index.
(() => {
  const MAX_TEXT = 600;
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "HEAD", "META", "LINK", "IFRAME", "FRAME", "OBJECT", "CANVAS"]);
  const doc = document;
  const root = doc.body || doc.documentElement;
  if (!root) return { nodes: [], doc: {} };

  const sx = window.scrollX, sy = window.scrollY;
  const vw = window.innerWidth, vh = window.innerHeight;
  const docW = Math.max(doc.documentElement.scrollWidth, root.scrollWidth || 0, vw);
  const docH = Math.max(doc.documentElement.scrollHeight, root.scrollHeight || 0, vh);

  const styleCache = new Map();
  const cs = (el) => {
    let s = styleCache.get(el);
    if (!s) { s = getComputedStyle(el); styleCache.set(el, s); }
    return s;
  };

  const parseColor = (str) => {
    if (!str) return null;
    const m = str.match(/rgba?\(([^)]+)\)/);
    if (!m) {
      if (str === "transparent") return [0, 0, 0, 0];
      return null;
    }
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const contrast = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };

  // Effective background: nearest ancestor-or-self with an opaque background color.
  // Returns {color, uncertain} ; uncertain when a background image is met first.
  const effectiveBg = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const s = cs(e);
      if (s.backgroundImage && s.backgroundImage !== "none") return { color: null, uncertain: true, by: e };
      const c = parseColor(s.backgroundColor);
      if (c && c[3] >= 0.9) return { color: c, uncertain: false, by: e };
      if (c && c[3] > 0.05) return { color: null, uncertain: true, by: e };
    }
    return { color: [255, 255, 255, 1], uncertain: false, by: null };
  };

  const px = (v) => { const n = parseFloat(v); return isNaN(n) ? 0 : n; };

  const nodes = [];
  const index = new Map();
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (n) => SKIP.has(n.tagName.toUpperCase()) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });

  const list = [root];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n !== root) list.push(n);

  const textOf = (el) => {
    // textContent without script/style content
    let out = "";
    const w = doc.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: (n) => (n.nodeType === 1 && SKIP.has(n.tagName.toUpperCase())) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (n.nodeType === 3) { out += n.nodeValue; if (out.length > MAX_TEXT * 2) return null; }
    }
    return out;
  };

  const hasOwnText = (el) => {
    for (const c of el.childNodes) if (c.nodeType === 3 && c.nodeValue.trim()) return true;
    return false;
  };

  const clipAncestors = (el) => {
    // visible area of el after clipping by overflow ancestors (in viewport coords)
    const r = el.getBoundingClientRect();
    let x1 = r.left, y1 = r.top, x2 = r.right, y2 = r.bottom;
    let clipper = null;
    for (let e = el.parentElement; e && e.nodeType === 1 && e !== doc.documentElement; e = e.parentElement) {
      const s = cs(e);
      if (s.overflowX !== "visible" || s.overflowY !== "visible" || s.contain.includes("paint")) {
        const pr = e.getBoundingClientRect();
        const nx1 = Math.max(x1, pr.left), ny1 = Math.max(y1, pr.top), nx2 = Math.min(x2, pr.right), ny2 = Math.min(y2, pr.bottom);
        if ((nx2 - nx1) * (ny2 - ny1) < (x2 - x1) * (y2 - y1) && !clipper) clipper = e;
        x1 = nx1; y1 = ny1; x2 = nx2; y2 = ny2;
        if (x2 <= x1 || y2 <= y1) return { area: 0, clipper: clipper || e };
      }
    }
    return { area: Math.max(0, x2 - x1) * Math.max(0, y2 - y1), clipper };
  };

  for (const el of list) {
    const raw = textOf(el);
    if (raw === null) continue; // too long to be a single ad unit
    if (!raw.trim()) continue;
    const i = nodes.length;
    index.set(el, i);
    nodes.push({ el, raw });
  }

  const idxOf = (e) => (e && index.has(e)) ? index.get(e) : -1;
  // map an arbitrary element to the nearest recorded ancestor-or-self
  const recAncestor = (e) => { for (; e && e.nodeType === 1; e = e.parentElement) if (index.has(e)) return index.get(e); return -1; };

  const out = [];
  for (let i = 0; i < nodes.length; i++) {
    const { el, raw } = nodes[i];
    const s = cs(el);
    const signals = [];
    const add = (technique, kind, rootEl, detail) => signals.push({ technique, kind, root: recAncestor(rootEl), detail });

    // --- display:none (self or ancestor) ---
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const es = cs(e);
      if (es.display === "none") { add("OFFSCREEN", "display-none", e, `display:none (${e.tagName.toLowerCase()})`); break; }
      if (e.hasAttribute && e.hasAttribute("hidden") && es.display === "none") break;
    }
    const displayed = el.getClientRects().length > 0 || s.display === "contents";

    // --- visibility:hidden (inherited) ---
    if (s.visibility === "hidden" || s.visibility === "collapse") {
      let r = el;
      while (r.parentElement && ["hidden", "collapse"].includes(cs(r.parentElement).visibility)) r = r.parentElement;
      add("TRANSPARENT", "visibility-hidden", r, `visibility:${s.visibility}`);
    }

    // --- opacity (product over ancestors) ---
    let op = 1, opRoot = null, minOp = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const o = parseFloat(cs(e).opacity);
      if (!isNaN(o)) { op *= o; if (o < minOp) { minOp = o; opRoot = e; } }
    }
    if (op <= 0.05) add("TRANSPARENT", "opacity", opRoot, `opacity:${minOp} (누적 ${op.toFixed(3)})`);

    // --- text color ---
    if (hasOwnText(el)) {
      const c = parseColor(s.color);
      const fill = parseColor(s.webkitTextFillColor);
      const shadow = s.textShadow && s.textShadow !== "none";
      const stroke = px(s.webkitTextStrokeWidth) > 0;
      const visibleColor = fill && fill[3] > 0 ? fill : c;
      if (c && c[3] <= 0.05 && (!fill || fill[3] <= 0.05) && !shadow && !stroke) {
        let r = el;
        while (r.parentElement && (parseColor(cs(r.parentElement).color) || [0, 0, 0, 1])[3] <= 0.05) r = r.parentElement;
        add("TRANSPARENT", "color-transparent", r, `color:${s.color}`);
      } else if (fill && fill[3] <= 0.05 && !shadow && !stroke) {
        add("TRANSPARENT", "text-fill-transparent", el, `-webkit-text-fill-color:${s.webkitTextFillColor}`);
      } else if (visibleColor && visibleColor[3] > 0.05 && !shadow && !stroke) {
        const bg = effectiveBg(el);
        if (!bg.uncertain && bg.color) {
          const ratio = contrast(visibleColor, bg.color);
          if (ratio < 1.12) add("TRANSPARENT", "same-as-background", el, `글자색 ${s.color} / 배경색 rgb(${bg.color.slice(0, 3).join(", ")}) 대비 ${ratio.toFixed(2)}:1`);
        }
      }
    }

    // --- font-size 0~1px (inherited) ---
    const fs = px(s.fontSize);
    if (hasOwnText(el) && fs <= 1) {
      let r = el;
      while (r.parentElement && px(cs(r.parentElement).fontSize) <= 1) r = r.parentElement;
      add("OFFSCREEN", "tiny-font", r, `font-size:${s.fontSize}`);
    }

    // --- geometry-based (only when rendered) ---
    let rect = null;
    if (displayed) {
      const b = el.getBoundingClientRect();
      rect = { x: b.left + sx, y: b.top + sy, w: b.width, h: b.height };
      const pos = s.position;
      const outside = (bb) => (bb.right + sx <= 0) || (bb.bottom + sy <= 0) || (bb.left + sx >= docW) || (bb.left >= vw + 1000);
      if ((b.width > 0 || b.height > 0) && outside(b)) {
        let r = el;
        while (r.parentElement && r.parentElement !== root) {
          const pb = r.parentElement.getBoundingClientRect();
          if ((pb.width > 0 || pb.height > 0) && outside(pb)) r = r.parentElement; else break;
        }
        add("OFFSCREEN", "outside-document", r, `위치 left:${Math.round(b.left + sx)}px, top:${Math.round(b.top + sy)}px (${cs(r).position})`);
      } else if (pos === "fixed" && (b.right <= 0 || b.bottom <= 0 || b.left >= vw || b.top >= vh)) {
        add("OFFSCREEN", "fixed-outside-viewport", el, `position:fixed, 화면 밖 (${Math.round(b.left)}, ${Math.round(b.top)})`);
      } else {
        // clipping (clip / clip-path / overflow on tiny box)
        let clipEl = null, clipDesc = "";
        for (let e = el; e && e.nodeType === 1 && e !== root; e = e.parentElement) {
          const es = cs(e);
          if (es.clip && es.clip !== "auto" && /rect\(\s*0(px)?[\s,]+0(px)?[\s,]+0(px)?[\s,]+0(px)?\s*\)/.test(es.clip)) { clipEl = e; clipDesc = `clip:${es.clip}`; break; }
          if (es.clipPath && es.clipPath !== "none" && /inset\(\s*(50%|100%)|circle\(\s*0|polygon\(\s*0px 0px\s*\)/.test(es.clipPath)) { clipEl = e; clipDesc = `clip-path:${es.clipPath}`; break; }
        }
        if (clipEl) add("OFFSCREEN", "clipped", clipEl, clipDesc);
        else if (hasOwnText(el)) {
          const ca = clipAncestors(el);
          const selfTiny = (b.width <= 1.5 || b.height <= 1.5) && (s.overflowX !== "visible" || s.overflowY !== "visible");
          if (selfTiny) add("OFFSCREEN", "zero-size", el, `크기 ${b.width.toFixed(1)}×${b.height.toFixed(1)}px, overflow:${s.overflow}`);
          else if (ca.area <= 4 && ca.clipper) add("OFFSCREEN", "clipped-by-overflow", ca.clipper, `상위 요소 overflow로 잘림 (보이는 면적 ${ca.area.toFixed(1)}px²)`);
        }
        const ti = px(s.textIndent);
        if (hasOwnText(el) && Math.abs(ti) >= 999) {
          let r = el;
          while (r.parentElement && Math.abs(px(cs(r.parentElement).textIndent)) >= 999) r = r.parentElement;
          add("OFFSCREEN", "text-indent", r, `text-indent:${s.textIndent}`);
        }
      }
    }

    out.push({
      i,
      p: recAncestor(el.parentElement),
      tag: el.tagName.toLowerCase(),
      id: el.id || "",
      cls: (el.getAttribute("class") || "").trim(),
      own: hasOwnText(el),
      text: raw,
      display: s.display,
      inline: s.display === "inline" || s.display === "contents",
      rect,
      href: el.tagName === "A" ? (el.href || "") : "",
      signals,
    });
  }

  window.__cwNodes = nodes.map((n) => n.el);
  return {
    nodes: out,
    doc: { url: location.href, title: doc.title, vw, vh, docW, docH, bg: (effectiveBg(root).color || null) },
  };
})()
