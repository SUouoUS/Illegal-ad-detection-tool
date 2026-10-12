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
        self.private = [re.compile(p, re.I) for p in cp["identifier"]]
        self.web = [re.compile(p, re.I) for p in cp.get("web_address", [])]
        self.identifier = self.web + self.private
        self.code = [re.compile(p) for p in cp.get("code", [])]
        self.contact = self.mention + self.identifier
        self.cta_sq = [(t, _squash(t.lower())) for t in self.cta]
        self.ordinary = {k: tuple(_squash(w.lower()) for w in v)
                         for k, v in d.get("ordinary_words", {}).items() if not k.startswith("_")}
        self.cta_inquiry = set(d.get("cta_inquiry", []))
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


class _Spaced:
    """Text with spaces removed, mapped back to the original positions.

    Ads split words with spaces ("카 지 노") so terms are searched without spaces, but a
    match may not begin in the middle of a word: "청와대 출입" must not read as "대출".
    A match inside one word, or starting at a word start, is accepted."""

    def __init__(self, text: str):
        self.text = text
        self.pos = [k for k, ch in enumerate(text) if not ch.isspace()]
        self.sq = "".join(text[k] for k in self.pos)

    def find(self, term_sq: str, ordinary: tuple[str, ...] = ()) -> tuple[int, int] | None:
        """First acceptable match. `ordinary`: everyday words that contain the term
        ("오피스" for "오피"); a match inside one of them is skipped, the search goes on, so
        the same term standing on its own elsewhere in the text still matches."""
        if not term_sq:
            return None
        inside = []
        for w in ordinary:
            j = self.sq.find(w)
            while j != -1:
                inside.append((j, j + len(w)))
                j = self.sq.find(w, j + 1)
        k = self.sq.find(term_sq)
        while k != -1:
            a, b = self.pos[k], self.pos[k + len(term_sq) - 1] + 1
            word_ok = not any(ch.isspace() for ch in self.text[a:b]) or a == 0 or self.text[a - 1].isspace()
            if word_ok and not any(s <= k and k + len(term_sq) <= e for s, e in inside):
                return a, b
            k = self.sq.find(term_sq, k + 1)
        return None


def term_spans(text: str) -> dict[str, dict[str, tuple[int, int]]]:
    """{'strong': {label: (start, end)}, 'weak': {...}} - where each lexicon term matched
    in NFC-lowercased `text` ('category:term' labels)."""
    r = rules()
    low = unicodedata.normalize("NFC", text).lower()
    sp = _Spaced(low)
    hits: dict[str, dict[str, tuple[int, int]]] = {"strong": {}, "weak": {}}
    for cat, c in r.categories.items():
        for lvl in ("strong", "weak"):
            for t in c.get(lvl, []):
                m = sp.find(t, r.ordinary.get(t, ()))
                if m:
                    hits[lvl][f"{cat}:{t}"] = m
    for (cat, lvl, w), rx in r._latin.items():
        m = rx.search(low)
        if m:
            hits["strong" if lvl == "latin_strong" else "weak"][f"{cat}:{w}"] = m.span()
    for k, v in r.chosung.items():
        m = sp.find(k)
        if m:
            hits["strong"][f"gambling:{v}({k})"] = m
    # a strong term implies its own sub-words: drop weak hits contained in a strong hit
    strong_terms = [h.split(":", 1)[1] for h in hits["strong"]]
    hits["weak"] = {h: m for h, m in hits["weak"].items() if not any(h.split(":", 1)[1] in s for s in strong_terms)}
    return hits


