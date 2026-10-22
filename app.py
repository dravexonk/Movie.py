#!/usr/bin/env python3
# app.py
# Production-ready single-file Android Termux app: media search + streaming UI + downloader
# Run:
#   python app.py
# Then browse:
#   http://127.0.0.1:8000

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

# Bootstrap dependency installation before importing third-party libraries.
REQUIRED_PYTHON = [
    "fastapi",
    "uvicorn",
    "yt-dlp",
    "beautifulsoup4",
    "requests",
    "lxml",
    "jinja2",
]
REQUIRED_SYSTEM = ["ffmpeg"]

MODULE_MAP = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "yt-dlp": "yt_dlp",
    "beautifulsoup4": "bs4",
    "requests": "requests",
    "lxml": "lxml",
    "jinja2": "jinja2",
}

def run_command(command: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)

def ensure_system_dependencies() -> None:
    for tool in REQUIRED_SYSTEM:
        if shutil.which(tool) is not None:
            continue
        if os.path.exists("/data/data/com.termux/files/usr/bin/pkg"):
            subprocess.run(["pkg", "install", "-y", tool], check=False)
        elif shutil.which("apt-get"):
            subprocess.run(["apt-get", "update"], check=False)
            subprocess.run(["apt-get", "install", "-y", tool], check=False)
        else:
            raise RuntimeError(f"Required system dependency not found and could not be installed: {tool}")

def ensure_python_dependencies() -> None:
    for package in REQUIRED_PYTHON:
        module_name = MODULE_MAP.get(package, package)
        try:
            __import__(module_name)
            continue
        except Exception:
            pass

        pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", package]
        result = run_command(pip_cmd)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to install Python package '{package}': {result.stderr.strip() or result.stdout.strip()}")

ensure_system_dependencies()
ensure_python_dependencies()

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jinja2 import Template
from lxml import html as lxml_html
import uvicorn
import yt_dlp

APP_TITLE = "Termux Media Search & Stream"
APP_VERSION = "1.0.0"

# -----------------------------
# Utility helpers
# -----------------------------

def safe_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip() or fallback

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "N/A"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
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
        return "Auto"
    return f"{target}p"

def canonical_query(raw_query: str) -> str:
    q = safe_text(raw_query, "")
    q = q.replace("\n", " ").replace("\r", " ")
    q = re.sub(r"\s+", " ", q)
    return q.strip()

def build_language_variants(query: str, language: str) -> List[str]:
    cleaned = canonical_query(query)
    if not cleaned:
        return []
    base = cleaned
    languages = {
        "all": [
            base,
            f"{base} movie",
            f"{base} full movie",
            f"{base} latest",
            f"{base} dubbed",
            f"{base} hd",
            f"{base} 1080p",
        ],
        "hindi": [
            f"{base} hindi movie",
            f"{base} hindi dubbed movie",
            f"{base} bollywood movie",
            f"{base} latest hindi movie",
            f"{base} hindi full movie",
        ],
        "english": [
            f"{base} english movie",
            f"{base} hollywood movie",
            f"{base} latest english film",
            f"{base} english action movie",
            f"{base} english 1080p",
        ],
        "south indian": [
            f"{base} south indian movie",
            f"{base} tamil movie",
            f"{base} telugu movie",
            f"{base} kannada movie",
            f"{base} malayalam movie",
            f"{base} south indian dubbed",
        ],
        "regional": [
            f"{base} marathi movie",
            f"{base} bengali movie",
            f"{base} gujarati movie",
            f"{base} punjabi movie",
            f"{base} odia movie",
            f"{base} regional movie",
            f"{base} hindi regional dubbed",
        ],
    }
    variants = languages.get(language.lower(), languages["all"])
    unique: List[str] = []
    seen = set()
    for variant in variants:
        normalized = canonical_query(variant)
        if not normalized or normalized in seen:
            continue
        unique.append(normalized)
        seen.add(normalized)
    return unique[:8]

# -----------------------------
# Media extraction engine
# -----------------------------

