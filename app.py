#!/usr/bin/env python3
# app.py
# Android Termux auto-installer + movie search + streaming UI + download proxy
# No manual dependency installation required
# Works with public search results and browser HTML5 playback

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

# -------------------------------------------------------------------
# Auto-bootstrap dependencies before importing third-party libs
# -------------------------------------------------------------------

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
            installed_output = run_command(["pkg", "list-installed"], timeout=60)
            if pkg_name in (installed_output.stdout or ""):
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

        install_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--disable-pip-version-check",
            "--no-cache-dir",
            package,
        ]

        result = run_command(install_cmd, timeout=600)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            details = stderr or stdout or "Unknown pip error"
            raise RuntimeError(f"Failed to install Python package '{package}': {details}")

ensure_native_dependencies()
ensure_python_dependencies()

# -------------------------------------------------------------------
# Third-party imports after dependency bootstrap
# -------------------------------------------------------------------

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jinja2 import Template
import uvicorn
import yt_dlp

APP_TITLE = "Termux Movie Stream"
APP_VERSION = "1.0.0"

# -------------------------------------------------------------------
# Utility functions
# -------------------------------------------------------------------

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

def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""

def normalize_thumbnail_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    url = raw_url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url

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

# -------------------------------------------------------------------
# Search result parsing from YouTube public HTML
# -------------------------------------------------------------------