def term_hits(text: str) -> dict[str, set[str]]:
    """Return {'strong': {...}, 'weak': {...}} lexicon hits with 'category:term' labels."""
    h = term_spans(text)
    return {"strong": set(h["strong"]), "weak": set(h["weak"])}


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
    # what matched where: {"kind", "rule", "text", "where" (원문/복원문), "pos"} - technical detail
    matches: list[dict] = field(default_factory=list)


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
    sq = _squash(text.lower())
    matches: list[dict] = []
    views = [("원문", raw)] + ([("복원문", restored)] if restored != raw else [])

    def note(kind, rule, where, s, a, b):
        matches.append({"kind": kind, "rule": rule, "text": s[a:b], "where": where, "pos": a})

    h = {"strong": set(), "weak": set()}
    for where, t in views:
        sp = term_spans(t)
        low = unicodedata.normalize("NFC", t).lower()
        for lvl, kind in (("strong", "주제어"), ("weak", "보조 주제어")):
            for lab, (a, b) in sp[lvl].items():
                if lab not in h[lvl]:
                    h[lvl].add(lab)
                    note(kind, lab.split(":", 1)[1], where, low, a, b)
    h["weak"] = {x for x in h["weak"] if not any(x.split(":", 1)[1] in s.split(":", 1)[1] for s in h["strong"])}
    cta = set()
    for where, t in views:
        sp = _Spaced(unicodedata.normalize("NFC", t).lower())
        for c, c_sq in r.cta_sq:
            if c not in cta and (m := sp.find(c_sq)):
                cta.add(c)
                note("유도 표현", c, where, sp.text, *m)

    def found(patterns, kind, order):
        out = []
        for rx in patterns:
            for where, t in sorted(views, key=lambda x: order.index(x[0])):
                m = rx.search(t)
                if m:
                    s = m.group(0).strip()
                    # one address matched by two patterns (full URL and bare domain) counts once
                    if not any(s in o or o in s for o, _ in out):
                        out.append((s, rx))
                        note(kind, s, where, t, m.start(), m.end())
                    break
        return out

    mention = found(r.mention, "연락·접속 수단", ["복원문", "원문"])
    private = found(r.private, "연락·접속 수단", ["복원문", "원문"])
    web = [x for x in found(r.web, "연락·접속 수단", ["복원문", "원문"])
           if not any(x[0] in o or o in x[0] for o, _ in private)]
    contact = [s for s, _ in mention + private + web]
    code = [s for s, _ in found(r.code, "가입·추천 코드", ["원문", "복원문"])]
    benign = sorted({t for t, t_sq in r.benign_sq if t_sq in sq})

    S, W, C, T = len(h["strong"]), len(h["weak"]), len(cta), len(contact) + len(code)
    # a reachable private channel (phone, messenger, @handle) or code, versus only an
    # ordinary web address - public notices link to homepages all the time
    P = len(mention) + len(private) + len(code)
    # inquiry words (문의, 상담) are in every public notice: together they count as one
    C2 = len(cta - r.cta_inquiry) + (1 if cta & r.cta_inquiry else 0)
    v = Verdict(False, strong=h["strong"], weak=h["weak"], cta=cta, contact=contact, code=code, matches=matches)
    v.categories = {x.split(":")[0] for x in h["strong"] | h["weak"]}
    short = len(_squash(restored)) <= 40

    ok = (
        (S >= 1 and (C >= 1 or T >= 1 or S >= 2 or W >= 1))
        # one ambiguous word (대출, 성인, 충전 ...) with only a web address also needs a
        # second topic word or two calls to action; a private channel is enough
        or (W >= 1 and T >= 1 and (C >= 1 or W >= 2) and (P >= 1 or W >= 2 or C2 >= 2))
        or (W >= 2 and C >= 2)
        or (S >= 1 and short and obfuscated)
    )
    if not ok:
        return v
    if S == 0 and P == 0 and not _clustered(matches, r.cta_inquiry):
        # only ambiguous words and web addresses, spread over a long notice ("대상: 성인 80명
        # ... 수강료: 무료 ... 신청: https://... 문의"): in an ad they stand together
        v.excluded_by = ["근거가 긴 안내문 여러 곳에 흩어져 있음"]
        return v

    # context: prevention / news / education / enforcement wording
    handle = bool(code) or bool(private) or bool(web)
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


CLUSTER_CHARS = 60


