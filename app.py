#!/usr/bin/env python3
# app.py
# Premium Movie Search & Streaming App for Android Termux
# Auto-installs native and Python dependencies
# Home page with trending films + category tabs + search results + browser player + download

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------
# Bootstrapping dependencies before importing third-party libs
# ------------------------------------------------------------

REQUIRED_PYTHON = [
    "fastapi",
    "uvicorn",
    "yt-dlp",
    "beautifulsoup4",
    "requests",
    "lxml",
    "jinja2",
]

MODULE_MAP = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "yt-dlp": "yt_dlp",
    "beautifulsoup4": "bs4",
    "requests": "requests",
    "lxml": "lxml",
    "jinja2": "jinja2",
}

TERMUX = os.path.exists("/data/data/com.termux/files/usr/bin/pkg")
DEBIAN = shutil.which("apt-get") is not None

def run_command(command: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out: {' '.join(command)}") from exc

def install_termux_packages(packages: List[str]) -> None:
    if not TERMUX:
        return
    for pkg_name in packages:
        try:
            installed = run_command(["pkg", "list-installed"], timeout=60)
            if pkg_name in (installed.stdout or ""):
                continue
            result = run_command(["pkg", "install", "-y", pkg_name], timeout=600)
            if result.returncode != 0:
                print(f"[WARN] pkg install failed for '{pkg_name}': {result.stderr.strip() or result.stdout.strip()}")
        except Exception as exc:
            print(f"[WARN] Exception installing '{pkg_name}': {exc}")

def install_debian_packages(packages: List[str]) -> None:
    if not DEBIAN:
        return
    try:
        run_command(["apt-get", "update"], timeout=600)
    except Exception:
        pass

    for pkg_name in packages:
        try:
            result = run_command(["apt-get", "install", "-y", pkg_name], timeout=600)
            if result.returncode != 0:
                print(f"[WARN] apt-get failed for '{pkg_name}': {result.stderr.strip() or result.stdout.strip()}")
        except Exception as exc:
            print(f"[WARN] Exception apt-get installing '{pkg_name}': {exc}")

def ensure_native_dependencies() -> None:
    if TERMUX:
        install_termux_packages([
            "ffmpeg",
            "libxml2",
            "libxslt",
            "libxml2-dev",
            "libxslt-dev",
            "clang",
            "make",
            "pkg-config",
        ])
    elif DEBIAN:
        install_debian_packages([
            "ffmpeg",
            "libxml2-dev",
            "libxslt1.1",
            "libxslt-dev",
            "build-essential",
            "pkg-config",
        ])

def ensure_python_dependencies() -> None:
    for package in REQUIRED_PYTHON:
        module_name = MODULE_MAP.get(package, package)
        try:
            __import__(module_name)
            continue
        except Exception:
            pass

        if package == "lxml":
            ensure_native_dependencies()

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--disable-pip-version-check",
            "--no-cache-dir",
            package,
        ]
        result = run_command(cmd, timeout=600)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Failed to install '{package}': {err}")

ensure_native_dependencies()
ensure_python_dependencies()

# ------------------------------------------------------------
# Import loaded third-party libs
# ------------------------------------------------------------

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jinja2 import Template
import uvicorn
import yt_dlp

APP_TITLE = "Movie Search Pro"
APP_VERSION = "1.0.0"

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def safe_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def canonical_query(raw_query: str) -> str:
    q = safe_text(raw_query, "")
    q = q.replace("\n", " ").replace("\r", " ")
    q = re.sub(r"\s+", " ", q)
    return q.strip()

def normalize_url(raw: str) -> str:
    if not raw:
        return ""
    url = raw.strip()
    if url.startswith("//"):
        return "https:" + url
    return url

def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""

def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "N/A"
    try:
        seconds = int(seconds)
    except Exception:
        return "N/A"

    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, rem = divmod(minutes, 60)
    return f"{hours}h {rem}m"

