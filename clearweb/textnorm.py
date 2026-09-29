"""Text restoration for analysis.

The raw text is never modified; `restore()` returns an analysis string plus a list of
the transformations that were applied, so every change can be traced and shown.

Two independent passes:
  * homoglyph: look-alike characters (fullwidth, Cyrillic/Greek in Latin words,
    digits used as letters) mapped to their Latin/ASCII counterpart - token-scoped.
  * jamo: sequences of separated Hangul jamo (compatibility or conjoining) composed
    back into syllables with a small state machine.
Unicode normalization alone is not relied on: NFKC is only used per character for
fullwidth / mathematical / enclosed forms, and its result is checked.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------- homoglyphs
# Cyrillic / Greek / other letters that look like Latin letters.
CONFUSABLE = {
    # Cyrillic lower
    "а": "a", "в": "b", "е": "e", "ё": "e", "һ": "h", "і": "i", "ї": "i", "ј": "j", "к": "k",
    "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
    "ѕ": "s", "ԁ": "d", "ԛ": "q", "ԝ": "w", "ь": "b", "ы": "bl", "г": "r", "п": "n",
    # Cyrillic upper
    "А": "A", "В": "B", "Е": "E", "Ё": "E", "Н": "H", "І": "I", "Ј": "J", "К": "K", "М": "M",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "Ү": "Y", "Ԛ": "Q", "Ԝ": "W",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
    "υ": "u", "χ": "x", "γ": "y", "ω": "w",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Latin variants / symbols
    "ı": "i", "ȷ": "j", "ℓ": "l", "ǀ": "l", "ɑ": "a", "ɡ": "g", "ɩ": "i", "ʀ": "r", "ᴀ": "a",
    "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k",
    "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v",
    "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
}
# digits / symbols that stand in for letters inside a Latin word
LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "|": "l", "!": "i"}

LATIN_RE = re.compile(r"[A-Za-z]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _nfkc_char(ch: str) -> str | None:
    """Map fullwidth/mathematical/enclosed forms to ASCII when NFKC does so."""
    if ord(ch) < 0x80:
        return None
    n = unicodedata.normalize("NFKC", ch)
    if n != ch and len(n) == 1 and ord(n) < 0x80:
        return n
    return None


@dataclass
class Change:
    kind: str       # "homoglyph" | "leet" | "jamo"
    start: int      # offset in raw text
    end: int
    src: str
    dst: str


@dataclass
class Restored:
    raw: str
    text: str                       # restored analysis string
    changes: list[Change] = field(default_factory=list)

    def kinds(self) -> set[str]:
        return {c.kind for c in self.changes}


TOKEN_RE = re.compile(r"[^\s가-힣ㄱ-ㆎᄀ-ᇿ,.;:()\[\]{}<>\"'“”‘’/·]+")


def _homoglyph_tokens(raw: str) -> list[Change]:
    """Find look-alike substitutions, one non-Hangul token at a time.

    Only letter look-alikes are recorded (a token made of fullwidth digits or
    punctuation such as "３０％" is a formatting habit, not a disguise). Whether a
    substitution matters is decided later by the ad lexicon, so genuine Cyrillic or
    Greek words produce changes here but never a HOMOGLYPH finding on their own.
    """
    changes: list[Change] = []
    # "@" inside an e-mail address (found in the whole text: tokens split at ".") is not a letter
    email_at = {e.start() + e.group(0).index("@") for e in _EMAIL.finditer(raw)}
    for m in TOKEN_RE.finditer(raw):
        tok = m.group(0)
        mapped = list(tok)
        subs: list[Change] = []
        for k, ch in enumerate(tok):
            t = _nfkc_char(ch)
            if t is None:
                t = CONFUSABLE.get(ch)
            if t is not None:
                mapped[k] = t
                if t.isalpha():
                    subs.append(Change("homoglyph", m.start() + k, m.start() + k + 1, ch, t))
        changes.extend(subs)
        # digits/symbols used as letters inside a Latin word: c4sino, b3t, s1ot
        n_letters = sum(1 for c in mapped if c.isascii() and c.isalpha())
        if n_letters >= 2:
            for k, ch in enumerate(mapped):
                if ch not in LEET or (m.start() + k) in email_at:   # help@example.kr is an address, not "a"
                    continue
                prev_l = k > 0 and mapped[k - 1].isascii() and mapped[k - 1].isalpha()
                next_l = k + 1 < len(mapped) and mapped[k + 1].isascii() and mapped[k + 1].isalpha()
                if prev_l and next_l:
                    changes.append(Change("leet", m.start() + k, m.start() + k + 1, tok[k], LEET[ch]))
            changes.extend(_edge_leet(tok, mapped, m.start(), changes))
    return changes


_LATIN_WORDS: list[str] | None = None


def _latin_words() -> list[str]:
    """Latin topic words from the rules file (casino, toto, slot ...)."""
    global _LATIN_WORDS
    if _LATIN_WORDS is None:
        import json
        from pathlib import Path
        d = json.loads((Path(__file__).with_name("rules") / "ad_rules.json").read_text(encoding="utf-8"))
        _LATIN_WORDS = sorted({w for c in d["categories"].values()
                               for lvl in ("latin_strong", "latin_weak") for w in c.get(lvl, []) if len(w) >= 3})
    return _LATIN_WORDS


def _edge_leet(tok: str, mapped: list[str], start: int, done: list[Change]) -> list[Change]:
    """A single digit at the start or end of a Latin word used as a letter (c4sin0, 5lot).
    Applied only when it reveals a topic word that was not there before, so model names,
    codes and years (RTX5090, WIN11, LUCKY7, TEST77) stay as they are."""
    cur = list(mapped)
    for c in done:
        if start <= c.start < start + len(tok):
            cur[c.start - start] = c.dst
    letters = sum(1 for c in cur if c.isascii() and c.isalpha())
    if letters < 3:
        return []
    edges = []
    for k in (0, len(cur) - 1):
        ch = cur[k]
        if ch not in "013457" or ch not in LEET:
            continue
        inner = k + 1 if k == 0 else k - 1
        outer = k - 1 if k > 0 else None
        if not (cur[inner].isascii() and cur[inner].isalpha()):
            continue                                  # "2026" or "77" at the edge: not a letter stand-in
        if outer is not None and 0 <= outer < len(cur) and cur[outer].isdigit():
            continue
        edges.append(k)
    if not edges:
        return []
    before = "".join(cur).lower()
    after_l = list(cur)
    for k in edges:
        after_l[k] = LEET[cur[k]]
    after = "".join(after_l).lower()
    if not any(w in after and w not in before for w in _latin_words()):
        return []
    return [Change("leet", start + k, start + k + 1, tok[k], LEET[cur[k]]) for k in edges]


def homoglyph_view(raw: str) -> tuple[str, list[Change]]:
    changes = _homoglyph_tokens(raw)
    if not changes:
        return raw, []
    out = list(raw)
    for c in changes:
        out[c.start] = c.dst
    return "".join(out), changes


# ---------------------------------------------------------------- jamo
# compatibility jamo -> (choseong index, jungseong index, jongseong index)
CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ",
        "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
JUNG_COMBO = {("ㅗ", "ㅏ"): "ㅘ", ("ㅗ", "ㅐ"): "ㅙ", ("ㅗ", "ㅣ"): "ㅚ", ("ㅜ", "ㅓ"): "ㅝ",
              ("ㅜ", "ㅔ"): "ㅞ", ("ㅜ", "ㅣ"): "ㅟ", ("ㅡ", "ㅣ"): "ㅢ"}
JONG_COMBO = {("ㄱ", "ㅅ"): "ㄳ", ("ㄴ", "ㅈ"): "ㄵ", ("ㄴ", "ㅎ"): "ㄶ", ("ㄹ", "ㄱ"): "ㄺ", ("ㄹ", "ㅁ"): "ㄻ",
              ("ㄹ", "ㅂ"): "ㄼ", ("ㄹ", "ㅅ"): "ㄽ", ("ㄹ", "ㅌ"): "ㄾ", ("ㄹ", "ㅍ"): "ㄿ", ("ㄹ", "ㅎ"): "ㅀ",
              ("ㅂ", "ㅅ"): "ㅄ"}
JONG_SPLIT = {v: k for k, v in JONG_COMBO.items()}

# conjoining jamo (U+1100 block) -> compatibility jamo
_CONJ_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_CONJ_JONG = JONG[1:]


def _to_compat(ch: str) -> str:
    o = ord(ch)
    if 0x1100 <= o <= 0x1112:
        return _CONJ_CHO[o - 0x1100]
    if 0x1161 <= o <= 0x1175:
        return JUNG[o - 0x1161]
    if 0x11A8 <= o <= 0x11C2:
        return _CONJ_JONG[o - 0x11A8]
    return ch


def is_jamo(ch: str) -> bool:
    o = ord(ch)
    return 0x3131 <= o <= 0x3163 or 0x1100 <= o <= 0x11FF


def _syll(c: str, v: str, t: str = "") -> str:
    return chr(0xAC00 + (CHO.index(c) * 21 + JUNG.index(v)) * 28 + JONG.index(t))


def compose_jamo(seq: str) -> str:
    """Compose a string of compatibility jamo into syllables (greedy, with
    final-consonant lookahead). Unpaired jamo are kept as is."""
    out: list[str] = []
    i, n = 0, len(seq)
    while i < n:
        c = seq[i]
        if c in CHO and i + 1 < n and seq[i + 1] in JUNG:
            v = seq[i + 1]
            j = i + 2
            if j < n and (v, seq[j]) in JUNG_COMBO:
                v = JUNG_COMBO[(v, seq[j])]
                j += 1
            t = ""
            # final consonant only if not followed by a vowel (then it starts next syllable)
            if j < n and seq[j] in JONG and seq[j] != "":
                if not (j + 1 < n and seq[j + 1] in JUNG):
                    t = seq[j]
                    j += 1
                    if j < n and (t, seq[j]) in JONG_COMBO and not (j + 1 < n and seq[j + 1] in JUNG):
                        t = JONG_COMBO[(t, seq[j])]
                        j += 1
            if t and t not in JONG:
                t = ""
            out.append(_syll(c, v, t))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


# separators tolerated between single jamo inside one run ("ㅋ ㅏ ㅈ ㅣ", "ㅋ.ㅏ.ㅈ")
_SEP = set(" .-_·*~ ")


def jamo_view(raw: str) -> tuple[str, list[Change]]:
    """Recompose decomposed Hangul. Also attaches jamo to an adjacent syllable
    (e.g. "카ㅈㅣ노"). Returns restored text and changes (raw offsets)."""
    changes: list[Change] = []
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        if not is_jamo(raw[i]):
            out.append(raw[i])
            i += 1
            continue
        # collect a run of jamo possibly separated by single separators
        j = i
        seq = []
        while j < n:
            if is_jamo(raw[j]):
                seq.append(_to_compat(raw[j]))
                j += 1
            elif raw[j] in _SEP and j + 1 < n and is_jamo(raw[j + 1]) and seq and len(seq) >= 1 and _sep_run_ok(raw, i, j):
                j += 1
            else:
                break
        run = "".join(seq)
        composed = compose_jamo(run)
        n_syll = sum(1 for ch in composed if 0xAC00 <= ord(ch) <= 0xD7A3)
        conj = any(0x1100 <= ord(ch) <= 0x11FF for ch in raw[i:j])
        if n_syll >= 1 and (n_syll >= 2 or conj or len(run) >= 2):
            changes.append(Change("jamo", i, j, raw[i:j], composed))
            out.append(composed)
        else:
            out.append(raw[i:j])
        i = j
    return "".join(out), changes


def _sep_run_ok(raw: str, start: int, pos: int) -> bool:
    """Skip a separator only inside a spaced-out run such as 'ㅋ ㅏ ㅈ ㅣ', i.e. when the
    jamo before it is itself isolated (run start or preceded by a separator)."""
    if not is_jamo(raw[pos - 1]):
        return False
    return pos - 1 == start or raw[pos - 2] in _SEP


_HAS_JAMO = re.compile(r"[ᄀ-ᇿㄱ-ㅣ]")


def restore(raw: str) -> Restored:
    """Apply jamo composition then homoglyph mapping. Offsets in changes refer to raw."""
    # NFC first only for display-identical sequences is NOT done globally: conjoining
    # jamo are handled by jamo_view so that the change stays visible.
    j_text, j_changes = jamo_view(raw) if _HAS_JAMO.search(raw) else (raw, [])
    h_text, h_changes = homoglyph_view(raw)
    if not j_changes and not h_changes:
        return Restored(raw, raw, [])
    # combine: build from raw by applying both change sets (they do not overlap:
    # jamo operate on Hangul jamo, homoglyphs on non-Hangul characters)
    edits = sorted(j_changes + h_changes, key=lambda c: c.start)
    out, pos = [], 0
    for c in edits:
        if c.start < pos:
            continue
        out.append(raw[pos:c.start])
        out.append(c.dst)
        pos = c.end
    out.append(raw[pos:])
    return Restored(raw, "".join(out), edits)
