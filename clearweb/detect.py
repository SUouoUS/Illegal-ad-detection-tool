"""Detection logic that does not need a browser.

Input: element records produced by collector.js for one frame.
Output: candidate findings (record index to locate, technique, evidence, reasons).

Two judgements are kept separate:
  * adjudge()  - is this text an illegal advertisement? (restored text, context rules)
  * techniques - which concealment/disguise technique is present? (raw text, computed style)
A finding needs both.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .textnorm import homoglyph_view, jamo_view, restore

RULES_PATH = Path(__file__).with_name("rules") / "ad_rules.json"
TECHNIQUES = ("HOMOGLYPH", "JAMO", "TRANSPARENT", "OFFSCREEN")


# ------------------------------------------------------------------ rules
class Rules:
    def __init__(self, path: Path = RULES_PATH):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.categories = d["categories"]
        self.cta = d["cta"]
        cp = d["contact_patterns"]
        self.mention = [re.compile(p, re.I) for p in cp["mention"]]
        self.identifier = [re.compile(p, re.I) for p in cp["identifier"]]
        self.code = [re.compile(p) for p in cp.get("code", [])]
        self.contact = self.mention + self.identifier
        self.benign = d["benign"]["terms"]
        self.benign_sq = [(t, _squash(t)) for t in self.benign]   # squashed once, not per call
        self.chosung = {k: v for k, v in d["chosung"].items() if not k.startswith("_")}
        self._latin = {}
        for cat, c in self.categories.items():
            for lvl in ("latin_strong", "latin_weak"):
                for w in c.get(lvl, []):
                    # long words may appear inside tokens (megacasino), short ones need boundaries
                    pat = re.escape(w) if len(w) >= 5 else r"(?<![a-z])" + re.escape(w) + r"(?![a-z])"
                    self._latin[(cat, lvl, w)] = re.compile(pat)


_RULES: Rules | None = None


def rules() -> Rules:
    global _RULES
    if _RULES is None:
        _RULES = Rules()
    return _RULES


_WS = re.compile(r"\s+")


def _squash(s: str) -> str:
    return _WS.sub("", s)


def term_hits(text: str) -> dict[str, set[str]]:
    """Return {'strong': {...}, 'weak': {...}} lexicon hits with 'category:term' labels."""
    r = rules()
    low = unicodedata.normalize("NFC", text).lower()
    sq = _squash(low)
    hits = {"strong": set(), "weak": set()}
    for cat, c in r.categories.items():
        for lvl in ("strong", "weak"):
            for t in c.get(lvl, []):
                if t in sq:
                    hits[lvl].add(f"{cat}:{t}")
    for (cat, lvl, w), rx in r._latin.items():
        if rx.search(low):
            hits["strong" if lvl == "latin_strong" else "weak"].add(f"{cat}:{w}")
    for k, v in r.chosung.items():
        if k in sq:
            hits["strong"].add(f"gambling:{v}({k})")
    # a strong term implies its own sub-words: drop weak hits contained in a strong hit
    strong_terms = [h.split(":", 1)[1] for h in hits["strong"]]
    hits["weak"] = {h for h in hits["weak"] if not any(h.split(":", 1)[1] in s for s in strong_terms)}
    return hits


@dataclass
class Verdict:
    is_ad: bool
    reasons: list[str] = field(default_factory=list)
    categories: set[str] = field(default_factory=set)
    excluded_by: list[str] = field(default_factory=list)
    strong: set[str] = field(default_factory=set)
    weak: set[str] = field(default_factory=set)
    cta: set[str] = field(default_factory=set)
    contact: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)


def adjudge(raw: str, restored: str, concealed: bool = False, obfuscated: bool = False) -> Verdict:
    """Decide whether text is an illegal advertisement, with readable reasons.

    Only the wording decides. Being hidden (`concealed`) never lowers the threshold:
    hidden menus, tabs and sliders are full of ordinary text. A disguised strong topic
    word (`obfuscated`, e.g. jamo-split "카지노") does count, because nobody disguises
    an ordinary word. Evidence types are kept apart: channel mentions and reachable
    identifiers (contact) versus sign-up/referral codes (code, only with an explicit
    "코드" context) - a bare letters+digits token such as a tournament or model name
    is neither.
    """
    r = rules()
    text = restored + "\n" + raw
    h = term_hits(text)
    sq = _squash(text.lower())
    cta = {t for t in r.cta if t in sq}
    contact = []
    for rx in r.contact:
        m = rx.search(restored) or rx.search(raw)
        if m:
            contact.append(m.group(0).strip())
    code = []
    for rx in r.code:
        m = rx.search(raw) or rx.search(restored)
        if m and m.group(0).strip() not in code:
            code.append(m.group(0).strip())
    benign = sorted({t for t, t_sq in r.benign_sq if t_sq in sq})

    S, W, C, T = len(h["strong"]), len(h["weak"]), len(cta), len(contact) + len(code)
    v = Verdict(False, strong=h["strong"], weak=h["weak"], cta=cta, contact=contact, code=code)
    v.categories = {x.split(":")[0] for x in h["strong"] | h["weak"]}
    short = len(_squash(restored)) <= 40

    ok = (
        (S >= 1 and (C >= 1 or T >= 1 or S >= 2 or W >= 1))
        or (W >= 1 and T >= 1 and (C >= 1 or W >= 2))
        or (W >= 2 and C >= 2)
        or (S >= 1 and short and obfuscated)
    )
    if not ok:
        return v

    # context: prevention / news / education / enforcement wording
    handle = bool(code) or any(rx.search(restored) or rx.search(raw) for rx in r.identifier)
    if benign and (not handle or len(benign) >= 2) and not (S >= 2 and C >= 2 and handle):
        v.excluded_by = benign
        return v

    v.is_ad = True
    labels = {k: c["label"] for k, c in r.categories.items()}
    if h["strong"]:
        v.reasons.append("주제어: " + ", ".join(sorted(_lbl(x, labels) for x in h["strong"])))
    if h["weak"]:
        v.reasons.append("보조 주제어: " + ", ".join(sorted(_lbl(x, labels) for x in h["weak"])))
    if cta:
        v.reasons.append("유도 표현: " + ", ".join(sorted(cta)))
    if contact:
        v.reasons.append("연락·접속 수단: " + ", ".join(contact[:4]))
    if code:
        v.reasons.append("가입·추천 코드: " + ", ".join(code[:3]))
    if S >= 1 and short and obfuscated and not (C or T or W or S >= 2):
        v.reasons.append("짧은 주제어가 글자 변형으로 위장됨")
    return v


def _lbl(hit: str, labels: dict) -> str:
    cat, term = hit.split(":", 1)
    return f"{term}({labels.get(cat, cat)})"


# ------------------------------------------------------------------ obfuscation
def _new_terms(raw: str, view: str) -> set[str]:
    before = term_hits(raw)
    after = term_hits(view)
    return (after["strong"] | after["weak"]) - (before["strong"] | before["weak"])


_CYR_GRK = re.compile(r"[Ͱ-ϿЀ-ӿ]")
_FULL_LETTER = re.compile(r"[Ａ-Ｚａ-ｚ]")


def obfuscation(raw: str, is_ad: bool) -> dict[str, list[str]]:
    """Return {technique: [reasons]} for HOMOGLYPH / JAMO in raw text."""
    out: dict[str, list[str]] = {}
    hv, hch = homoglyph_view(raw)
    if hch:
        new = _new_terms(raw, hv)
        mixed = [m.group(0) for m in re.finditer(r"\S+", raw)
                 if _CYR_GRK.search(m.group(0)) and re.search(r"[A-Za-z]", m.group(0))]
        full = [m.group(0) for m in re.finditer(r"\S+", raw) if len(_FULL_LETTER.findall(m.group(0))) >= 2]
        pairs = sorted({f"{c.src}→{c.dst}" for c in hch})[:8]
        if new:
            out["HOMOGLYPH"] = [f"닮은꼴 문자 복원 후 드러난 주제어: {', '.join(sorted(t.split(':', 1)[1] for t in new))}",
                                "치환: " + " ".join(pairs)]
        elif is_ad and (mixed or full):
            out["HOMOGLYPH"] = [("서로 다른 문자 체계가 섞인 단어: " + ", ".join(mixed[:3])) if mixed
                                else ("전각 영문자 사용: " + ", ".join(full[:3])),
                                "치환: " + " ".join(pairs)]
    jv, jch = jamo_view(raw)
    chosung_hit = [k for k in rules().chosung if k in _squash(raw)]
    if jch or chosung_hit:
        new = _new_terms(raw, jv) if jch else set()
        real = [c for c in jch if sum(1 for ch in c.dst if 0xAC00 <= ord(ch) <= 0xD7A3) >= 2
                or any(0x1100 <= ord(ch) <= 0x11FF for ch in c.src)]
        runs = [f"{c.src} → {c.dst}" for c in jch][:4]
        if new:
            out["JAMO"] = [f"자모 조합 후 드러난 주제어: {', '.join(sorted(t.split(':', 1)[1] for t in new))}"] + runs
        elif chosung_hit:
            out["JAMO"] = ["초성 표기: " + ", ".join(f"{k}({rules().chosung[k]})" for k in chosung_hit)] + runs
        elif is_ad and real:
            out["JAMO"] = ["분해된 자모 조합: " + "; ".join(runs)]
    return out


# ------------------------------------------------------------------ units
def _collapse(s: str) -> str:
    return " ".join(s.split())


@dataclass
class Candidate:
    index: int                 # record index used as location
    technique: str
    evidence: str
    restored: str
    ad_reasons: list[str]
    tech_reasons: list[str]
    unit: int                  # ad unit record index (for grouping in the UI)
    changes: list[dict] = field(default_factory=list)


def analyze_frame(nodes: list[dict], report_excluded: bool = True) -> tuple[list[Candidate], list[dict]]:
    """Find ad units and their techniques in one frame's records."""
    by_i = {n["i"]: n for n in nodes}
    children: dict[int, list[int]] = {}
    for n in nodes:
        children.setdefault(n["p"], []).append(n["i"])

    def ancestors(i: int):
        p = by_i[i]["p"]
        while p != -1 and p in by_i:
            yield p
            p = by_i[p]["p"]

    def descendants(i: int):
        stack = list(children.get(i, []))
        while stack:
            c = stack.pop()
            yield c
            stack.extend(children.get(c, []))

    # 0) content boundaries: a record whose children are independent items (list items,
    #    rows, cards/slides, blocks linking to different pages) is never judged as one
    #    text - evidence from different news items must not be combined. The items
    #    themselves are still judged one by one.
    hrefs: dict[int, set[str]] = {}
    for n in nodes:
        if n.get("href"):
            for k in [n["i"], *ancestors(n["i"])]:
                hs = hrefs.setdefault(k, set())
                if len(hs) < 4:
                    hs.add(n["href"])
    composite = {n["i"] for n in nodes if _is_composite(n["i"], by_i, children, hrefs)}

    # 1) per-record restoration + verdict
    info: dict[int, dict] = {}
    for n in nodes:
        raw = n["text"]
        if len(_collapse(raw)) < 2 or n["i"] in composite:
            continue
        rs = restore(raw)
        concealed = bool(n["signals"])
        obf = bool(rs.changes)
        v = adjudge(raw, rs.text, concealed=concealed, obfuscated=obf)
        info[n["i"]] = {"rs": rs, "v": v}

    ad = {i for i, x in info.items() if x["v"].is_ad}
    # 2) minimal ad units: ad records with no ad descendant
    has_ad_desc = set()
    for i in ad:
        for a in ancestors(i):
            has_ad_desc.add(a)
    units = [i for i in ad if i not in has_ad_desc]

    # 2b) one sentence split over inline pieces (<span>카</span><b>지노</b> …): when the
    #     enclosing element holds more evidence, shares the same hiding and is not a list
    #     of items, it is the unit. A piece with its own hiding inside an ordinary
    #     sentence (p > span[opacity:0]) stays the unit because the hiding differs.
    def evidence(i: int) -> set:
        v = info[i]["v"]
        return set(v.strong) | set(v.weak) | set(v.cta) | set(v.contact) | set(v.code)

    def hide_sig(i: int) -> frozenset:
        return frozenset((sg["technique"], sg["root"]) for sg in by_i[i]["signals"])

    lifted = set()
    for u in units:
        cur = u
        while True:
            par = by_i[cur]["p"]
            if (par not in info or not info[par]["v"].is_ad or not by_i[cur].get("inline")
                    or hide_sig(cur) != hide_sig(par) or not evidence(par) > evidence(cur)):
                break
            cur = par
        lifted.add(cur)
    units = [u for u in lifted if not any(a in lifted for a in ancestors(u))]

    excluded = []
    if report_excluded:
        # hidden texts that looked like ads but were ruled out by context; one per hiding root
        seen_roots = set()
        for i, x in sorted(info.items()):
            v = x["v"]
            sig = by_i[i]["signals"]
            if not (v.excluded_by and sig):
                continue
            root = sig[0]["root"] if sig[0]["root"] in by_i else i
            if root in seen_roots:
                continue
            seen_roots.add(root)
            excluded.append({"index": i, "text": _collapse(by_i[i]["text"])[:200],
                             "hiding": sig[0]["detail"],
                             "reason": "예방·보도·안내 문맥: " + ", ".join(v.excluded_by[:5])})

    cands: list[Candidate] = []

    def hide_map(i: int) -> dict[str, tuple[int, list[str]]]:
        """technique -> (root record, reasons) from the record's own/ancestor signals."""
        m: dict[str, tuple[int, list[str]]] = {}
        for s in by_i[i]["signals"]:
            root = s["root"] if s["root"] in by_i else i
            t = s["technique"]
            if t in m:
                m[t][1].append(s["detail"])
            else:
                m[t] = (root, [s["detail"]])
        return m

    for u in units:
        n = by_i[u]
        rs, v = info[u]["rs"], info[u]["v"]
        obf = obfuscation(n["text"], True)
        hm = hide_map(u)
        if hm:
            # location: the highest hiding root that holds exactly this text, else the unit
            loc = u
            ut = _collapse(n["text"])
            for t, (root, _) in hm.items():
                if root != u and root in by_i and _collapse(by_i[root]["text"]) == ut:
                    if loc == u or root in set(ancestors(loc)):
                        loc = root
            for t, (root, why) in hm.items():
                cands.append(_cand(loc, t, by_i, rs, v, why, u))
            for t, why in obf.items():
                cands.append(_cand(loc, t, by_i, rs, v, why, u))
            continue
        # visible unit: hidden parts inside it?
        hidden_roots: dict[int, dict] = {}
        for d in descendants(u):
            dm = hide_map(d)
            for t, (root, why) in dm.items():
                r = root if (root == d or root in set(descendants(u))) else d
                hidden_roots.setdefault(r, {}).setdefault(t, why)
        reported_hidden = False
        for r, tmap in hidden_roots.items():
            rt = by_i[r]["text"]
            rrs = restore(rt)
            hv = adjudge(rt, rrs.text, concealed=True, obfuscated=bool(rrs.changes))
            if hv.strong or hv.weak or hv.contact or len(hv.cta) >= 1:
                reported_hidden = True
                for t, why in tmap.items():
                    cands.append(_cand(r, t, by_i, rrs, v, why + ["광고 문구의 일부가 숨겨짐"], u))
                for t, why in obfuscation(rt, True).items():
                    cands.append(_cand(r, t, by_i, rrs, v, why, u))
        if not reported_hidden:
            for t, why in obf.items():
                cands.append(_cand(u, t, by_i, rs, v, why, u))

    # 3) group units under one hidden root that holds nothing but ad text
    cands = _merge_pure_roots(cands, by_i, info, ancestors)
    # 4) dedupe (location, technique)
    seen, out = set(), []
    for c in cands:
        k = (c.index, c.technique)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out, excluded


