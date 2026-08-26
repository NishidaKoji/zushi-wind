#!/usr/bin/env python3
"""Run one cloud sample and publish files for the Apple Watch shortcut.

This version bootstraps yt-dlp's EJS component and Deno inside the GitHub
Actions runner, then tries several anonymous YouTube clients before falling
back to public Piped instances.
"""
import html
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / ".tools"
STATE = ROOT / ".state"
os.environ["ZUSHI_BASE"] = str(STATE)


def run_text(command, timeout=120):
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed")[-3000:]
        raise RuntimeError(detail)
    return result.stdout.strip()


def ensure_yt_dlp_and_deno():
    # The [default] extra includes yt-dlp-ejs, which plain `pip install yt-dlp`
    # does not necessarily install.
    run_text(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            "-U",
            "yt-dlp[default]",
        ],
        180,
    )

    deno = TOOLS / "deno"
    if not deno.exists():
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            asset = "deno-x86_64-unknown-linux-gnu.zip"
        elif machine in {"aarch64", "arm64"}:
            asset = "deno-aarch64-unknown-linux-gnu.zip"
        else:
            raise RuntimeError(f"未対応CPU: {machine}")
        TOOLS.mkdir(exist_ok=True)
        archive = TOOLS / "deno.zip"
        url = f"https://github.com/denoland/deno/releases/latest/download/{asset}"
        request = urllib.request.Request(url, headers={"User-Agent": "zushi-wind/2"})
        with urllib.request.urlopen(request, timeout=90) as response:
            archive.write_bytes(response.read())
        with zipfile.ZipFile(archive) as bundle:
            bundle.extract("deno", TOOLS)
        archive.unlink(missing_ok=True)
        deno.chmod(0o755)
    os.environ["PATH"] = str(TOOLS) + os.pathsep + os.environ.get("PATH", "")
    return deno


ensure_yt_dlp_and_deno()
import zushi_wind_cloud as zushi_wind


def resolve_youtube_stream(deno):
    common = [
        "yt-dlp",
        "--js-runtimes",
        f"deno:{deno}",
        "--remote-components",
        "ejs:github",
        "--no-playlist",
        "--socket-timeout",
        "30",
        "-g",
        "-f",
        "best*[height<=720]/best*",
    ]
    attempts = [
        "youtube:player_client=default,android;formats=missing_pot",
        "youtube:player_client=tv",
        "youtube:player_client=tv_downgraded",
    ]
    errors = []
    for extractor_args in attempts:
        try:
            output = run_text(
                common
                + ["--extractor-args", extractor_args, zushi_wind.VIDEO_URL],
                90,
            )
            urls = [line.strip() for line in output.splitlines() if line.startswith("http")]
            if urls:
                return urls[0]
            errors.append(f"{extractor_args}: URLなし")
        except Exception as exc:
            errors.append(f"{extractor_args}: {exc}")

    # Anonymous mirror fallback. No account cookies or credentials are used.
    # Current public instances listed by the Piped project.  Instances come
    # and go, so try the whole list instead of depending on three hosts.
    for base in (
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.leptons.xyz",
        "https://pipedapi.nosebs.ru",
        "https://pipedapi-libre.kavin.rocks",
        "https://piped-api.privacy.com.de",
        "https://pipedapi.adminforge.de",
        "https://api.piped.yt",
        "https://pipedapi.drgns.space",
        "https://pipedapi.owo.si",
        "https://pipedapi.ducks.party",
        "https://piped-api.codespace.cz",
        "https://pipedapi.reallyaweso.me",
        "https://api.piped.private.coffee",
        "https://pipedapi.darkness.services",
        "https://pipedapi.orangenet.cc",
    ):
        try:
            request = urllib.request.Request(
                f"{base}/streams/41UP1WsRBKw",
                headers={"User-Agent": "zushi-wind/2"},
            )
            with urllib.request.urlopen(request, timeout=18) as response:
                info = json.load(response)
            if info.get("hls"):
                return info["hls"]
            streams = [item for item in info.get("videoStreams", []) if item.get("url")]
            if streams:
                return max(streams, key=lambda item: item.get("height") or 0)["url"]
            errors.append(f"{base}: URLなし")
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    raise RuntimeError("ライブ映像URLを取得できません:\n" + "\n".join(errors))


def grab_frame_with_deno():
    deno = TOOLS / "deno"
    stream_url = resolve_youtube_stream(deno)
    temporary = zushi_wind.FRAME.with_suffix(".new.jpg")
    zushi_wind.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            stream_url,
            "-vf",
            "scale=640:360:flags=lanczos",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(temporary),
        ],
        75,
    )
    if not temporary.exists() or temporary.stat().st_size < 5000:
        raise RuntimeError("取得画像が不完全です")
    temporary.replace(zushi_wind.FRAME)


zushi_wind.grab_frame = grab_frame_with_deno
STATE.mkdir(exist_ok=True)
old = ROOT / "wind.json"
if old.exists():
    shutil.copy2(old, STATE / "last.json")

data = zushi_wind.sample()
(ROOT / "wind.json").write_text(
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
(ROOT / "watch.txt").write_text(body + "\n", encoding="utf-8")
(ROOT / "index.html").write_text(
    "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
    "<title>逗子マリーナ風速</title><style>body{font:22px system-ui;white-space:pre-line;"
    "padding:24px;line-height:1.7}</style>"
    + html.escape(body),
    encoding="utf-8",
)
print(body)