def display_label(height: Optional[int], tbr: Optional[int]) -> str:
    target = height or tbr
    if target is None:
        return "HD"
    return f"{target}p"

# ------------------------------------------------------------
# YouTube page parsing
# ------------------------------------------------------------

def extract_runs_text(runs: List[Dict[str, Any]]) -> str:
    if not isinstance(runs, list):
        return ""
    texts = []
    for item in runs:
        if isinstance(item, dict):
            txt = safe_text(item.get("text"), "")
            if txt:
                texts.append(txt)
    return " ".join(texts).strip()

def youtube_video_data_from_html(html_text: str, max_results: int = 12) -> List[Dict[str, Any]]:
    match = re.search(r'ytInitialData\s*=\s*({.*?})\s*;\s*(?:var\s+\w+|</script>)', html_text, re.S)
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
    except Exception:
        return []

    videos: List[Dict[str, Any]] = []
    seen = set()

    def walk(node: Any) -> None:
        if len(videos) >= max_results:
            return

        if isinstance(node, dict):
            if "videoRenderer" in node:
                renderer = node["videoRenderer"]
                video_id = safe_text(renderer.get("videoId"))
                if not video_id or video_id in seen:
                    return
                seen.add(video_id)

                title_obj = renderer.get("title", {})
                title = ""
                if isinstance(title_obj, dict):
                    title = extract_runs_text(title_obj.get("runs", []))
                if not title:
                    title = safe_text(renderer.get("title"), "Untitled Video")

                thumb = ""
                thumb_obj = renderer.get("thumbnail", {})
                if isinstance(thumb_obj, dict):
                    thumbs = thumb_obj.get("thumbnails", []) or []
                    if thumbs:
                        thumb = normalize_url(thumbs[-1].get("url", ""))

                if not thumb:
                    thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

                duration = ""
                length_obj = renderer.get("lengthText")
                if isinstance(length_obj, dict):
                    duration = safe_text(length_obj.get("simpleText", ""))
                elif isinstance(length_obj, str):
                    duration = safe_text(length_obj)

                url = youtube_watch_url(video_id)
                if url:
                    videos.append({
                        "id": video_id,
                        "title": title,
                        "url": url,
                        "thumbnail": thumb,
                        "duration": duration or "N/A",
                        "provider": "YouTube",
                    })
                    if len(videos) >= max_results:
                        return

            for value in node.values():
                walk(value)

        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return videos

# ------------------------------------------------------------
# Best stream extraction
# ------------------------------------------------------------

def extract_best_stream(webpage_url: str) -> Dict[str, Any]:
    if not webpage_url:
        return {"url": "", "label": "HD", "height": 0}

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "ignoreerrors": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(webpage_url, download=False)
    except Exception:
        return {"url": webpage_url, "label": "HD", "height": 0}

    if not isinstance(info, dict):
        return {"url": webpage_url, "label": "HD", "height": 0}

    formats = info.get("formats", []) or []
    if not formats:
        direct = info.get("url") or info.get("webpage_url") or webpage_url
        return {"url": direct, "label": "HD", "height": 0}

    filtered = []
    for fmt in formats:
        if not fmt.get("url"):
            continue
        ext = (fmt.get("ext") or "").lower()
        if ext and ext not in {"mp4", "m4a", "m4v", "webm", "ts", "m3u8", "mpd"}:
            continue
        if "manifest" in (fmt.get("format_note") or "").lower():
            continue
        filtered.append(fmt)

    if not filtered:
        filtered = formats

    filtered_sorted = sorted(
        filtered,
        key=lambda x: (
            safe_int(x.get("height"), 0),
            safe_int(x.get("tbr"), 0),
            safe_int(x.get("fps"), 0),
        ),
        reverse=True,
    )

    best = filtered_sorted[0]
    stream_url = best.get("url") or webpage_url
    height = safe_int(best.get("height"), 0)
    label = display_label(height, safe_int(best.get("tbr"), 0))
    return {"url": stream_url, "label": label, "height": height}