ITEM_TAGS = {"li", "tr", "article", "dt", "dd", "option"}


def _is_composite(i: int, by_i: dict, children: dict, hrefs: dict) -> bool:
    kids = [c for c in children.get(i, []) if c in by_i]
    if sum(1 for c in kids if by_i[c]["tag"] in ITEM_TAGS) >= 2:
        return True
    blocks = [c for c in kids if not by_i[c].get("inline")]
    linked = [c for c in blocks if hrefs.get(c)]
    if len(linked) >= 2 and len({min(hrefs[c]) for c in linked}) >= 2:
        return True
    sigs: dict[tuple, int] = {}
    for c in blocks:
        if by_i[c].get("cls"):
            key = (by_i[c]["tag"], by_i[c]["cls"])
            sigs[key] = sigs.get(key, 0) + 1
            if sigs[key] >= 2:
                return True
    return False


def _cand(loc, t, by_i, rs, v, why, unit) -> Candidate:
    return Candidate(
        index=loc, technique=t,
        evidence=_collapse(by_i[loc]["text"])[:500],
        restored=_collapse(rs.text)[:500],
        ad_reasons=list(v.reasons), tech_reasons=list(why), unit=unit,
        changes=[{"kind": c.kind, "src": c.src, "dst": c.dst} for c in rs.changes][:20],
    )


def _merge_pure_roots(cands, by_i, info, ancestors):
    """If several ad units share a hiding root whose whole text is covered by those
    units, report the root once instead of each unit."""
    by_root: dict[tuple[int, str], list[Candidate]] = {}
    for c in cands:
        if c.technique not in ("TRANSPARENT", "OFFSCREEN"):
            continue
        for s in by_i[c.unit]["signals"]:
            if s["technique"] == c.technique and s["root"] in by_i and s["root"] != c.index:
                by_root.setdefault((s["root"], c.technique), []).append(c)
    replace: dict[int, int] = {}
    for (root, t), cs in by_root.items():
        units = {c.unit for c in cs}
        if len(units) < 2:
            continue
        total = len(_squash(by_i[root]["text"]))
        covered = sum(len(_squash(by_i[u]["text"])) for u in units)
        if total and covered >= 0.9 * total:
            for c in cs:
                replace[c.index] = root
    if not replace:
        return cands
    for c in cands:
        if c.index in replace:
            c.index = replace[c.index]
            c.evidence = _collapse(by_i[c.index]["text"])[:500]
    return cands
