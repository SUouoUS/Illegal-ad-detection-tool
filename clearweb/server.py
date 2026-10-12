"""Local screen server (127.0.0.1 only). One scan at a time."""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import VERSION
from .paths import resource_dir, result_dir, runs_dir
from . import manage, ocr
from .scanner import Scan, ScanConfig

mimetypes.add_type("font/woff2", ".woff2")
WEB = resource_dir() / "web"
app = FastAPI(title="ClearWeb", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

_lock = threading.Lock()
_current: dict = {"scan": None, "thread": None}


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Block cross-site requests from other pages opened in the same browser.
    if request.method != "GET":
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/info")
def info():
    return {"version": VERSION, "result_dir": str(result_dir())}


@app.post("/api/scans")
async def start(request: Request):
    body = await request.json()
    prev = None
    if body.get("resume"):
        # continue a scan that ended with unvisited links, from where it stopped
        rid = str(body["resume"])
        f = runs_dir() / rid / "scan.json"
        if not rid.replace("-", "").isdigit() or not f.is_file():
            raise HTTPException(404, "이어서 검사할 기록이 없습니다.")
        prev = json.loads(f.read_text(encoding="utf-8"))
        if not (prev.get("resume") or {}).get("pending"):
            raise HTTPException(400, "이 검사에는 남은 링크가 없습니다.")
        url = prev["entry_url"]
    else:
        url = str(body.get("url", "")).strip()
    s = urlsplit(url)
    if s.scheme not in ("http", "https") or not s.netloc:
        raise HTTPException(400, "http:// 또는 https:// 로 시작하는 주소를 입력하세요.")
    with _lock:
        sc = _current["scan"]
        if sc is not None and sc.progress.state == "running":
            raise HTTPException(409, "이미 검사가 진행 중입니다.")
        sc = Scan(url, _config(body), resume=prev)
        th = threading.Thread(target=_run, args=(sc,), daemon=True)
        _current.update(scan=sc, thread=th)
        th.start()
    return {"run_id": sc.run_id}


def _config(body: dict) -> ScanConfig:
    """Optional scan settings from the screen, clamped to safe ranges."""
    cfg = ScanConfig()
    opts = body.get("options") or {}

    def num(key, lo, hi, cast=int):
        try:
            v = cast(opts[key])
        except (KeyError, TypeError, ValueError):
            return None
        return max(lo, min(hi, v))

    if (v := num("max_depth", 0, 10)) is not None:
        cfg.max_depth = v
    if (v := num("max_pages", 1, 2000)) is not None:
        cfg.max_pages = v
    # the contest measures up to 30 minutes; longer limits are for practical use
    if (v := num("budget_min", 1, 180, float)) is not None:
        cfg.total_budget_s = v * 60
    cfg.ocr_enabled = bool(opts.get("ocr"))
    # pages at a time: "auto" (default) or a fixed number
    if (v := num("concurrency", 1, 16)) is not None:
        cfg.concurrency, cfg.concurrency_auto = v, False
    return cfg


@app.get("/api/defaults")
def defaults():
    c = ScanConfig()
    ok, msg = ocr.engine_status()
    from . import concurrency as conc
    return {"max_depth": c.max_depth, "max_pages": c.max_pages, "budget_min": round(c.total_budget_s / 60, 1),
            "concurrency_plan": conc.plan(free_mb=conc.free_memory_mb()),
            "ocr": c.ocr_enabled, "ocr_available": ok, "ocr_message": msg}


@app.get("/api/scans/{run_id}/images/{name}")
def run_image(run_id: str, name: str):
    """Preview saved at scan time. The original image URL is never requested again."""
    if not run_id.replace("-", "").isdigit() or not re.fullmatch(r"[0-9a-f]{40}\.jpg", name):
        raise HTTPException(404)
    f = runs_dir() / run_id / "images" / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


def _run(sc: Scan):
    try:
        sc.run()
    except Exception as e:  # noqa: BLE001 - surfaced through status
        sc.progress.state = "failed"
        sc.progress.end_reason = f"결과 파일 작성 실패: {e}"


@app.post("/api/scans/stop")
def stop():
    sc = _current["scan"]
    if sc is None or sc.progress.state != "running":
        raise HTTPException(409, "진행 중인 검사가 없습니다.")
    sc.stop()
    return {"ok": True}


@app.get("/api/status")
def status():
    sc = _current["scan"]
    if sc is None:
        return {"state": "idle"}
    p = asdict(sc.progress)
    if p["state"] == "running" and sc._t0:
        p["elapsed_sec"] = round(time.monotonic() - sc._t0, 1)
    p["stop_requested"] = sc.cancel.is_set()
    return p


@app.get("/api/scans")
def list_runs():
    out = []
    for d in sorted(runs_dir().iterdir(), reverse=True)[:30]:
        f = d / "scan.json"
        if f.is_file():
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                rv = manage.load_reviews(d)
                out.append({k: r.get(k) for k in ("run_id", "entry_url", "state", "end_reason", "started_at", "elapsed_sec",
                                                  "tool_version")}
                           | {"findings": len(r.get("result", {}).get("findings", [])),
                              "items": len(manage.rows(r, rv)),
                              "reviewed": sum(1 for v in rv.values() if v.get("status") != "unreviewed")})
            except (OSError, ValueError):
                continue
    return out


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"\d{8}-\d{6}", run_id):
        raise HTTPException(404)
    d = runs_dir() / run_id
    if not (d / "scan.json").is_file():
        raise HTTPException(404, "검사 기록이 없습니다.")
    return d


