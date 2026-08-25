#!/usr/bin/env python3
"""Read the Riviera Zushi Marina YouTube wind display."""
import json, math, os, re, subprocess, tempfile, time
from collections import Counter
from pathlib import Path

VIDEO_URL = "https://www.youtube.com/watch?v=41UP1WsRBKw"
BASE = Path(os.environ.get("ZUSHI_BASE", str(Path.home() / ".zushi_wind")))
FRAME, LAST = BASE / "frame.jpg", BASE / "last.json"

# width, height, x, y after normalizing the source to 640x360
CROPS = {
    "speed":  (72, 26, 150, 187),
    "avg10":  (62, 22, 453, 66),
    "maxday": (62, 22, 497, 151),
    "max1h":  (62, 22, 497, 187),
    "max10":  (62, 22, 497, 224),
}
DIRMAP = {"N":"北","NNE":"北北東","NE":"北東","ENE":"東北東","E":"東","ESE":"東南東","SE":"南東","SSE":"南南東","S":"南","SSW":"南南西","SW":"南西","WSW":"西南西","W":"西","WNW":"西北西","NW":"北西","NNW":"北北西"}
DIRS = list(DIRMAP)

def run(cmd, timeout=45, check=True):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if check and p.returncode:
        msg = (p.stderr or p.stdout or b"command failed")[-1200:]
        raise RuntimeError(msg.decode("utf-8", "replace"))
    return p

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def load_last():
    try:
        return json.loads(LAST.read_text(encoding="utf-8"))
    except Exception:
        return {}

def grab_frame():
    out = run(["yt-dlp", "-g", "-f", "best*", "--no-warnings", VIDEO_URL], 60).stdout.decode().splitlines()
    if not out:
        raise RuntimeError("YouTubeライブURLを取得できません")
    tmp = FRAME.with_suffix(".new.jpg")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", out[0],
         "-vf", "scale=640:360:flags=lanczos", "-frames:v", "1", "-q:v", "2", str(tmp)], 60)
    if tmp.stat().st_size < 5000:
        raise RuntimeError("取得画像が不完全です")
    tmp.replace(FRAME)

def parse_number(text):
    s = text.replace(",", ".").replace(" ", "").replace("O", "0").replace("o", "0")
    m = re.search(r"(\d{1,2})[.]([0-9])", s)
    if m:
        v = float(m.group(1) + "." + m.group(2))
    else:
        m = re.search(r"(\d)(\d)", s)
        if not m:
            return None
        v = float(m.group(1) + "." + m.group(2))
    return v if 0 <= v <= 40 else None

def read_number(name, box, work):
    w, h, x, y = box
    variants = [
        f"crop={w}:{h}:{x}:{y},scale={w*12}:{h*12}:flags=lanczos",
        f"crop={w}:{h}:{x}:{y},scale={w*12}:{h*12}:flags=lanczos,format=gray,lut=y='if(gt(val,70),255,0)',negate",
        f"crop={w}:{h}:{x}:{y},scale={w*14}:{h*14}:flags=lanczos,format=gray,lut=y='if(gt(val,100),255,0)',negate",
    ]
    votes, raw = [], []
    for i, vf in enumerate(variants):
        image = work / f"{name}-{i}.png"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(FRAME), "-vf", vf, str(image)], 20)
        for psm in (7, 8, 13):
            p = run(["tesseract", str(image), "stdout", "--psm", str(psm), "-l", "eng",
                     "-c", "tessedit_char_whitelist=0123456789."], 20, False)
            text = p.stdout.decode("utf-8", "replace").strip()
            raw.append(text)
            v = parse_number(text)
            if v is not None:
                votes.append(v)
    if not votes:
        return None, raw
    value, count = Counter(votes).most_common(1)[0]
    if name != "speed":
        q = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(FRAME),
                 "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], 15, False)
        pix, points = q.stdout, set()
        if len(pix) >= w*h*3:
            for yy in range(h):
                for xx in range(w):
                    j = (yy*w+xx)*3
                    r, g, b = pix[j:j+3]
                    if r > 100 and r > g*1.15 and g > b*1.15:
                        points.add((xx, yy))
        widths = []
        while points:
            stack, comp = [points.pop()], []
            while stack:
                pt = stack.pop(); comp.append(pt)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nxt = (pt[0]+dx, pt[1]+dy)
                        if nxt in points:
                            points.remove(nxt); stack.append(nxt)
            if len(comp) > 20:
                widths.append((min(a for a, _ in comp), max(a for a, _ in comp)-min(a for a, _ in comp)+1))
        widths.sort()
        if len(widths) >= 2 and widths[-1][1] <= 6:
            value = math.floor(value) + 0.1
    return (value if count >= 2 else None), raw

def read_direction(work):
    image = work / "direction.png"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(FRAME),
         "-vf", "crop=190:125:225:120,scale=1520:1000:flags=lanczos,format=gray,eq=contrast=2.4", str(image)], 20)
    p = run(["tesseract", str(image), "stdout", "--psm", "11", "-l", "eng",
             "-c", "tessedit_char_whitelist=NESW"], 20, False)
    text = re.sub("[^NESW]", "", p.stdout.decode("utf-8", "ignore").upper())
    for key in sorted(DIRMAP, key=len, reverse=True):
        if key in text:
            return DIRMAP[key], key
    p = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(FRAME),
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], 20, False)
    raw = p.stdout; sx = sy = count = 0
    cx, cy = 176, 188
    if len(raw) >= 640*360*3:
        for y in range(25, 345):
            for x in range(15, 340):
                dx, dy = x-cx, y-cy; radius = math.hypot(dx, dy)
                if not 105 <= radius <= 145:
                    continue
                i = (y*640+x)*3; r, g, b = raw[i:i+3]
                if r > 125 and r > g*1.25 and g > b*1.15:
                    sx += dx/radius; sy += dy/radius; count += 1
    if count >= 12:
        bearing = (math.degrees(math.atan2(sx, -sy))+360) % 360
        code = DIRS[int((bearing+11.25)//22.5) % 16]
        return DIRMAP[code], code
    return None, None

def sample():
    BASE.mkdir(parents=True, exist_ok=True)
    grab_frame(); old = load_last(); debug = {}
    with tempfile.TemporaryDirectory(dir=BASE) as td:
        work = Path(td); values = {}
        for name, box in CROPS.items():
            values[name], debug[name] = read_number(name, box, work)
        direction, direction_code = read_direction(work)
    for key in CROPS:
        if values[key] is None:
            values[key] = old.get(key)
    if direction is None:
        direction = old.get("direction", "?")
    if direction_code is None:
        direction_code = old.get("direction_code")
    if values["speed"] is None:
        raise RuntimeError("現在風速を認識できません")
    for key in ("maxday", "max1h", "max10"):
        if values[key] is not None and values[key] < values["speed"]:
            values[key] = old.get(key)
    data = {"ts": int(time.time()), **values, "direction": direction,
            "direction_code": direction_code, "debug": debug}
    atomic_json(LAST, data)
    return data