def extract_best_stream(webpage_url: str) -> Dict[str, Any]:
    if not webpage_url:
        return {"url": "", "label": "Direct", "height": 0}

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
        if not isinstance(info, dict):
            return {"url": webpage_url, "label": "Direct", "height": 0}

        formats = info.get("formats", []) or []
        if not formats:
            # fallback to direct URL if present
            direct_url = info.get("url") or info.get("webpage_url") or webpage_url
            return {"url": direct_url, "label": "Direct", "height": 0}

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

        # Prefer video-compatible streams and highest quality.
        filtered_sorted = sorted(
            filtered,
            key=lambda x: (
                int(x.get("height") or 0),
                int(x.get("tbr") or 0),
                int(x.get("fps") or 0),
            ),
            reverse=True,
        )

        best = filtered_sorted[0]
        stream_url = best.get("url") or webpage_url
        height = safe_int(best.get("height"), 0)
        label = display_label(height, safe_int(best.get("tbr"), 0))
        return {"url": stream_url, "label": label, "height": height}
    except Exception:
        return {"url": webpage_url, "label": "Direct", "height": 0}

def search_youtube(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = canonical_query(query)
    if not q:
        return []

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "noplaylist": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    }

    results: List[Dict[str, Any]] = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
    except Exception:
        return results

    entries = info.get("entries", []) if isinstance(info, dict) else []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = safe_text(item.get("title"), "Unknown Title")
        webpage_url = safe_text(item.get("webpage_url") or item.get("url"), "")
        thumbnail = safe_text(item.get("thumbnail") or "", "")
        if not webpage_url:
            continue

        stream_data = extract_best_stream(webpage_url)
        stream_url = stream_data.get("url") or webpage_url
        label = stream_data.get("label") or "Direct"

        result = {
            "title": title,
            "url": webpage_url,
            "stream_url": stream_url,
            "download_url": stream_url,
            "thumbnail": thumbnail,
            "resolution": label,
            "duration": format_duration(safe_int(item.get("duration"), 0)),
            "provider": "YouTube",
            "language": q,
        }
        results.append(result)
    return results