def _record(d: Path) -> dict:
    return json.loads((d / "scan.json").read_text(encoding="utf-8"))


@app.put("/api/scans/{run_id}/reviews")
async def put_review(run_id: str, request: Request):
    """The inspector's decision on one item. Kept apart: result files are never changed."""
    d = _run_dir(run_id)
    body = await request.json()
    try:
        reviews = manage.save_review(d, str(body.get("key", ""))[:4000], str(body.get("status", "")),
                                     str(body.get("memo", "")))
    except ValueError:
        raise HTTPException(400, "검토 상태가 올바르지 않습니다.")
    return reviews


@app.get("/api/scans/{run_id}/export.csv")
def export_csv(run_id: str):
    d = _run_dir(run_id)
    rec = _record(d)
    prev = manage.find_previous(runs_dir(), rec)
    return Response(manage.to_csv(rec, manage.load_reviews(d), manage.compare(rec, prev) if prev else None),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="clearweb-{run_id}.csv"'})


@app.get("/api/scans/{run_id}/report")
def report(run_id: str):
    from .scanner import page_summary
    d = _run_dir(run_id)
    rec = _record(d)
    prev = manage.find_previous(runs_dir(), rec)
    return HTMLResponse(manage.report_html(rec, manage.load_reviews(d), page_summary(rec.get("pages", [])),
                                           manage.compare(rec, prev) if prev else None))


@app.get("/api/scans/{run_id}")
def get_run(run_id: str):
    if not run_id.replace("-", "").isdigit():
        raise HTTPException(404)
    f = runs_dir() / run_id / "scan.json"
    if not f.is_file():
        raise HTTPException(404, "검사 기록이 없습니다.")
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["reviews"] = manage.load_reviews(f.parent)
    prev = manage.find_previous(runs_dir(), rec)
    rec["compare"] = manage.compare(rec, prev) if prev else None
    # failure summary recomputed with the current categories (also for older records)
    from .scanner import page_summary
    rec["page_summary"] = page_summary(rec.get("pages", []))
    return JSONResponse(rec)


def _free_port(start: int, tries: int = 20) -> int:
    """First port from `start` that nothing on 127.0.0.1 is listening on."""
    import socket
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit(f"사용할 수 있는 포트가 없습니다({start}~{start + tries - 1}).")


def serve(port: int = 8765, open_browser: bool = True):
    port = _free_port(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"ClearWeb {VERSION} - 화면 주소: {url}")
    print(f"결과 파일 위치: {Path(result_dir()) / 'result.json'}")
    print("이 창을 닫으면 프로그램이 종료됩니다.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
