"""Write result.json exactly as the official schema requires.

UTF-8 without BOM, top-level {meta, findings}, findings always an array,
one object per (url + location + technique), ids unique.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import VERSION

TOPIC = "TOPIC"
TECHNIQUES = {"HOMOGLYPH", "JAMO", "TRANSPARENT", "OFFSCREEN"}
FINDING_KEYS = ("id", "url", "is_violation", "location", "evidence_text", "technique")


def iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def build(entry_url: str, started: datetime, finished: datetime, findings: list[dict],
          elapsed: float | None = None) -> dict:
    seen = set()
    out = []
    for f in findings:
        key = (f["url"], f["location"], f["technique"])
        if key in seen or f["technique"] not in TECHNIQUES:
            continue
        seen.add(key)
        out.append({
            "id": f"f_{len(out) + 1:03d}",
            "url": f["url"],
            "is_violation": True,
            "location": f["location"],
            "evidence_text": f.get("evidence_text", ""),
            "technique": f["technique"],
        })
    if elapsed is None:
        elapsed = (finished - started).total_seconds()
    return {
        "meta": {
            "topic": TOPIC,
            "entry_url": entry_url,
            "started_at": iso(started),
            "finished_at": iso(finished),
            "elapsed_sec": round(elapsed, 2),
            "tool_version": VERSION,
        },
        "findings": out,
    }


def validate(doc: dict) -> list[str]:
    """Return a list of schema problems (empty when valid)."""
    errs = []
    if set(doc) != {"meta", "findings"}:
        errs.append(f"top-level keys must be meta, findings: {sorted(doc)}")
    m = doc.get("meta", {})
    for k, t in (("topic", str), ("entry_url", str), ("started_at", str), ("finished_at", str)):
        if not isinstance(m.get(k), t):
            errs.append(f"meta.{k} missing or not {t.__name__}")
    if not isinstance(m.get("elapsed_sec"), (int, float)) or isinstance(m.get("elapsed_sec"), bool):
        errs.append("meta.elapsed_sec missing or not number")
    for k in ("started_at", "finished_at"):
        try:
            datetime.fromisoformat(m.get(k, ""))
        except (TypeError, ValueError):
            errs.append(f"meta.{k} not ISO 8601")
    fs = doc.get("findings")
    if not isinstance(fs, list):
        errs.append("findings must be an array")
        return errs
    ids, keys = set(), set()
    for n, f in enumerate(fs):
        for k in FINDING_KEYS:
            if k not in f:
                errs.append(f"findings[{n}].{k} missing")
        if f.get("technique") not in TECHNIQUES:
            errs.append(f"findings[{n}].technique invalid: {f.get('technique')}")
        if not isinstance(f.get("is_violation"), bool):
            errs.append(f"findings[{n}].is_violation not boolean")
        for k in ("id", "url", "location", "evidence_text"):
            if k in f and not isinstance(f[k], str):
                errs.append(f"findings[{n}].{k} not string")
        if f.get("id") in ids:
            errs.append(f"findings[{n}].id duplicated")
        ids.add(f.get("id"))
        key = (f.get("url"), f.get("location"), f.get("technique"))
        if key in keys:
            errs.append(f"findings[{n}] duplicate url+location+technique")
        keys.add(key)
    return errs


def write(doc: dict, path: Path) -> Path:
    errs = validate(doc)
    if errs:
        raise ValueError("result schema invalid: " + "; ".join(errs))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")  # no BOM
    fd, tmp = tempfile.mkstemp(prefix=".result_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # read back to confirm the file parses and has no BOM
    back = path.read_bytes()
    if back.startswith(b"\xef\xbb\xbf"):
        raise ValueError("BOM written")
    json.loads(back.decode("utf-8"))
    return path
