"""Crawl policy: which links to follow and in what order. Pure functions, no browser.

General rules (no site-specific code):
* scope: same site as the entry page (host, www. variant, subdomains of it)
* never follow state-changing links (logout, delete, ...) and skip links with little
  inspection value (login, search, write, sign-up, recommend, report, download, print ...)
* skip downloadable files
* query parameters are kept (they distinguish pages in the official scoring); only
  well-known tracking parameters that never change page content are dropped
* limits: depth, pages per URL pattern (path with numbers generalised + query names),
  and total pages (enforced by the scanner when pages are taken from the queue)
* priority: shallow pages first, links in the main content before navigation/footer,
  pages that share the entry path, earlier list pages before later ones
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .urlnorm import clean, compare_key

SKIP_EXT = re.compile(
    r"\.(pdf|hwp|hwpx|docx?|xlsx?|pptx?|zip|rar|7z|tar|gz|exe|msi|apk|dmg|iso|jpe?g|png|gif|bmp|svg|webp|ico|"
    r"mp3|mp4|avi|mov|wmv|mkv|webm|css|js|json|xml|txt|csv|woff2?|ttf|eot)$", re.I)

# --- state-changing actions (never visited)
UNSAFE_SUB = ("logout", "logoff", "signout", "log_out", "sign_out", "delete", "remove", "destroy",
              "withdraw", "unsubscribe", "unregister", "truncate")
UNSAFE_TOKEN = {"del", "drop", "reset", "out", "leave", "kill", "purge", "erase", "secession"}

# --- little inspection value (forms, account, search results, downloads, sharing)
LOW_VALUE_TOKEN = {
    "login", "logon", "signin", "join", "signup", "register", "regist", "registration",
    "search", "srch", "write", "modify", "edit", "reply", "recommend", "recom", "vote", "like",
    "report", "singo", "scrap", "download", "filedown", "down", "print", "share", "sns", "rss",
    "captcha", "password", "passwd", "pwd", "findid", "findpw", "mypage", "cart", "order",
    "payment", "subscribe",
}
# query keys whose *values* name an action (mode=write, act=login ...)
ACTION_KEYS = {"mode", "act", "action", "cmd", "command", "method", "proc", "task", "flag", "type", "job"}

# tracking parameters that never change page content; everything else is kept
TRACKING = {"gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "ttclid",
            "twclid", "li_fat_id", "mc_cid", "mc_eid", "_ga", "_gl", "srsltid"}

PAGE_KEYS = {"page", "pageindex", "pageno", "pagenum", "pg", "cpage", "curpage", "currentpage", "p", "start", "offset"}
WORD_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def _tokens(s: str) -> set[str]:
    """Words of a path/value, splitting camelCase: boardWrite.do -> {board, write, do}."""
    return {w.lower() for w in WORD_RE.findall(s)}


def unsafe_reason(url: str) -> str | None:
    s = urlsplit(url)
    hay = (s.path + "?" + s.query).lower()
    for w in UNSAFE_SUB:
        if w in hay:
            return w
    hit = _tokens(s.path + " " + s.query) & UNSAFE_TOKEN
    return sorted(hit)[0] if hit else None


def low_value_reason(url: str) -> str | None:
    s = urlsplit(url)
    toks = _tokens(s.path)
    for k, v in parse_qsl(s.query, keep_blank_values=True):
        if k.lower() in ACTION_KEYS:
            toks |= _tokens(v)
    hit = toks & LOW_VALUE_TOKEN
    return sorted(hit)[0] if hit else None


def strip_tracking(url: str) -> str:
    s = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
         if not (k.lower() in TRACKING or k.lower().startswith("utm_"))]
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q, doseq=True), s.fragment))


def site_of(host: str) -> str:
    host = host.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


_PAGE_NUM = re.compile(r"(?:(?<=[-_])|(?<=^))\d{1,4}(?=\.\w+$|$)|[-_]\d{1,4}(?=\.\w+$|$)")
_NUMERIC_SEG = re.compile(r"^(\d+|[0-9a-f]{12,}|[0-9a-f-]{32,36})$", re.I)


@dataclass
class Policy:
    entry: str
    max_pages: int = 50
    max_depth: int = 2
    per_pattern: int = 10
    sites: set[str] = field(default_factory=set)
    seen: set[str] = field(default_factory=set)             # keys queued, visited or rejected for good
    limited: dict[str, str] = field(default_factory=dict)    # key -> limit reason (may be admitted later)
    pattern_count: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)  # reason -> sample urls
    skipped_n: dict[str, int] = field(default_factory=dict)      # reason -> unique url count
    admitted: int = 1                                         # entry page counts
    queries: dict[str, list[tuple[frozenset, str]]] = field(default_factory=dict)  # path -> (query set, key)
    lateral: dict[str, set] = field(default_factory=dict)    # path-paged list -> pages treated as lateral
    superseded: set[str] = field(default_factory=set)        # variant keys whose plain URL came later

    def __post_init__(self):
        self.sites.add(site_of(urlsplit(self.entry).netloc))
        self.seen.add(compare_key(strip_tracking(clean(self.entry))))
        self._entry_segs = [x for x in urlsplit(self.entry).path.split("/") if x]
        e = urlsplit(strip_tracking(clean(self.entry)))
        self.queries.setdefault(site_of(e.netloc) + e.path, []).append(
            (frozenset(parse_qsl(e.query, keep_blank_values=True)), compare_key(strip_tracking(clean(self.entry)))))

    def allow_site_of(self, url: str):
        """Also accept the host the entry page redirected to."""
        self.sites.add(site_of(urlsplit(url).netloc))

    def in_scope(self, url: str) -> bool:
        s = urlsplit(url)
        if s.scheme not in ("http", "https"):
            return False
        h = site_of(s.netloc)
        return any(h == site or h.endswith("." + site) for site in self.sites)

    @staticmethod
    def pattern(url: str) -> str:
        """Path with ids generalised + sorted query names: /board/view/123?id=x -> /board/view/{n}?id"""
        s = urlsplit(url)
        segs = ["{n}" if _NUMERIC_SEG.match(x) else x for x in s.path.split("/")]
        names = sorted({k for k, _ in parse_qsl(s.query, keep_blank_values=True)})
        return f"{site_of(s.netloc)}{'/'.join(segs)}?{'&'.join(names)}"

    def _skip(self, reason: str, url: str, key: str, final: bool = True):
        if final:
            self.seen.add(key)
        elif self.limited.get(key) == reason:
            return                                   # already counted for this reason
        else:
            self.limited[key] = reason
        self.skipped_n[reason] = self.skipped_n.get(reason, 0) + 1
        lst = self.skipped.setdefault(reason, [])
        if len(lst) < 20:
            lst.append(url)

    def note(self, reason: str, url: str):
        """Record a URL that was queued but not visited (time/page limits, stop)."""
        self._skip(reason, url, compare_key(url), final=True)

    def admit(self, url: str, depth: int) -> str | None:
        """Return the URL to queue (cleaned, tracking stripped) or None (reason recorded)."""
        if not url or url.startswith(("javascript:", "mailto:", "tel:", "data:", "blob:", "about:")):
            return None
        u = strip_tracking(clean(url))
        key = compare_key(u)
        if key in self.seen:
            return None
        if not self.in_scope(u):
            self._skip("범위 밖(다른 사이트)", u, key)
            return None
        if SKIP_EXT.search(urlsplit(u).path):
            self._skip("파일 다운로드 링크", u, key)
            return None
        if unsafe_reason(u):
            self._skip("상태 변경 위험 링크", u, key)
            return None
        if low_value_reason(u):
            self._skip("검사 가치 낮은 링크", u, key)
            return None
        if depth > self.max_depth:
            self._skip("깊이 제한", u, key, final=False)
            return None
        pat = self.pattern(u)
        if self.pattern_count.get(pat, 0) >= self.per_pattern:
            self._skip("같은 형태 주소 제한", u, key, final=False)
            return None
        if self.limited.pop(key, None):
            pass                                     # found again within limits
        self.pattern_count[pat] = self.pattern_count.get(pat, 0) + 1
        self.seen.add(key)
        self.admitted += 1
        s = urlsplit(u)
        q = frozenset(parse_qsl(s.query, keep_blank_values=True))
        lst = self.queries.setdefault(site_of(s.netloc) + s.path, [])
        for other, okey in lst:
            if q < other:                            # plain URL arrived after its variant
                self.superseded.add(okey)
        if len(lst) < 500:
            lst.append((q, key))
        return u

    @staticmethod
    def _same_item(a: frozenset, b: frozenset) -> bool:
        """Queries that agree on every shared parameter and differ only by extra ones:
        view?id=3&t=cv vs view?id=3&page=1. Different values on a shared key (id=3 vs
        id=4) mean different pages."""
        da, db = dict(a), dict(b)
        common = set(da) & set(db)
        extra = set(da) ^ set(db)
        if any(k.lower() in PAGE_KEYS and (da.get(k) or db.get(k) or "1") not in ("1", "0") for k in extra):
            return False                             # another page of a list is new content
        return a != b and bool(common) and all(da[k] == db[k] for k in common)

    def near_duplicate(self, url: str) -> bool:
        """Probably the same page as an earlier URL under another address. Still a separate
        page by the official rule, so it is only deprioritised (visited after distinct pages),
        never dropped. A URL with fewer parameters than the earlier one is preferred instead."""
        s = urlsplit(url)
        q = frozenset(parse_qsl(s.query, keep_blank_values=True))
        return any(self._same_item(q, other) and not q < other
                   for other, _ in self.queries.get(site_of(s.netloc) + s.path, []) if other != q)

    def is_superseded(self, url: str) -> bool:
        return compare_key(url) in self.superseded

    @staticmethod
    def is_pagination(parent: str, link: str) -> bool:
        """Same list, different page number: /list?page=1 -> /list?page=2."""
        a, b = urlsplit(parent), urlsplit(link)
        if (site_of(a.netloc), a.path) != (site_of(b.netloc), b.path):
            return False
        qa = dict(parse_qsl(a.query, keep_blank_values=True))
        qb = dict(parse_qsl(b.query, keep_blank_values=True))
        diff = {k for k in set(qa) | set(qb) if qa.get(k) != qb.get(k)}
        return bool(diff) and all(k.lower() in PAGE_KEYS for k in diff)

    @staticmethod
    def path_page_key(parent: str, link: str) -> str | None:
        """Path-style paging (list-2.html, /page/3/, list.html -> list_2.html): same site,
        same query, paths that differ only by a page number in one segment. Returns the
        list's key, or None."""
        a, b = urlsplit(parent), urlsplit(link)
        if site_of(a.netloc) != site_of(b.netloc) or a.path == b.path:
            return None
        if sorted(parse_qsl(a.query, keep_blank_values=True)) != sorted(parse_qsl(b.query, keep_blank_values=True)):
            return None
        sa, sb = a.path.split("/"), b.path.split("/")
        if len(sa) != len(sb):
            return None
        diff = [k for k, (x, y) in enumerate(zip(sa, sb)) if x != y]
        if len(diff) != 1:
            return None
        x, y = sa[diff[0]], sb[diff[0]]
        if not (_PAGE_NUM.search(x) or _PAGE_NUM.search(y)):
            return None
        strip = lambda t: _PAGE_NUM.sub("", t)  # noqa: E731
        if strip(x) != strip(y):
            return None
        return site_of(b.netloc) + "/".join(sa[:diff[0]] + [strip(y)] + sa[diff[0] + 1:])

    def child_depth(self, parent: str, link: str, depth: int, pager: bool = False) -> int:
        """Moving to another page of the same list is lateral, not one level deeper:
        query paging (?page=2) always; path paging (list-2.html) only when the link sits in
        a paging control. Path-paged lists get at most `per_pattern` lateral pages, so an
        endless numbered chain still runs into the depth limit."""
        if self.is_pagination(parent, link):
            return depth
        if pager:
            key = self.path_page_key(parent, link)
            if key:
                seen = self.lateral.setdefault(key, set())
                ck = compare_key(strip_tracking(clean(link)))
                if ck in seen or len(seen) < self.per_pattern:
                    seen.add(ck)
                    return depth
        return depth + 1

    def priority(self, url: str, depth: int, area: str = "other") -> float:
        """Lower is visited first."""
        s = urlsplit(url)
        pr = depth * 100.0
        pr += {"main": 0, "other": 15, "nav": 30}.get(area, 15)
        segs = [x for x in s.path.split("/") if x]
        shared = 0
        for a, b in zip(segs, self._entry_segs):
            if a != b:
                break
            shared += 1
        pr -= min(shared, 3) * 4
        for k, v in parse_qsl(s.query, keep_blank_values=True):
            if k.lower() in PAGE_KEYS and v.isdigit():
                pr += min(int(v), 20) * 2            # later list pages after earlier ones
        if self.near_duplicate(url):
            pr += 150                                # after distinct pages, even one level deeper
        pr += min(len(url), 300) / 300.0             # tie-break: shorter first
        return pr
