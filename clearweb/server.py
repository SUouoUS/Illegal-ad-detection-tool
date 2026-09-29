"""Local screen server (127.0.0.1 only). One scan at a time."""
from __future__ import annotations

import json
import mimetypes
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import VERSION
from .paths import resource_dir, result_dir, runs_dir
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
    url = str(body.get("url", "")).strip()
    s = urlsplit(url)
    if s.scheme not in ("http", "https") or not s.netloc:
        raise HTTPException(400, "http:// 또는 https:// 로 시작하는 주소를 입력하세요.")
    with _lock:
        sc = _current["scan"]
        if sc is not None and sc.progress.state == "running":
            raise HTTPException(409, "이미 검사가 진행 중입니다.")
        sc = Scan(url, _config(body))
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
    if (v := num("budget_min", 1, 29, float)) is not None:
        cfg.total_budget_s = v * 60
    return cfg


@app.get("/api/defaults")
def defaults():
    c = ScanConfig()
    return {"max_depth": c.max_depth, "max_pages": c.max_pages, "budget_min": round(c.total_budget_s / 60, 1)}


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
                out.append({k: r.get(k) for k in ("run_id", "entry_url", "state", "end_reason", "started_at", "elapsed_sec")}
                           | {"findings": len(r.get("result", {}).get("findings", []))})
            except (OSError, ValueError):
                continue
    return out


@app.get("/api/scans/{run_id}")
def get_run(run_id: str):
    if not run_id.replace("-", "").isdigit():
        raise HTTPException(404)
    f = runs_dir() / run_id / "scan.json"
    if not f.is_file():
        raise HTTPException(404, "검사 기록이 없습니다.")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


def serve(port: int = 8765, open_browser: bool = True):
    url = f"http://127.0.0.1:{port}/"
    print(f"ClearWeb {VERSION} - 화면 주소: {url}")
    print(f"결과 파일 위치: {Path(result_dir()) / 'result.json'}")
    print("이 창을 닫으면 프로그램이 종료됩니다.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