def extract_links_from_public_html(url: str) -> List[str]:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
        response = requests.get(url, timeout=25, headers=headers, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return []

    page_text = response.text
    soup = BeautifulSoup(page_text, "lxml")
    links = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if not href:
            continue
        href = requests.compat.urljoin(url, href)
        if re.search(r"(youtube\\.com/watch|youtube\\.com/shorts|vimeo\\.com|\\.mp4|\\.m3u8|\\.m3u|\\.webm|/embed/)", href, re.IGNORECASE):
            links.add(href)

    return sorted(links)[:25]

def search_generic_public_index(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    candidates = [
        f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}",
        f"https://www.bing.com/search?q={urllib.parse.quote(query + ' video')}",
    ]
    results: List[Dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        for href in extract_links_from_public_html(candidate):
            if href in seen:
                continue
            seen.add(href)
            if "youtube.com/watch" in href.lower() or "youtube.com/shorts" in href.lower():
                stream_data = extract_best_stream(href)
                title = safe_text(re.sub(r"\\s+", " ", href.split("v=")[-1] if "youtube.com/watch" in href.lower() else href), "Video")
                results.append({
                    "title": title[:80] if title else "Public Video",
                    "url": href,
                    "stream_url": stream_data.get("url") or href,
                    "download_url": stream_data.get("url") or href,
                    "thumbnail": "",
                    "resolution": stream_data.get("label") or "Direct",
                    "duration": "N/A",
                    "provider": "Public Index",
                    "language": query,
                })
                if len(results) >= limit:
                    return results
    return results

def search_public_media(query: str, language: str = "all", max_results: int = 12) -> List[Dict[str, Any]]:
    cleaned_query = canonical_query(query)
    if not cleaned_query:
        return []

    combined: List[Dict[str, Any]] = []
    seen = set()

    for variant in build_language_variants(cleaned_query, language):
        for result in search_youtube(variant, limit=5):
            key = (result.get("title"), result.get("stream_url"))
            if key in seen:
                continue
            seen.add(key)
            combined.append(result)
            if len(combined) >= max_results:
                return combined

        for result in search_generic_public_index(variant, limit=3):
            key = (result.get("title"), result.get("stream_url"))
            if key in seen:
                continue
            seen.add(key)
            combined.append(result)
            if len(combined) >= max_results:
                return combined

    return combined[:max_results]

# -----------------------------
# Web app
# -----------------------------

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

HTML_TEMPLATE = Template("""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
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
      --shadow: rgba(10, 15, 30, 0.6);
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
    .container {
      width: min(1100px, 100%);
    }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 30;
      background: rgba(9, 15, 28, 0.78);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 18px;
      margin-bottom: 16px;
      box-shadow: 0 12px 30px var(--shadow);
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
      width: min(640px, 100%);
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
    select.search-field {
      max-width: 180px;
      flex: 0 0 180px;
    }
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
    .results-head h2 {
      margin: 0;
      font-size: 1.15rem;
      letter-spacing: 0.03em;
    }
    .hint {
      color: var(--muted);
      font-size: 0.92rem;
    }
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
    .card-body {
      padding: 12px 12px 14px;
    }
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
    .modal.show {
      display: grid;
    }
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
          <input id="query" class="search-field" type="text" placeholder="Search Hindi, English, South Indian, Regional..." value="latest hindi movies" />
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
      <h1>Stream Real Media from Public Search Results</h1>
      <p>Search across Hindi, English, South Indian dubbed, and regional titles. Each card contains a native mobile-friendly player, quick playback, and direct download action.</p>
      <div class="status-row">
        <div class="badge"><span class="dot"></span> Ready for Termux / Mobile</div>
        <div class="badge">FastAPI + yt-dlp + HTML5 Player</div>
      </div>
    </section>

    <div class="results-head">
      <h2>Results</h2>
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
          <img class="thumb" src="${thumbnailUrl}" alt="${escapeHtml(item.title || 'Video')}" loading="lazy" />
          <div class="badge-time">${escapeHtml(item.duration || 'N/A')}</div>
        </div>
        <div class="card-body">
          <h3 class="title">${escapeHtml(item.title || 'Untitled')}</h3>
          <div class="meta">
            <span class="pill">${escapeHtml(item.provider || 'Public')}</span>
            <span>${escapeHtml(item.resolution || 'HD')}</span>
          </div>
          <div class="actions">
            <button class="button primary" type="button" data-action="play" data-url="${escapeHtml(streamUrl)}" data-title="${escapeHtml(item.title || 'Video')}">Play</button>
            <button class="button danger" type="button" data-action="download" data-url="${escapeHtml(downloadUrl)}">Download</button>
          </div>
        </div>
      `;

      card.querySelectorAll('button').forEach((button) => {
        button.addEventListener('click', () => {
          const action = button.dataset.action;
          const url = button.dataset.url;
          if (action === 'play') {
            openModal(url, button.dataset.title || 'Video');
          } else if (action === 'download') {
            const target = encodeURIComponent(url);
            window.location.href = `/download?url=${target}`;
          }
        });
      });

      return card;
    }

    function openModal(url, title) {
      modalTitle.textContent = title || 'Stream';
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
      statusEl.textContent = 'Searching…';
      resultsEl.innerHTML = '';

      try {
        const url = `/api/search?q=${encodeURIComponent(query)}&language=${encodeURIComponent(language)}&limit=12`;
        const response = await fetch(url);
        const payload = await response.json();

        const items = payload.results || [];

        if (!items.length) {
          statusEl.textContent = 'No results found.';
          resultsEl.innerHTML = '<div class=\"empty\">No public media matches were found. Try another language or keyword.</div>';
          return;
        }

        statusEl.textContent = `${items.length} result${items.length > 1 ? 's' : ''} found for "${query}"`;
        items.forEach((item) => resultsEl.appendChild(renderCard(item)));
      } catch (error) {
        console.error(error);
        statusEl.textContent = 'Search failed.';
        resultsEl.innerHTML = '<div class=\"empty\">Something went wrong while fetching results. Please try again.</div>';
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

    document.addEventListener('DOMContentLoaded', () => {
      fetchResults('latest hindi movies', 'hindi');
    });
  </script>
</body>
</html>
""")

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    rendered = HTML_TEMPLATE.render(title=APP_TITLE)
    return HTMLResponse(content=rendered)

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "title": APP_TITLE,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "python": sys.version.split()[0],
    }

@app.get("/api/search")
async def search_endpoint(
    q: str = Query(..., description="Search query", min_length=1),
    language: str = Query("all", description="Language filter: all, hindi, english, south indian, regional"),
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

    decoded_url = urllib.parse.unquote(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Referer": "https://www.youtube.com/",
    }

    try:
        response = requests.get(decoded_url, stream=True, timeout=60, headers=headers, allow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch download stream: {exc}")

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
    print(f"[{APP_TITLE}] Starting server on 0.0.0.0:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False, log_level="info")

if __name__ == "__main__":
    main()