# ------------------------------------------------------------
# Search functions
# ------------------------------------------------------------

def search_youtube(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    q = canonical_query(query)
    if not q:
        return []

    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(q)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(search_url, headers=headers, timeout=25, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return []

    parsed = youtube_video_data_from_html(response.text, max_results=limit)

    if not parsed:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "ignoreerrors": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
            entries = info.get("entries", []) if isinstance(info, dict) else []
            parsed = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                vid = safe_text(entry.get("id") or "")
                if not vid:
                    continue
                parsed.append({
                    "id": vid,
                    "title": safe_text(entry.get("title"), "Video"),
                    "url": youtube_watch_url(vid),
                    "thumbnail": safe_text(entry.get("thumbnail") or "", ""),
                    "duration": format_duration(safe_int(entry.get("duration"), 0)),
                    "provider": "YouTube",
                })
        except Exception:
            parsed = []

    results = []
    seen = set()

    for item in parsed:
        watch_url = safe_text(item.get("url"), "")
        if not watch_url:
            continue
        stream_info = extract_best_stream(watch_url)
        stream_url = stream_info.get("url") or watch_url
        title = safe_text(item.get("title"), "Video")
        thumb = safe_text(item.get("thumbnail"), "")
        key = (title, stream_url)
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "title": title,
            "url": watch_url,
            "stream_url": stream_url,
            "download_url": stream_url,
            "thumbnail": thumb,
            "resolution": stream_info.get("label") or "HD",
            "duration": safe_text(item.get("duration"), "N/A"),
            "provider": safe_text(item.get("provider"), "YouTube"),
            "language": q,
        })

    return results[:limit]

