"""The worked example of the official rules (모집요강 붙임4) as a local site: the four findings
must come out with the same url + technique and a location that points at the example's
element, and both result files must follow the schema."""
import json

import pytest

pytest.importorskip("playwright.async_api")
from clearweb import result_writer, scanner  # noqa: E402
from testsites.server import start  # noqa: E402


def test_official_example_findings(tmp_path, monkeypatch):
    srv = start("spec", 0)
    base = f"http://127.0.0.1:{srv.server_port}/"
    monkeypatch.setattr(scanner, "runs_dir", lambda: tmp_path / "runs")
    try:
        scanner.Scan(base + "board/list", scanner.ScanConfig(), tmp_path).run()
    finally:
        srv.shutdown()
    raw = (tmp_path / "result.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")                       # UTF-8 without BOM
    res = json.loads(raw.decode("utf-8"))
    assert result_writer.validate(res) == []
    assert res["meta"]["topic"] == "TOPIC" and res["meta"]["entry_url"] == base + "board/list"
    got = {(f["url"].replace(base, "/"), f["technique"], f["location"]) for f in res["findings"]}
    assert got == {
        ("/board/view?id=1024", "JAMO", "div.notice-board > span.visually-hidden"),
        ("/board/view?id=1024", "HOMOGLYPH", "table.list > tbody > tr:nth-of-type(7) > td.title > a"),
        ("/board/view?id=1180", "TRANSPARENT", "p.desc > span"),
        ("/board/view?id=1180", "OFFSCREEN", f'iframe[src="{base}widget/notice"] >>> div.wrap > span.hidden-link'),
    }
    assert all(f["is_violation"] is True and f["evidence_text"] for f in res["findings"])
    assert len({f["id"] for f in res["findings"]}) == 4
    extra = json.loads((tmp_path / "result_extra.json").read_text(encoding="utf-8"))
    assert result_writer.validate_extra(extra) == [] and extra["findings"] == []