def _clustered(matches: list[dict], inquiry: set, width: int = CLUSTER_CHARS) -> bool:
    """Weak-only evidence must meet the ad rule inside one stretch of `width` characters
    of the same text (원문 or 복원문)."""
    for where in {m["where"] for m in matches}:
        ms = sorted((m for m in matches if m["where"] == where), key=lambda m: m["pos"])
        for i, first in enumerate(ms):
            win = [m for m in ms[i:] if m["pos"] + len(m["text"]) <= first["pos"] + width]
            w = {m["rule"] for m in win if m["kind"] == "보조 주제어"}
            c = {m["rule"] for m in win if m["kind"] == "유도 표현"}
            t = [m for m in win if m["kind"] in ("연락·접속 수단", "가입·추천 코드")]
            c2 = len(c - inquiry) + (1 if c & inquiry else 0)
            if (w and t and (c or len(w) >= 2) and (len(w) >= 2 or c2 >= 2)) or (len(w) >= 2 and len(c) >= 2):
                return True
    return False


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


def _original(s: str) -> str:
    """evidence_text: the collected text as it is (공식 요건 '정규화 전 원본 그대로'). Only the
    whitespace before the first and after the last character is removed - that is the page's
    indentation around the element, not part of the text."""
    return s.strip()


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
    ad_matches: list[dict] = field(default_factory=list)


def analyze_frame(nodes: list[dict], report_excluded: bool = True,
                  visible: list | None = None) -> tuple[list[Candidate], list[dict]]:
    """Find ad units and their techniques in one frame's records.

    `visible`, when given, collects ad units that met the ad rules but show none of the
    four official techniques (plain visible text). They never become official findings;
    the screen lists them as additional suspects with the reason they were left out."""
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
                             "reason": (v.excluded_by[0] if v.excluded_by[0].startswith("근거가")
                                        else "예방·보도·안내 문맥: " + ", ".join(v.excluded_by[:5]))})

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
            # the hidden part must carry evidence of its own; a CTA word alone ("문의") is not
            if hv.strong or hv.weak or hv.contact or hv.code:
                reported_hidden = True
                scope = [f"판정 범위: 숨긴 부분이 속한 상위 문구 전체({len(_squash(n['text']))}자)",
                         "숨긴 부분의 근거: " + ", ".join(_evidence_terms(hv))]
                for t, why in tmap.items():
                    cands.append(_cand(r, t, by_i, rrs, v, why + ["광고 문구의 일부가 숨겨짐"], u, scope))
                for t, why in obfuscation(rt, True).items():
                    cands.append(_cand(r, t, by_i, rrs, v, why, u, scope))
        if not reported_hidden:
            for t, why in obf.items():
                cands.append(_cand(u, t, by_i, rs, v, why, u))
            if not obf and visible is not None:
                visible.append({"index": u, "evidence": _original(n["text"]), "restored": _collapse(rs.text)[:500],
                                "ad_reasons": list(v.reasons), "ad_matches": _matches(v),
                                "excluded_why": "숨김·위장(공식 4종 기법)이 확인되지 않아 공식 결과(result.json)에서 제외"})

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


def _cand(loc, t, by_i, rs, v, why, unit, scope=None) -> Candidate:
    return Candidate(
        index=loc, technique=t,
        evidence=_original(by_i[loc]["text"]),
        restored=_collapse(rs.text)[:500],
        ad_reasons=list(v.reasons) + list(scope or []), tech_reasons=list(why), unit=unit,
        changes=[{"kind": c.kind, "src": c.src, "dst": c.dst} for c in rs.changes][:20],
        ad_matches=_matches(v),
    )


def _matches(v: Verdict, limit: int = 12) -> list[dict]:
    """Matched strings and their positions, for the technical detail."""
    return [dict(m, text=m["text"][:60]) for m in v.matches[:limit]]


def _evidence_terms(v: Verdict) -> list[str]:
    return ([x.split(":", 1)[1] for x in sorted(v.strong | v.weak)] + v.contact[:2] + v.code[:2]) or ["-"]


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
            c.evidence = _original(by_i[c.index]["text"])
    return cands


