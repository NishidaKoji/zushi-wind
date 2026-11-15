#!/usr/bin/env python3
"""Independent, clearly labelled reference wind for Zushi Marina Watch shortcut.

Riviera Zushi Marina's *own* wind observations remain separate, in wind.json.
This file never presents weather at another location as the marina anemometer.
Source 1: JMA Tsujido AMeDAS (46141), measured 10-minute average.
Source 2: Open-Meteo model for Zushi Marina area, explicitly modelled.
"""
import html
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
NOW = datetime.now(timezone.utc)
JMA_BASE = "https://www.jma.go.jp/bosai/amedas/data"
STATION_ID = "46141"
DIRECTIONS = (
    "静穏", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
    "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西", "北",
)


def get_url(url, *, as_json=True):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ZushiWindReference/1.0 (public weather data)",
                 "Accept": "application/json,text/plain"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read(1_200_000)
    if len(raw) >= 1_200_000:
        raise ValueError("Oversized weather data response")
    return json.loads(raw) if as_json else raw.decode("utf-8").strip()


def validated_age(observed, max_age_minutes):
    age = (NOW - observed.astimezone(timezone.utc)).total_seconds()
    if age < -180 or age > max_age_minutes * 60:
        raise ValueError(f"Stale/future reference weather observation: age={age:.0f}s")


def read_good(pair):
    if not isinstance(pair, list) or len(pair) < 2:
        raise ValueError("Missing weather value")
    value, quality = pair[:2]
    if value is None or isinstance(value, bool) or quality not in (0, 1):
        raise ValueError(f"Missing or invalid-quality observation: {pair}")
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("Non-finite weather value")
    return n


def jma_amedas():
    latest = datetime.fromisoformat(
        get_url(JMA_BASE + "/latest_time.txt", as_json=False)
    ).astimezone(JST)
    validated_age(latest, 30)
    errors = []
    # point files are partitioned in JST into 3-hour intervals.
    for offset in (0, 3, 6):
        local = latest - timedelta(hours=offset)
        part = local.replace(hour=(local.hour // 3) * 3, minute=0, second=0)
        path = f"/point/{STATION_ID}/{part:%Y%m%d_%H}.json"
        try:
            data = get_url(JMA_BASE + path)
            records = sorted(data.items(), reverse=True)
            for key, entry in records:
                if not (isinstance(entry, dict) and "wind" in entry
                        and "windDirection" in entry):
                    continue
                try:
                    observed = datetime.strptime(key, "%Y%m%d%H%M%S").replace(tzinfo=JST)
                    validated_age(observed, 30)
                    speed = read_good(entry["wind"])
                    direction_code = int(read_good(entry["windDirection"]))
                    if speed < 0 or speed > 75 or not 0 <= direction_code <= 16:
                        continue
                    return {
                        "source": "気象庁アメダス（辻堂）",
                        "kind": "measured_other_station",
                        "station": "辻堂",
                        "station_id": STATION_ID,
                        "observed_at": observed.isoformat(),
                        "ts": int(observed.timestamp()),
                        "speed": round(speed, 1),
                        "direction": DIRECTIONS[direction_code],
                        "period": "10分平均",
                        "url": "https://www.jma.go.jp/bosai/amedas/",
                    }
                except (TypeError, ValueError) as err:
                    errors.append(f"Record {key}: {err}")
        except Exception as err:
            errors.append(str(err)[:120])
    raise RuntimeError("辻堂アメダスから新鮮な風速を取得できません: " + " | ".join(errors[-4:]))


def local_model():
    query = urllib.parse.urlencode({
        "latitude": "35.296", "longitude": "139.548",
        "current": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms", "timezone": "Asia/Tokyo",
    })
    data = get_url("https://api.open-meteo.com/v1/forecast?" + query)
    current = data["current"]
    observed = datetime.fromisoformat(current["time"]).replace(tzinfo=JST)
    validated_age(observed, 95)
    speed = float(current["wind_speed_10m"])
    degrees = float(current["wind_direction_10m"])
    if not (math.isfinite(speed) and 0 <= speed <= 75
            and math.isfinite(degrees) and 0 <= degrees <= 360):
        raise ValueError("Model data out of range")
    code = round(degrees / 22.5) % 16
    direction = DIRECTIONS[16 if code == 0 else code]
    return {
        "source": "Open-Meteo（逗子周辺）",
        "kind": "model_estimate",
        "station": "逗子マリーナ周辺（予測モデル）",
        "observed_at": observed.isoformat(),
        "ts": int(observed.timestamp()),
        "speed": round(speed, 1),
        "direction": direction,
        "period": "モデル推定",
        "url": "https://open-meteo.com/",
    }


def publish(data):
    when = datetime.fromtimestamp(data["ts"], JST).strftime("%m/%d %H:%M")
    if data["kind"] == "measured_other_station":
        body = (
            "逗子マリーナ実測 取得不可\n"
            f"【参考・辻堂アメダス】{when}\n"
            f"10分平均 {data['speed']:.1f}m/s {data['direction']}\n"
            "※逗子マリーナとは別地点の実測\n"
            "出典: 気象庁"
        )
    else:
        body = (
            "逗子マリーナ実測 取得不可\n"
            f"【参考・周辺の風予測】{when}\n"
            f"推定風速 {data['speed']:.1f}m/s {data['direction']}\n"
            "※実測ではありません／出艇判断不可\n"
            "出典: Open-Meteo"
        )
    (ROOT / "backup_wind.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "watch.txt").write_text(body + "\n", encoding="utf-8")
    (ROOT / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>風の参考値（逗子マリーナ実測ではありません）</title>"
        "<style>body{font:20px system-ui;white-space:pre-line;"
        "padding:24px;line-height:1.7}</style>" + html.escape(body),
        encoding="utf-8")
    print(body)


def main():
    failures = []
    for name, reader in (("JMA", jma_amedas), ("Open-Meteo", local_model)):
        try:
            publish(reader())
            return 0
        except Exception as err:
            failures.append(f"{name}: {err}")
            print(f"参考風データ取得失敗 {name}: {err}", file=sys.stderr)
    print("全参考風データ源が利用できません: " + " | ".join(failures), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
