// Build a unique CSS selector for each requested element index (window.__cwNodes)
// and re-verify it with querySelectorAll in the same document.
// Format follows the official rule: tag + classes, joined by " > ", nth-of-type
// where needed, attribute values in double quotes.
(indices) => {
  const nodes = window.__cwNodes || [];
  const doc = document;
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/([^\w-])/g, "\\$1");
  const okClass = (c) => c && !/^\d/.test(c) && c.length < 60;

  const seg = (el, forceNth) => {
    const tag = el.localName;
    const classes = Array.from(el.classList).filter(okClass);
    let s = tag + classes.map((c) => "." + esc(c)).join("");
    const parent = el.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter((c) => c.localName === tag);
      if (same.length > 1) {
        const matching = classes.length ? same.filter((c) => classes.every((k) => c.classList.contains(k))) : same;
        if (forceNth || matching.length > 1 || !classes.length) s += `:nth-of-type(${same.indexOf(el) + 1})`;
      }
    }
    return s;
  };

  const unique = (sel, el) => {
    try {
      const m = doc.querySelectorAll(sel);
      return m.length === 1 && m[0] === el;
    } catch (e) { return false; }
  };

  const build = (el) => {
    if (!el || !el.isConnected) return { selector: null, verified: false, reason: "element detached" };
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== doc.documentElement) {
      parts.unshift(seg(cur, false));
      const sel = parts.join(" > ");
      // Require at least two segments when possible for robustness.
      if ((parts.length >= 2 || cur.parentElement === doc.documentElement) && unique(sel, el)) {
        // Anchor on a meaningful container (element with a class or id) so the
        // selector does not start with a bare "tr:nth-of-type(3)" that could match
        // again if the page gains another list.
        let anchor = cur, best = sel, extra = parts.slice();
        while (anchor.classList.length === 0 && !anchor.id && extra.length < 6 &&
               anchor.parentElement && anchor.parentElement !== doc.documentElement && anchor.parentElement !== doc.body) {
          anchor = anchor.parentElement;
          extra.unshift(seg(anchor, false));
          const s2 = extra.join(" > ");
          if (unique(s2, el)) best = s2;
        }
        return { selector: best, verified: true };
      }
      if (cur.id && /^[A-Za-z][\w-]*$/.test(cur.id) && doc.querySelectorAll("#" + esc(cur.id)).length === 1) {
        const idSel = [cur.localName + "#" + esc(cur.id)].concat(parts.slice(1)).join(" > ");
        if (unique(idSel, el)) return { selector: idSel, verified: true };
      }
      cur = cur.parentElement;
    }
    // Fallback: full path with nth-of-type on every level.
    const full = [];
    for (let e = el; e && e.nodeType === 1 && e !== doc.documentElement; e = e.parentElement) full.unshift(seg(e, true));
    const fsel = "html > " + full.join(" > ");
    return { selector: fsel, verified: unique(fsel, el), reason: "fallback" };
  };

  return indices.map((i) => {
    const r = build(nodes[i]);
    r.index = i;
    return r;
  });
}
