#!/usr/bin/env python3
"""Run one sample and publish static files for Apple Shortcuts."""
import html, json, os, shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["ZUSHI_BASE"] = str(Path(".state").resolve())
import zushi_wind_cloud as zushi_wind

DOCS = Path(".")
STATE = Path(os.environ["ZUSHI_BASE"])
STATE.mkdir(exist_ok=True)

old = DOCS / "wind.json"
if old.exists():
    shutil.copy2(old, STATE / "last.json")

data = zushi_wind.sample()
(DOCS / "wind.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

def fmt(value):
    return "?" if value is None else f"{float(value):.1f}"

when = datetime.fromtimestamp(data["ts"], ZoneInfo("Asia/Tokyo")).strftime("%H:%M")
body = (
    f"逗子マリーナ {when}取得\n"
    f"現在 {fmt(data.get('speed'))}m/s {data.get('direction', '?')}\n"
    f"10分平均 {fmt(data.get('avg10'))}m/s\n"
    f"日最大 {fmt(data.get('maxday'))}m/s\n"
    f"1h最大 {fmt(data.get('max1h'))}m/s\n"
    f"10分最大 {fmt(data.get('max10'))}m/s"
)
(DOCS / "watch.txt").write_text(body + "\n", encoding="utf-8")
(DOCS / "index.html").write_text(
    "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
    "<title>逗子マリーナ風速</title><style>body{font:22px system-ui;white-space:pre-line;"
    "padding:24px;line-height:1.7}</style>" + html.escape(body), encoding="utf-8"
)
print(body)
