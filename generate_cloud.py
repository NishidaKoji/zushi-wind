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
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
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


def candidate_video_urls():
    """Find the anemometer video on Riviera's official page."""
    page = "https://www.riviera.co.jp/area/zushi/livecamera/"
    urls = []
    try:
        request = urllib.request.Request(page, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            page_html = response.read().decode("utf-8", "replace")
        # Only trust video links labelled as wind-meter footage, never the camera.
        pattern = r'(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})'
        for match in re.finditer(pattern, page_html):
            nearby = page_html[max(0, match.start() - 600):match.end() + 250]
            if "風向風速計" in nearby or "Anemometer" in nearby:
                url = "https://www.youtube.com/watch?v=" + match.group(1)
                if url not in urls:
                    urls.append(url)
    except Exception as exc:
        print(f"公式ページの確認失敗: {exc}", file=sys.stderr)
    if not urls:
        raise RuntimeError("公式ページで風向風速計の配信を特定できません")
    return urls


def candidate_stream_urls(deno):
    """Try alternative YouTube formats and providers; validate by decoding a frame."""
    cookie_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    common = [
        "yt-dlp", "--js-runtimes", f"deno:{deno}",
        "--remote-components", "ejs:github",
        "--no-playlist", "--socket-timeout", "20",
        "-g",
    ]
    if cookie_file and Path(cookie_file).is_file():
        common.extend(["--cookies", cookie_file])
    # Live HLS (often itag 93) can give an unplayable manifest even when
    # yt-dlp succeeds. Prefer trying a different client/format next.
    strategies = [
        ("youtube:player_client=default,mweb", "best*[height<=720]/best*"),
        ("youtube:player_client=tv", "bestvideo[protocol^=https][height<=720]/bestvideo[height<=720]/best*"),
        ("youtube:player_client=default,android;formats=missing_pot", "bestvideo[height<=720]/best*[height<=720]/best*"),
        ("youtube:player_client=tv_downgraded", "best*[height<=480]/best*"),
    ]
    deadline = time.monotonic() + 180
    seen = set()
    candidates = candidate_video_urls()
    for candidate in candidates:
        for extractor_args, fmt in strategies:
            if time.monotonic() >= deadline:
                return
            try:
                out = run_text(common + [
                    "--extractor-args", extractor_args, "-f", fmt, candidate
                ], min(40, max(8, int(deadline - time.monotonic()))))
                urls = [line.strip() for line in out.splitlines()
                        if line.strip().startswith(("https://", "http://"))]
                for url in urls:
                    if url not in seen:
                        seen.add(url)
                        yield candidate, url
            except Exception as exc:
                print(f"YouTube別形式の取得失敗: {str(exc)[-180:]}", file=sys.stderr)
    # Only for this video's ID found on Riviera's official page.
    for base in (
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.leptons.xyz",
        "https://pipedapi.nosebs.ru",
        "https://pipedapi.adminforge.de",
        "https://api.piped.yt",
        "https://pipedapi.drgns.space",
        "https://pipedapi.owo.si",
        "https://pipedapi.ducks.party",
    ):
        if time.monotonic() >= deadline:
            return
        try:
            request = urllib.request.Request(
                f"{base}/streams/{candidates[0].split('v=')[-1]}",
                headers={"User-Agent": "zushi-wind/2"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                info = json.load(response)
            urls = []
            if info.get("hls"):
                urls.append(info["hls"])
            urls.extend(item["url"] for item in sorted(
                (item for item in info.get("videoStreams", []) if item.get("url")),
                key=lambda item: item.get("height") or 0, reverse=True)[:2])
            for url in urls:
                if url not in seen:
                    seen.add(url)
                    yield base, url
        except Exception as exc:
            print(f"代替配信先の取得失敗: {base}: {str(exc)[-120:]}", file=sys.stderr)


def grab_frame_with_deno():
    deno = TOOLS / "deno"
    temporary = zushi_wind.FRAME.with_suffix(".new.jpg")
    failures = []
    for source, stream_url in candidate_stream_urls(deno):
        try:
            print(f"風速映像の取得を試行: {source}", file=sys.stderr)
            zushi_wind.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-rw_timeout", "12000000",
                    "-i", stream_url,
                    "-vf", "scale=640:360:flags=lanczos",
                    "-frames:v", "1", "-q:v", "2",
                    str(temporary),
                ], 25,
            )
            if not temporary.exists() or temporary.stat().st_size < 5000:
                raise RuntimeError("取得画像が不完全です")
            temporary.replace(zushi_wind.FRAME)
            print(f"風速映像の取得成功: {source}", file=sys.stderr)
            return
        except Exception as exc:
            failures.append(str(exc)[-180:])
            temporary.unlink(missing_ok=True)
            print(f"この映像形式は取得不可。別の形式を試行: {str(exc)[-150:]}", file=sys.stderr)
    raise RuntimeError(
        "風速ライブ映像のどの候補からも画像を取得できません: " +
        " / ".join(failures[-4:])
    )


zushi_wind.grab_frame = grab_frame_with_deno
STATE.mkdir(exist_ok=True)
data = zushi_wind.sample()
age = (datetime.now(timezone.utc) - datetime.fromtimestamp(int(data["ts"]), timezone.utc)).total_seconds()
if not (0 <= age <= 900):
    raise RuntimeError(f"観測データの時刻が不正または15分以上古いです: age={age:.0f}秒")
(ROOT / "wind.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)


def fmt(value):
    return "?" if value is None else f"{float(value):.1f}"


when = datetime.fromtimestamp(data["ts"], ZoneInfo("Asia/Tokyo")).strftime("%m/%d %H:%M")
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