# ------------------------------------------------------------------ text outside the page body
def judge_attr(text: str):
    """Text that only search engines / screen readers get (title, meta, alt, title=, aria-label):
    the same ad rules as page text. Returns the Verdict when it is an ad, else None."""
    raw = text.strip()
    if len(_collapse(raw)) < 2:
        return None
    rs = restore(raw)
    v = adjudge(raw, rs.text)
    return v if v.is_ad else None


_HREF_SPLIT = re.compile(r"[/._\-?=&:#]+")


def judge_link(text: str, href: str):
    """A link whose words are empty or hidden, leading to another site. It counts only when the
    link's own words are not an ad (those are already judged as page text) and the address
    adds the evidence (casino-xxx.example/join ...)."""
    if judge_attr(text):
        return None
    words = _HREF_SPLIT.sub(" ", href.split("://", 1)[-1])
    raw = (text + " " + words + " " + href).strip()
    v = adjudge(raw, restore(raw).text)
    if not v.is_ad or not (v.strong or v.weak):
        return None
    return v


# ------------------------------------------------------------------ text in images (OCR)
def judge_image(blocks: list[dict]) -> dict:
    """Ad judgement for OCR text of ONE image.

    * each text block (caption / banner line group) is judged on its own with the same
      rules as page text; words from different blocks or different images are not combined
    * 'suspect' only when one block meets the rules on its own
    * 'uncertain' when evidence exists but is not enough: the rules are met only if
      separate blocks are joined, or a block has a topic word without anything else
    * OCR can misread or normalise characters, so jamo / look-alike shapes in OCR output
      never become JAMO / HOMOGLYPH findings; they are only mentioned as a note
    """
    out = {"level": "none", "block": None, "reasons": [], "excluded_by": [], "note": "", "blocks": []}
    texts = [b["text"] for b in blocks if len(_squash(b["text"])) >= 2]
    if not texts:
        out["note"] = "인식된 글자가 없음"
        return out
    best = None
    weak_hint = None
    shapes = False
    for k, b in enumerate(blocks):
        t = b["text"]
        if len(_squash(t)) < 2:
            continue
        rs = restore(t)
        shapes = shapes or bool(rs.changes)
        v = adjudge(t, rs.text, concealed=False, obfuscated=False)
        out["blocks"].append({"index": k, "is_ad": v.is_ad, "excluded_by": v.excluded_by,
                              "strong": sorted(v.strong), "weak": sorted(v.weak)})
        if v.is_ad and best is None:
            best = (k, v)
        if v.excluded_by:
            out["excluded_by"] = sorted(set(out["excluded_by"]) | set(v.excluded_by))
        if not v.is_ad and not v.excluded_by and v.strong and weak_hint is None:
            weak_hint = (k, v)
    if best:
        k, v = best
        out.update(level="suspect", block=k, reasons=list(v.reasons))
    else:
        joined = " ".join(texts)
        jv = adjudge(joined, restore(joined).text)
        if jv.is_ad and len(texts) > 1:
            out.update(level="uncertain", reasons=list(jv.reasons),
                       note="서로 떨어진 문구를 합쳤을 때만 기준을 충족함 — 한 문구 안의 근거는 부족")
        elif weak_hint:
            k, v = weak_hint
            labels = {c: d["label"] for c, d in rules().categories.items()}
            out.update(level="uncertain", block=k,
                       reasons=["주제어: " + ", ".join(sorted(_lbl(x, labels) for x in v.strong))],
                       note="주제어만 있고 유도 표현·연락 수단이 없음")
        elif out["excluded_by"]:
            out["note"] = "예방·보도·안내 문맥으로 판단: " + ", ".join(out["excluded_by"][:5])
    if shapes:
        out["note"] = (out["note"] + " · " if out["note"] else "") + \
            "자모·닮은꼴 형태가 보이지만 OCR 오인식일 수 있어 은닉 기법으로 판단하지 않음"
    return out