def extract_runs_text(runs: List[Dict[str, Any]]) -> str:
    if not isinstance(runs, list):
        return ""
    texts: List[str] = []
    for item in runs:
        if isinstance(item, dict):
            text = safe_text(item.get("text"), "")
            if text:
                texts.append(text)
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

                thumbnail = ""
                thumbnail_obj = renderer.get("thumbnail", {})
                if isinstance(thumbnail_obj, dict):
                    thumbs = thumbnail_obj.get("thumbnails", []) or []
                    if thumbs:
                        thumbnail = normalize_thumbnail_url(thumbs[-1].get("url", ""))

                if not thumbnail:
                    thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

                duration = ""
                length = renderer.get("lengthText")
                if isinstance(length, dict):
                    duration = safe_text(length.get("simpleText", ""))
                elif isinstance(length, str):
                    duration = safe_text(length)

                url = youtube_watch_url(video_id)
                if url:
                    videos.append({
                        "id": video_id,
                        "title": title,
                        "url": url,
                        "thumbnail": thumbnail,
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
        direct_url = info.get("url") or info.get("webpage_url") or webpage_url
        return {"url": direct_url, "label": "HD", "height": 0}

    filtered: List[Dict[str, Any]] = []
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
    return {"url": stream_url, "label": display_label(height, safe_int(best.get("tbr"), 0)), "height": height}

def search_youtube(query: str, limit: int = 12) -> List[Dict[str, Any]]:
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

    html_text = response.text
    parsed = youtube_video_data_from_html(html_text, max_results=limit)

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

    results: List[Dict[str, Any]] = []
    for item in parsed:
        watch_url = safe_text(item.get("url"), "")
        if not watch_url:
            continue
        stream_info = extract_best_stream(watch_url)
        stream_url = stream_info.get("url") or watch_url
        results.append({
            "title": safe_text(item.get("title"), "Video"),
            "url": watch_url,
            "stream_url": stream_url,
            "download_url": stream_url,
            "thumbnail": safe_text(item.get("thumbnail"), ""),
            "resolution": stream_info.get("label") or "HD",
            "duration": safe_text(item.get("duration"), "N/A"),
            "provider": safe_text(item.get("provider"), "YouTube"),
            "language": q,
        })
    return results[:limit]

def build_language_variants(query: str, language: str) -> List[str]:
    q = canonical_query(query)
    if not q:
        return []
    base = q

    variants_map = {
        "all": [
            base,
            f"{base} movie",
            f"{base} full movie",
            f"{base} latest movie",
            f"{base} dubbed",
            f"{base} hd",
            f"{base} watch online",
        ],
        "hindi": [
            f"{base} hindi movie",
            f"{base} latest hindi movie",
            f"{base} hindi dubbed movie",
            f"{base} bollywood movie",
            f"{base} hindi full movie",
        ],
        "english": [
            f"{base} english movie",
            f"{base} latest english movie",
            f"{base} hollywood movie",
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

    variants = variants_map.get(language.lower(), variants_map["all"])
    unique: List[str] = []
    seen = set()
    for variant in variants:
        normalized = canonical_query(variant)
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique[:8]

def search_generic_web(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    search_url = f"https://www.bing.com/search?q={urllib.parse.quote(query + ' video download')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(search_url, headers=headers, timeout=20, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(response.text, "lxml")
    found: List[Dict[str, Any]] = []
    seen = set()

    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if not href:
            continue
        raw = href.strip()
        if raw.startswith("javascript:") or not raw.startswith("http"):
            continue
        if "youtube.com" not in raw.lower() and ".mp4" not in raw.lower() and ".m3u8" not in raw.lower() and ".mpd" not in raw.lower():
            continue
        if raw in seen:
            continue
        seen.add(raw)

        title = safe_text(link.get_text(" ", strip=True), "Video")
        if not title:
            title = "Video"

        stream_info = extract_best_stream(raw)
        found.append({
            "title": title[:90],
            "url": raw,
            "stream_url": stream_info.get("url") or raw,
            "download_url": stream_info.get("url") or raw,
            "thumbnail": "",
            "resolution": stream_info.get("label") or "HD",
            "duration": "N/A",
            "provider": "Public Web",
            "language": query,
        })
        if len(found) >= limit:
            break

    return found

def search_public_media(query: str, language: str = "all", max_results: int = 12) -> List[Dict[str, Any]]:
    q = canonical_query(query)
    if not q:
        return []

    results: List[Dict[str, Any]] = []
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

# -------------------------------------------------------------------
# FastAPI app
# -------------------------------------------------------------------

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
      --border: rgba(148, 163, 184, 0.12);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      background: radial-gradient(circle at top, #122033 0%, var(--bg) 45%, #080d18 100%);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      min-height: 100%;
    }
    body {
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 18px 14px 52px;
    }
    .container { width: min(1100px, 100%); }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 30;
      background: rgba(9, 15, 28, 0.78);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 18px;
      margin-bottom: 16px;
      box-shadow: 0 12px 30px rgba(10, 15, 30, 0.6);
    }
    .toolbar-inner {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      color: #f4f8ff;
    }
    .brand-mark {
      width: 38px;
      height: 38px;
      display: grid;
      place-items: center;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--primary), var(--primary-2));
      color: #00111f;
      font-weight: 800;
      box-shadow: 0 8px 20px rgba(78, 160, 255, 0.25);
    }
    .search-box {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      width: min(660px, 100%);
      margin-left: auto;
    }
    .search-field {
      flex: 1 1 260px;
      background: rgba(18, 28, 44, 0.95);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 14px;
      color: var(--text);
      font-size: 1rem;
      outline: none;
      min-width: 0;
    }
    .search-field::placeholder { color: var(--muted); }
    select.search-field { max-width: 180px; flex: 0 0 180px; }
    .search-button {
      border: none;
      border-radius: 14px;
      background: linear-gradient(135deg, var(--primary), var(--primary-2));
      color: #081521;
      font-weight: 800;
      padding: 12px 16px;
      cursor: pointer;
      min-width: 110px;
    }
    .hero {
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.7), rgba(17, 32, 49, 0.8));
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 20px;
      margin-bottom: 18px;
      box-shadow: 0 18px 40px rgba(3, 7, 18, 0.35);
    }
    .hero h1 {
      margin: 0 0 8px;
      font-size: clamp(1.8rem, 4vw, 3rem);
      line-height: 1.15;
      letter-spacing: -0.05em;
    }
    .hero p {
      margin: 0;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.6;
    }
    .status-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-top: 18px;
      flex-wrap: wrap;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: rgba(21, 31, 47, 0.8);
      color: var(--muted);
      font-size: 0.9rem;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--success);
      box-shadow: 0 0 12px rgba(42, 224, 164, 0.85);
    }
    .results-head {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 12px 4px 16px;
    }
    .results-head h2 { margin: 0; font-size: 1.15rem; letter-spacing: 0.03em; }
    .hint { color: var(--muted); font-size: 0.92rem; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }
    .card {
      background: linear-gradient(180deg, rgba(17, 24, 39, 0.96), rgba(13, 19, 31, 0.96));
      border: 1px solid var(--border);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 12px 28px rgba(3, 7, 18, 0.32);
      transition: transform 0.18s ease, border-color 0.18s ease;
    }
    .card:hover {
      transform: translateY(-2px);
      border-color: rgba(78, 160, 255, 0.5);
    }
    .thumb-wrap {
      position: relative;
      width: 100%;
      aspect-ratio: 16 / 9;
      overflow: hidden;
      background: #0f172a;
    }
    .thumb {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .badge-time {
      position: absolute;
      right: 10px;
      bottom: 10px;
      background: rgba(10, 13, 20, 0.82);
      color: white;
      border: 1px solid rgba(255,255,255,0.11);
      border-radius: 11px;
      padding: 4px 8px;
      font-size: 0.72rem;
      font-weight: 700;
    }
    .card-body { padding: 12px 12px 14px; }
    .title {
      min-height: 44px;
      font-size: 1rem;
      font-weight: 700;
      line-height: 1.35;
      margin: 0 0 8px;
      color: var(--text);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .meta {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 0.82rem;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .pill {
      border-radius: 999px;
      background: rgba(78, 160, 255, 0.12);
      color: #bfe1ff;
      border: 1px solid rgba(78, 160, 255, 0.18);
      padding: 5px 8px;
      font-weight: 600;
    }
    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .button {
      appearance: none;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: rgba(17, 24, 39, 0.92);
      color: var(--text);
      padding: 9px 11px;
      font-weight: 700;
      cursor: pointer;
      flex: 1 1 110px;
      text-align: center;
      text-decoration: none;
      font-size: 0.9rem;
    }
    .button.primary {
      background: linear-gradient(135deg, var(--primary), var(--primary-2));
      color: #071521;
      border: none;
    }
    .button.danger {
      background: rgba(255, 98, 125, 0.08);
      color: #ffc2ce;
      border-color: rgba(255, 98, 125, 0.2);
    }
    .empty {
      text-align: center;
      color: var(--muted);
      background: rgba(17, 24, 39, 0.7);
      border: 1px dashed rgba(148, 163, 184, 0.35);
      border-radius: 18px;
      padding: 28px 18px;
      margin-top: 16px;
    }
    .modal {
      position: fixed;
      inset: 0;
      background: rgba(2, 6, 15, 0.82);
      display: none;
      place-items: center;
      z-index: 50;
      padding: 18px;
    }
    .modal.show { display: grid; }
    .modal-panel {
      width: min(980px, 100%);
      background: rgba(9, 15, 28, 0.98);
      border: 1px solid var(--border);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 24px 80px rgba(0,0,0,0.5);
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      background: rgba(13, 22, 36, 0.95);
    }
    .modal-title {
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .close-btn {
      width: 36px;
      height: 36px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: rgba(30,41,59,0.82);
      color: var(--text);
      font-size: 1.15rem;
      cursor: pointer;
    }
    video {
      width: 100%;
      height: min(72vh, 600px);
      display: block;
      background: #02070f;
      object-fit: contain;
    }
    @media (max-width: 640px) {
      .toolbar-inner { padding: 12px; }
      .search-box { width: 100%; }
      .search-field, select.search-field, .search-button { width: 100%; max-width: none; flex-basis: 100%; }
      .brand { width: 100%; }
      .hero { padding: 16px; }
      .actions .button { flex: 1 1 100%; }
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
          <input id="query" class="search-field" type="text" placeholder="Search Hindi, English, South Indian, Regional..." value="latest hindi movie" />
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

    <section class="hero">
      <h1>Play Movies from Public Search</h1>
      <p>Search across Hindi, English, South Indian, and regional titles. Each result shows a thumbnail, stream button, and download button.</p>
      <div class="status-row">
        <div class="badge"><span class="dot"></span> Mobile-friendly</div>
        <div class="badge">FastAPI + YouTube Search + HTML5 Player</div>
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
          statusEl.textContent = 'No results found.';
          resultsEl.innerHTML = '<div class=\"empty\">No public movie results found. Try a different query or language.</div>';
          return;
        }

        statusEl.textContent = `${items.length} results for "${query}"`;
        items.forEach((item) => {
          resultsEl.appendChild(renderCard(item));
        });
      } catch (error) {
        console.error(error);
        statusEl.textContent = 'Search failed.';
        resultsEl.innerHTML = '<div class=\"empty\">Could not fetch results. Please try again in a moment.</div>';
      }
    }

    searchForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const query = document.getElementById('query').value.trim();
      const language = document.getElementById('language').value;
      if (!query) {
        document.getElementById('query').focus();
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

    async function loadDefaultMovies() {
      const queries = [
        'latest hindi movie',
        'latest english movie',
        'south indian movie',
        'regional movie',
      ];
      const language = 'all';
      let all = [];
      for (const q of queries) {
        try {
          const response = await fetch(`/api/search?q=${encodeURIComponent(q)}&language=${encodeURIComponent(language)}&limit=6`);
          const payload = await response.json();
          const rows = payload.results || [];
          for (const item of rows) {
            if (!all.some((x) => (x.title === item.title && x.stream_url === item.stream_url))) {
              all.push(item);
            }
          }
        } catch (error) {
          console.error(error);
        }
      }
      statusEl.textContent = all.length ? 'Showing popular movie results' : 'No public results loaded';
      if (!all.length) {
        resultsEl.innerHTML = '<div class=\"empty\">No public movie results found right now. Search manually to try another title.</div>';
        return;
      }
      all.slice(0, 12).forEach((item) => resultsEl.appendChild(renderCard(item)));
    }

    document.addEventListener('DOMContentLoaded', () => {
      loadDefaultMovies();
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
        raise HTTPException(status_code=400, detail="Missing download url")

    decoded_url = urllib.parse.unquote(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Referer": "https://www.youtube.com/",
    }

    try:
        response = requests.get(decoded_url, stream=True, timeout=60, headers=headers, allow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch stream: {exc}")

    filename = decoded_url.split("/")[-1] or "download.bin"
    if not filename.lower().endswith((".mp4", ".m3u8", ".mpd", ".webm", ".mkv", ".ts", ".mov", ".avi")):
        filename = "download.bin"

    return StreamingResponse(
        response.iter_content(chunk_size=65536),
        media_type=response.headers.get("Content-Type", "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )

def main() -> None:
    print(f"[{APP_TITLE}] Starting on http://0.0.0.0:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False, log_level="info")

if __name__ == "__main__":
    main()
