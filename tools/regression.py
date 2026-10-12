"""Local regression check: scan every local test site, score it, and compare two runs.

  python tools/regression.py run <out_dir> [--code <repo dir>]   # scan + score all sites
  python tools/regression.py compare <before_dir> <after_dir>    # findings, extra ads, images, result_extra

The six local sites (and the official worked example) must already be served:
  python -m testsites.server --site dev --port 8811   (crawl 8821, fp 8831, ocr 8841, eval 8851,
  eval_ocr 8861, spec 8871)
`--code` runs the scanner from another checkout (e.g. a git worktree of an older commit) to make
a baseline. Results reused from data that was used for fixes are regression checks, not new
evaluation results.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITES = [("dev", 8811, False), ("crawl", 8821, False), ("fp", 8831, False), ("eval", 8851, False),
         ("spec", 8871, False), ("ocr", 8841, True), ("eval_ocr", 8861, True)]


def run(out: Path, code: Path):
    py = str(ROOT / ".venv" / "Scripts" / "python.exe")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(code))
    for site, port, ocr in SITES:
        d = out / site
        d.mkdir(parents=True, exist_ok=True)
        runs = d / "runs"
        script = ("import sys; from pathlib import Path; from clearweb import scanner; "
                  f"scanner.runs_dir = lambda: Path(r'{runs}'); "
                  f"cfg = scanner.ScanConfig(ocr_enabled={ocr}); "
                  f"r = scanner.Scan('http://127.0.0.1:{port}/', cfg, Path(r'{d}')).run(); "
                  f"import json; json.dump(r, open(r'{d / 'scan.json'}', 'w', encoding='utf-8'), ensure_ascii=False)")
        subprocess.run([py, "-c", script], cwd=str(code), env=env, capture_output=True, timeout=900)
        if ocr:
            sc = subprocess.run([py, str(ROOT / "tools" / "score_ocr.py"), str(d / "scan.json"),
                                 "--expected", str(ROOT / "testsites" / site / "expected.json")],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
            line = [x for x in sc.stdout.splitlines() if x.startswith("extraction")]
        else:
            sc = subprocess.run([py, str(ROOT / "tools" / "score.py"), "--answers", str(ROOT / "testsites" / site / "answers.json"),
                                 "--result", str(d / "result.json"), "--base", f"http://127.0.0.1:{port}/"],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
            line = [x for x in sc.stdout.splitlines() if x.startswith("precision")]
        (d / "score.txt").write_text(sc.stdout, encoding="utf-8")
        print(f"{site:9s} {line[0] if line else 'NO SCORE (is the site served?)'}")


def _load(d: Path, site: str):
    rec = json.loads((d / site / "scan.json").read_text(encoding="utf-8"))
    xf = d / site / "result_extra.json"
    extra = json.loads(xf.read_text(encoding="utf-8")) if xf.is_file() else {"findings": []}
    return rec, extra


def compare(a: Path, b: Path) -> int:
    bad = 0
    for site, _, _ in SITES:
        if not (a / site / "scan.json").is_file() or not (b / site / "scan.json").is_file():
            print(f"{site:9s} missing")
            continue
        (ra, xa), (rb, xb) = _load(a, site), _load(b, site)
        K = lambda r: {(f["url"], f["location"], f["technique"]) for f in r["result"]["findings"]}  # noqa: E731
        E = lambda r: {(x["url"], x["location"]) for x in r.get("extra_ads", [])}  # noqa: E731
        X = lambda r: {x.get("text", "")[:40] for x in r.get("excluded", [])}  # noqa: E731
        I = lambda r: {(i["page_url"], i.get("location"), i.get("level"), i["status"]) for i in r.get("images", [])}  # noqa: E731
        IX = lambda x: {(f["url"], f["location"], f["extra_finding"]) for f in x["findings"] if "IMAGE" in f["extra_finding"]}  # noqa: E731
        parts = {"result.json": K(ra) == K(rb), "extra_ads": E(ra) == E(rb), "excluded": X(ra) == X(rb),
                 "images": I(ra) == I(rb), "result_extra(images)": IX(xa) == IX(xb)}
        diff = [k for k, ok in parts.items() if not ok]
        bad += bool(diff)
        print(f"{site:9s} {'same' if not diff else 'DIFF: ' + ', '.join(diff)} | official {len(K(rb))} · extra ads {len(E(rb))} "
              f"· result_extra {len(xb['findings'])} · pages {len(ra['pages'])}->{len(rb['pages'])}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("out", type=Path)
    r.add_argument("--code", type=Path, default=ROOT)
    c = sub.add_parser("compare")
    c.add_argument("before", type=Path)
    c.add_argument("after", type=Path)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.out, a.code.resolve())
    else:
        sys.exit(1 if compare(a.before, a.after) else 0)


if __name__ == "__main__":
    main()
