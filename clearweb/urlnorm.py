"""URL handling following the official comparison rules.

Only the fragment is dropped. Every query parameter is kept (values distinguish
pages); parameters are sorted so that order does not matter.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


def clean(url: str) -> str:
    """Absolute URL without fragment, host lower-cased, query sorted."""
    s = urlsplit(url)
    q = urlencode(sorted(parse_qsl(s.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path or "/", q, ""))


def compare_key(url: str) -> str:
    """Key used to decide 'same page': ignores scheme, trailing slash and fragment."""
    s = urlsplit(url)
    q = urlencode(sorted(parse_qsl(s.query, keep_blank_values=True)), doseq=True)
    path = (s.path or "/").rstrip("/") or "/"
    return f"{s.netloc.lower()}{path}" + (f"?{q}" if q else "")


def absolutize(base: str, href: str) -> str:
    return urljoin(base, href)