def search_generic_web(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    url = f"https://www.bing.com/search?q={urllib.parse.quote(query + ' movie download')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(response.text, "lxml")
    out = []
    seen = set()

    for a in soup.select("a[href]"):
        href = safe_text(a.get("href"), "")
        if not href or href.startswith("javascript:") or not href.startswith("http"):
            continue

        low = href.lower()
        if "youtube.com" not in low and ".mp4" not in low and ".m3u8" not in low and ".mpd" not in low and ".webm" not in low:
            continue

        if href in seen:
            continue
        seen.add(href)

        title = safe_text(a.get_text(" ", strip=True), "Video")
        stream_info = extract_best_stream(href)
        out.append({
            "title": title[:95] if title else "Video",
            "url": href,
            "stream_url": stream_info.get("url") or href,
            "download_url": stream_info.get("url") or href,
            "thumbnail": "",
            "resolution": stream_info.get("label") or "HD",
            "duration": "N/A",
            "provider": "Public Web",
            "language": query,
        })
        if len(out) >= limit:
            break

    return out

def build_language_variants(query: str, language: str) -> List[str]:
    q = canonical_query(query)
    if not q:
        return []

    base = q
    lang_map = {
        "all": [
            base,
            f"{base} movie",
            f"{base} full movie",
            f"{base} latest movie",
            f"{base} dubbed",
            f"{base} hd",
            f"{base} 2025",
            f"{base} official trailer",
        ],
        "hindi": [
            f"{base} hindi movie",
            f"{base} latest hindi movie",
            f"{base} bollywood movie",
            f"{base} hindi dubbed movie",
            f"{base} hindi full movie",
        ],
        "english": [
            f"{base} english movie",
            f"{base} hollywood movie",
            f"{base} latest english movie",
            f"{base} english full movie",
            f"{base} english 1080p",
        ],
        "south indian": [
            f"{base} south indian movie",
            f"{base} tamil movie",
            f"{base} telugu movie",
            f"{base} malayalam movie",
            f"{base} kannada movie",
        ],
        "regional": [
            f"{base} marathi movie",
            f"{base} bengali movie",
            f"{base} gujarati movie",
            f"{base} punjabi movie",
            f"{base} regional movie",
        ],
    }

    variants = lang_map.get(language.lower(), lang_map["all"])
    unique = []
    seen = set()
    for item in variants:
        norm = canonical_query(item)
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(norm)
    return unique[:8]

def search_public_media(query: str, language: str = "all", max_results: int = 12) -> List[Dict[str, Any]]:
    q = canonical_query(query)
    if not q:
        return []

    results = []
    seen = set()

    for variant in build_language_variants(q, language):
        for item in search_youtube(variant, limit=5):
            key = (item.get("title"), item.get("stream_url"))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= max_results:
                return results

        for item in search_generic_web(variant, limit=3):
            key = (item.get("title"), item.get("stream_url"))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= max_results:
                return results

    return results[:max_results]

# ------------------------------------------------------------
# Trending queries for home tabs
# ------------------------------------------------------------

TRENDING_MAP = {
    "trending": [
        "Spider-Man latest movie",
        "new Hollywood movie 2025",
        "latest movie in theaters",
        "new Hindi movie 2025",
        "new South Indian movie 2025",
        "latest Bollywood movie 2025",
        "Spider-Man official trailer",
        "latest movie release 2025",
        "new theater movie 2025",
        "biggest movie trending 2025",
        "new Hindi dubbed movie 2025",
        "new English movie 2025",
    ],
    "hindi": [
        "latest Hindi movie 2025",
        "new Bollywood movie 2025",
        "hindi movie release 2025",
        "latest Hindi dubbed movie 2025",
        "new Hindi cinema 2025",
    ],
    "english": [
        "latest English movie 2025",
        "Spider-Man movie",
        "new Hollywood movie 2025",
        "latest action movie 2025",
        "movie release in theaters",
    ],
    "south indian": [
        "latest Tamil movie 2025",
        "latest Telugu movie 2025",
        "new South Indian movie 2025",
        "latest Malayalam movie 2025",
        "latest Kannada movie 2025",
    ],
    "regional": [
        "latest regional movie 2025",
        "new Marathi movie 2025",
        "new Bengali movie 2025",
        "new Punjabi movie 2025",
        "new Gujarati movie 2025",
    ],
}

def get_trending_movies(category: str = "trending", limit: int = 12) -> List[Dict[str, Any]]:
    queries = TRENDING_MAP.get(category.lower(), TRENDING_MAP["trending"])
    results: List[Dict[str, Any]] = []
    seen = set()

    for query in queries:
        for item in search_public_media(query, language="all", max_results=4):
            key = (item.get("title"), item.get("stream_url"))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= limit:
                return results

    return results[:limit]

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

HTML_TEMPLATE = Template("""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>{{ title }}</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: #101827;
      --panel-2: #111b2d;
      --panel-3: #152238;
      --text: #e5edf9;
      --muted: #9bb0cc;
      --primary: #4ea0ff;
      --primary-2: #66d9ef;
      --success: #2ae0a4;
      --danger: #ff627d;
      --border: rgba(148,163,184,0.12);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      background: radial-gradient(circle at top, #122033 0%, var(--bg) 45%, #080d18 100%);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      min-height: 100%;
    }
    body {
      display: flex; flex-direction: column; align-items: center;
      padding: 18px 14px 52px;
    }
    .container { width: min(1120px, 100%); }
    .toolbar {
      position: sticky; top: 0; z-index: 30;
      background: rgba(9,15,28,0.78);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 18px;
      margin-bottom: 16px;
      box-shadow: 0 12px 30px rgba(10,15,30,0.6);
    }
    .toolbar-inner {
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px;
    }
    .brand {
      display: flex; align-items: center; gap: 12px; font-weight: 700; letter-spacing: 0.04em; color: #f4f8ff;
    }
    .brand-mark {
      width: 38px; height: 38px; display: grid; place-items: center; border-radius: 12px;
      background: linear-gradient(135deg, var(--primary), var(--primary-2)); color: #00111f; font-weight: 800;
      box-shadow: 0 8px 20px rgba(78,160,255,0.25);
    }
    .search-box {
      display: flex; flex-wrap: wrap; gap: 10px; width: min(700px, 100%); margin-left: auto;
    }
    .search-field {
      flex: 1 1 260px; background: rgba(18,28,44,0.95); border: 1px solid var(--border); border-radius: 14px; padding: 12px 14px;
      color: var(--text); font-size: 1rem; outline: none; min-width: 0;
    }
    .search-field::placeholder { color: var(--muted); }
    select.search-field { max-width: 180px; flex: 0 0 180px; }

    .search-button {
      border: none; border-radius: 14px; background: linear-gradient(135deg, var(--primary), var(--primary-2)); color: #081521; font-weight: 800;
      padding: 12px 16px; cursor: pointer; min-width: 110px;
    }

    .tabs {
      display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 16px; padding: 0;
    }
    .tab-btn {
      background: rgba(17,24,39,0.88); border: 1px solid var(--border); color: var(--text);
      border-radius: 12px; padding: 10px 12px; font-weight: 700; cursor: pointer;
    }
    .tab-btn.active {
      background: linear-gradient(135deg, var(--primary), var(--primary-2)); color: #071521; border: none;
    }

    .hero {
      background: linear-gradient(135deg, rgba(30,41,59,0.7), rgba(17,32,49,0.8));
      border: 1px solid var(--border); border-radius: 22px; padding: 20px; margin-bottom: 18px;
      box-shadow: 0 18px 40px rgba(3,7,18,0.35);
    }
    .hero h1 {
      margin: 0 0 8px; font-size: clamp(1.8rem, 4vw, 3rem); line-height: 1.15; letter-spacing: -0.05em;
    }
    .hero p { margin: 0; color: var(--muted); font-size: 1rem; line-height: 1.6; }
    .status-row {
      display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 18px; flex-wrap: wrap;
    }
    .badge {
      display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px; border: 1px solid var(--border);
      background: rgba(21,31,47,0.8); color: var(--muted); font-size: 0.9rem;
    }
    .dot {
      width: 10px; height: 10px; border-radius: 999px; background: var(--success); box-shadow: 0 0 12px rgba(42,224,164,0.85);
    }
    .results-head {
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin: 12px 4px 16px;
    }
    .results-head h2 { margin: 0; font-size: 1.15rem; letter-spacing: 0.03em; }
    .hint { color: var(--muted); font-size: 0.92rem; }

    .grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px;
    }
    .card {
      background: linear-gradient(180deg, rgba(17,24,39,0.96), rgba(13,19,31,0.96));
      border: 1px solid var(--border);
      border-radius: 18px; overflow: hidden;
      box-shadow: 0 12px 28px rgba(3,7,18,0.32);
      transition: transform 0.18s ease, border-color 0.18s ease;
    }
    .card:hover { transform: translateY(-2px); border-color: rgba(78,160,255,0.5); }
    .thumb-wrap {
      position: relative; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; background: #0f172a;
    }
    .thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
    .badge-time {
      position: absolute; right: 10px; bottom: 10px; background: rgba(10,13,20,0.82); color: white; border: 1px solid rgba(255,255,255,0.11); border-radius: 11px; padding: 4px 8px; font-size: 0.72rem; font-weight: 700;
    }
    .card-body { padding: 12px 12px 14px; }
    .title {
      min-height: 44px; font-size: 1rem; font-weight: 700; line-height: 1.35; margin: 0 0 8px; color: var(--text);
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .meta {
      display: flex; justify-content: space-between; gap: 8px; align-items: center; color: var(--muted); font-size: 0.82rem; margin-bottom: 12px; flex-wrap: wrap;
    }
    .pill {
      border-radius: 999px; background: rgba(78,160,255,0.12); color: #bfe1ff; border: 1px solid rgba(78,160,255,0.18); padding: 5px 8px; font-weight: 600;
    }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .button {
      appearance: none; border: 1px solid var(--border); border-radius: 10px; background: rgba(17,24,39,0.92); color: var(--text);
      padding: 9px 11px; font-weight: 700; cursor: pointer; flex: 1 1 110px; text-align: center; text-decoration: none; font-size: 0.9rem;
    }
    .button.primary { background: linear-gradient(135deg, var(--primary), var(--primary-2)); color: #071521; border: none; }
    .button.danger { background: rgba(255,98,125,0.08); color: #ffc2ce; border-color: rgba(255,98,125,0.2); }
    .empty {
      text-align: center; color: var(--muted); background: rgba(17,24,39,0.7); border: 1px dashed rgba(148,163,184,0.35); border-radius: 18px; padding: 28px 18px; margin-top: 16px;
    }
    .modal {
      position: fixed; inset: 0; background: rgba(2,6,15,0.82); display: none; place-items: center; z-index: 50; padding: 18px;
    }
    .modal.show { display: grid; }
    .modal-panel {
      width: min(980px, 100%); background: rgba(9,15,28,0.98); border: 1px solid var(--border); border-radius: 18px; overflow: hidden; box-shadow: 0 24px 80px rgba(0,0,0,0.5);
    }
    .modal-header {
      display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid var(--border); background: rgba(13,22,36,0.95);
    }
    .modal-title {
      font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .close-btn {
      width: 36px; height: 36px; border: 1px solid var(--border); border-radius: 10px; background: rgba(30,41,59,0.82); color: var(--text); font-size: 1.15rem; cursor: pointer;
    }
    video {
      width: 100%; height: min(72vh, 600px); display: block; background: #02070f; object-fit: contain;
    }
    @media (max-width: 640px) {
      .toolbar-inner { padding: 12px; }
      .search-box { width: 100%; }
      .search-field, select.search-field, .search-button { width: 100%; max-width: none; flex-basis: 100%; }
      .brand { width: 100%; }
      .hero { padding: 16px; }
      .actions .button { flex: 1 1 100%; }
      .tabs { overflow-x: auto; white-space: nowrap; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="toolbar">
      <div class="toolbar-inner">
        <div class="brand">
          <div class="brand-mark">M</div>
          <span>{{ title }}</span>
        </div>
        <form id="searchForm" class="search-box">
          <input id="query" class="search-field" type="text" placeholder="Search movies..." value="Spider-Man" />
          <select id="language" class="search-field">
            <option value="all">All</option>
            <option value="hindi">Hindi</option>
            <option value="english">English</option>
            <option value="south indian">South Indian</option>
            <option value="regional">Regional</option>
          </select>
          <button class="search-button" type="submit">Search</button>
        </form>
      </div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" data-category="trending" type="button">Trending</button>
      <button class="tab-btn" data-category="hindi" type="button">Hindi</button>
      <button class="tab-btn" data-category="english" type="button">English</button>
      <button class="tab-btn" data-category="south indian" type="button">South Indian</button>
      <button class="tab-btn" data-category="regional" type="button">Regional</button>
    </div>

    <section class="hero">
      <h1>Latest Movies, Trending Now</h1>
      <p>Spider-Man, new theater releases, Bollywood, Hollywood, South Indian, and regional movie results with thumbnails, streaming, and download options.</p>
      <div class="status-row">
        <div class="badge"><span class="dot"></span> Auto-ready for Termux</div>
        <div class="badge">FastAPI + yt-dlp + HTML5 Player</div>
      </div>
    </section>

    <div class="results-head">
      <h2>Movies</h2>
      <div id="status" class="hint">Loading...</div>
    </div>

    <div id="results" class="grid"></div>
  </div>

  <div id="modal" class="modal" aria-modal="true" role="dialog">
    <div class="modal-panel">
      <div class="modal-header">
        <div id="modalTitle" class="modal-title">Preview</div>
        <button id="closeModal" class="close-btn" aria-label="Close stream">✕</button>
      </div>
      <video id="videoPlayer" controls playsinline preload="auto"></video>
    </div>
  </div>

  <script>
    const resultsEl = document.getElementById('results');
    const statusEl = document.getElementById('status');
    const modal = document.getElementById('modal');
    const videoPlayer = document.getElementById('videoPlayer');
    const modalTitle = document.getElementById('modalTitle');
    const searchForm = document.getElementById('searchForm');
    const queryInput = document.getElementById('query');
    const languageSelect = document.getElementById('language');

    function escapeHtml(value = '') {
      return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function renderCard(item) {
      const card = document.createElement('article');
      card.className = 'card';

      const thumbnailUrl = item.thumbnail || 'https://images.unsplash.com/photo-1492691527719-9d1e07e534b4?auto=format&fit=crop&w=1200&q=80';
      const streamUrl = item.stream_url || item.url || '';
      const downloadUrl = item.download_url || item.stream_url || item.url || '';

      card.innerHTML = `
        <div class="thumb-wrap">
          <img class="thumb" src="${thumbnailUrl}" alt="${escapeHtml(item.title || 'Movie')}" loading="lazy" />
          <div class="badge-time">${escapeHtml(item.duration || 'N/A')}</div>
        </div>
        <div class="card-body">
          <h3 class="title">${escapeHtml(item.title || 'Untitled')}</h3>
          <div class="meta">
            <span class="pill">${escapeHtml(item.provider || 'Public')}</span>
            <span>${escapeHtml(item.resolution || 'HD')}</span>
          </div>
          <div class="actions">
            <button class="button primary" type="button" data-action="play" data-url="${escapeHtml(streamUrl)}" data-title="${escapeHtml(item.title || 'Movie')}">Play</button>
            <button class="button danger" type="button" data-action="download" data-url="${escapeHtml(downloadUrl)}">Download</button>
          </div>
        </div>
      `;

      card.querySelectorAll('button').forEach((button) => {
        button.addEventListener('click', () => {
          const action = button.dataset.action;
          const url = button.dataset.url;
          if (action === 'play') {
            openModal(url, button.dataset.title || 'Movie');
          } else if (action === 'download') {
            const target = encodeURIComponent(url);
            window.location.href = `/download?url=${target}`;
          }
        });
      });

      return card;
    }

    function openModal(url, title) {
      modalTitle.textContent = title || 'Movie';
      videoPlayer.src = url;
      modal.classList.add('show');
      videoPlayer.play().catch(() => {});
    }

    function closeModal() {
      modal.classList.remove('show');
      videoPlayer.pause();
      videoPlayer.removeAttribute('src');
      videoPlayer.load();
    }

    async function fetchResults(query, language) {
      statusEl.textContent = 'Searching...';
      resultsEl.innerHTML = '';

      try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&language=${encodeURIComponent(language)}&limit=12`);
        const payload = await response.json();
        const items = payload.results || [];

        if (!items.length) {
          statusEl.textContent = 'No public results found.';
          resultsEl.innerHTML = '<div class=\"empty\">No results found for this movie title. Try another search or category.</div>';
          return;
        }

        statusEl.textContent = `${items.length} results for "${query}"`;
        items.forEach((item) => resultsEl.appendChild(renderCard(item)));
      } catch (error) {
        console.error(error);
        statusEl.textContent = 'Search failed.';
        resultsEl.innerHTML = '<div class=\"empty\">Could not fetch results. Please try again.</div>';
      }
    }

    async function loadTrending(category) {
      statusEl.textContent = 'Loading trending movies...';
      resultsEl.innerHTML = '';

      try {
        const response = await fetch(`/api/trending?category=${encodeURIComponent(category)}`);
        const payload = await response.json();
        const items = payload.results || [];

        if (!items.length) {
          resultsEl.innerHTML = '<div class=\"empty\">No trending movie results found right now.</div>';
          statusEl.textContent = 'No trending results';
          return;
        }

        statusEl.textContent = `${items.length} trending results`;
        items.forEach((item) => resultsEl.appendChild(renderCard(item)));
      } catch (error) {
        console.error(error);
        statusEl.textContent = 'Trending load failed';
        resultsEl.innerHTML = '<div class=\"empty\">Trending movies failed to load. Use search manually.</div>';
      }
    }

    document.querySelectorAll('.tab-btn').forEach((button) => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.remove('active'));
        button.classList.add('active');

        const category = button.dataset.category || 'trending';
        if (category === 'trending') {
          queryInput.value = 'Spider-Man';
          languageSelect.value = 'all';
        } else if (category === 'hindi') {
          queryInput.value = 'latest Hindi movie';
          languageSelect.value = 'hindi';
        } else if (category === 'english') {
          queryInput.value = 'latest English movie';
          languageSelect.value = 'english';
        } else if (category === 'south indian') {
          queryInput.value = 'latest South Indian movie';
          languageSelect.value = 'south indian';
        } else {
          queryInput.value = 'latest regional movie';
          languageSelect.value = 'regional';
        }

        loadTrending(category);
      });
    });

    searchForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const query = queryInput.value.trim();
      const language = languageSelect.value;
      if (!query) {
        queryInput.focus();
        return;
      }
      fetchResults(query, language);
    });

    document.getElementById('closeModal').addEventListener('click', closeModal);
    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    document.addEventListener('DOMContentLoaded', () => {
      loadTrending('trending');
    });
  </script>
</body>
</html>
""")

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(content=HTML_TEMPLATE.render(title=APP_TITLE))

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "title": APP_TITLE,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "python": sys.version.split()[0],
    }

@app.get("/api/trending")
async def api_trending(category: str = Query("trending")) -> JSONResponse:
    category_key = canonical_query(category).lower() or "trending"
    results = get_trending_movies(category_key, limit=12)
    return JSONResponse({
        "category": category_key,
        "count": len(results),
        "results": results,
    })

@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=1),
    language: str = Query("all"),
    limit: int = Query(12, ge=1, le=25),
) -> JSONResponse:
    query = canonical_query(q)
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    results = search_public_media(query, language=language, max_results=limit)
    return JSONResponse({
        "query": query,
        "language": language,
        "count": len(results),
        "results": results,
    })

@app.get("/download")
async def download_media(url: str = Query(...)) -> StreamingResponse:
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")

    decoded = urllib.parse.unquote(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Referer": "https://www.youtube.com/",
    }

    # Cache downloaded file locally
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", decoded.split("/")[-1] or "movie")[:80]
    if not safe_name.lower().endswith((".mp4", ".m3u8", ".mpd", ".webm", ".mkv", ".ts", ".mov", ".avi")):
        safe_name = "movie_download.mp4"

    cache_id = hashlib.md5(decoded.encode("utf-8")).hexdigest()
    file_path = os.path.join(tempfile.gettempdir(), f"movie_stream_{cache_id}_{safe_name}")

    try:
        if "youtube.com" in decoded.lower() or "youtu.be" in decoded.lower():
            opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "format": "best[ext=mp4]/best",
                "outtmpl": file_path,
                "restrictfilenames": False,
                "nocheckcertificate": True,
                "geo_bypass": True,
                "merge_output_format": "mp4",
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([decoded])
        else:
            response = requests.get(decoded, stream=True, timeout=60, headers=headers, allow_redirects=True)
            response.raise_for_status()
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to prepare download: {exc}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=502, detail="Download file not generated")

    with open(file_path, "rb") as f:
        payload = f.read()

    return StreamingResponse(
        iter([payload]),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{os.path.basename(file_path)}"',
            "Cache-Control": "no-store",
        },
    )

def main() -> None:
    print(f"[{APP_TITLE}] Starting server on http://0.0.0.0:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False, log_level="info")

if __name__ == "__main__":
    main()
