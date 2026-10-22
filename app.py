#!/usr/bin/env python3
"""
Self-contained Flask media streaming dashboard for Termux / Android.

Run:
  python app.py

Then open:
  http://0.0.0.0:5000
"""

import importlib
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

# ------------------------------------------------------------
# 1) Automatic dependency installation
# ------------------------------------------------------------
def ensure_package(module_name, pip_name):
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        print(f"[app] Installing missing dependency: {pip_name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name], stdout=sys.stdout, stderr=sys.stderr)
        return False

ensure_package("flask", "flask")
ensure_package("requests", "requests")
ensure_package("yt_dlp", "yt-dlp")

import requests
import yt_dlp
from flask import Flask, jsonify, render_template_string, request

# ------------------------------------------------------------
# 2) App config
# ------------------------------------------------------------
HOST = "0.0.0.0"
PORT = 5000
TIMEOUT = 12

app = Flask(__name__)

# Public sample MP4 links that are commonly accessible in browsers
SAMPLE_STREAMS = [
    {
        "title": "Sample: Flower",
        "url": "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
        "quality": "SD",
        "source": "sample"
    },
    {
        "title": "Sample: Big Buck Bunny",
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
        "quality": "SD",
        "source": "sample"
    },
    {
        "title": "Sample: Elephant Dream",
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
        "quality": "SD",
        "source": "sample"
    },
    {
        "title": "Sample: For Bigger Joyrides",
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4",
        "quality": "HD",
        "source": "sample"
    },
]

# ------------------------------------------------------------
# 3) Utility helpers
# ------------------------------------------------------------
def clean_title(value, fallback="Media stream"):
    text = (value or fallback).strip()
    return text if text else fallback

def normalize_stream(item):
    if not item:
        return None
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    if not re.match(r"^https?://", url, re.I):
        return None
    if "javascript:" in url.lower() or "data:" in url.lower():
        return None
    return {
        "title": clean_title(item.get("title"), "Media stream"),
        "url": url,
        "quality": item.get("quality") or "Auto",
        "source": item.get("source") or "direct",
    }

def is_media_url(url):
    if not isinstance(url, str):
        return False
    u = url.lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    if "javascript:" in u or "data:" in u:
        return False
    media_exts = [".mp4", ".webm", ".m3u8", ".m4v", ".mov", ".mkv", ".avi", ".3gp", ".mp3"]
    return any(ext in u for ext in media_exts) or "video" in u or "stream" in u or "manifest" in u

def dedupe(items):
    seen = set()
    results = []
    for item in items:
        if not item:
            continue
        url = item.get("url", "")
        if url in seen:
            continue
        seen.add(url)
        results.append(item)
    return results

def safe_request(url, headers=None):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    })
    if headers:
        session.headers.update(headers)
    return session.get(url, timeout=TIMEOUT, allow_redirects=True)

def extract_html_media_links(page_url, html_text):
    items = []
    # Common element attributes
    patterns = [
        r'''(?:src|href|data-src|data-href)\s*[:=]\s*["']([^"']+?)["']''',
        r'''(?:src|href|data-src|data-href)\s*[:=]\s*([^\\s"'>]+)''',
    ]
    found = set()

    for pattern in patterns:
        matches = re.findall(pattern, html_text, flags=re.I)
        for raw in matches:
            candidate = raw.strip()
            if not candidate:
                continue
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            if candidate.startswith("http://") or candidate.startswith("https://"):
                if is_media_url(candidate):
                    found.add(candidate)

    # Filter and add as media links
    for link in sorted(found):
        items.append({
            "title": "Extracted media",
            "url": link,
            "quality": "Auto",
            "source": "page"
        })

    # Also look for "mp4" style explicit text URLs
    for match in re.finditer(r'https?://[^\\s"\'<>]+(?:\\.mp4|\\.m3u8|\\.webm|\\.m4v|\\.mov|\\.mkv|\\.avi)[^\\s"\'<>]*', html_text, re.I):
        candidate = match.group(0)
        if candidate and is_media_url(candidate):
            found.add(candidate)

    for link in sorted(found):
        items.append({
            "title": "Extracted media",
            "url": link,
            "quality": "Auto",
            "source": "page"
        })

    # De-duplicate and keep first 12
    return dedupe(items)[:12]

def extract_from_direct_url(url):
    # If it already points directly to media
    if is_media_url(url):
        return [{
            "title": "Direct stream",
            "url": url,
            "quality": "Auto",
            "source": "direct"
        }]

    try:
        resp = safe_request(url)
        if resp.status_code >= 400:
            return []
        content_type = resp.headers.get("Content-Type", "").lower()
        if "video" in content_type or "audio" in content_type:
            return [{
                "title": "Direct media response",
                "url": url,
                "quality": "Auto",
                "source": "direct"
            }]

        html = resp.text or ""
        links = extract_html_media_links(url, html)
        if links:
            return links
    except Exception:
        pass

    # Last-ditch: look for a url parameter or direct embed
    try:
        resp = safe_request(url)
        html = resp.text or ""
        for pattern in [
            r'''(?:"|')?(https?://[^"'\s<>]+(?:\.mp4|\.m3u8|\.webm|\.m4v|\.mov|\.mkv|\.avi)[^"'\s<>]*)''',
            r'''(?:src|href)\s*[:=]\s*[\"'](https?://[^\"']+)["']'''
        ]:
            matches = re.findall(pattern, html, flags=re.I)
            if matches:
                items = []
                for m in matches:
                    if m.startswith("http"):
                        items.append({
                            "title": "Extracted media",
                            "url": m,
                            "quality": "Auto",
                            "source": "page"
                        })
                return dedupe(items)[:10]
    except Exception:
        pass

    return []

def yt_dlp_streams_from_url(url):
    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return []
        results = []
        entries = []
        if isinstance(info, dict):
            if info.get("_type") == "playlist":
                entries = info.get("entries") or []
            else:
                entries = [info]
        else:
            return []

        for entry in entries:
            if not entry:
                continue
            title = entry.get("title") or "Video"
            quality = "Auto"
            url_candidate = None

            # Prefer best available video url among formats
            formats = entry.get("formats") or []
            if formats:
                ranked = sorted(
                    formats,
                    key=lambda f: (
                        f.get("tbr") or 0,
                        1 if f.get("format_note") == "hd720" else 0
                    ),
                    reverse=True
                )
                for f in ranked:
                    if isinstance(f, dict):
                        candidate = f.get("url")
                        if candidate:
                            url_candidate = candidate
                            quality = f.get("format_note") or f.get("quality") or "Auto"
                            break

            if not url_candidate:
                url_candidate = entry.get("url") or entry.get("webpage_url")

            if url_candidate:
                results.append({
                    "title": title,
                    "url": url_candidate,
                    "quality": quality,
                    "source": "yt-dlp"
                })

        return dedupe(results)[:12]
    except Exception:
        return []

def yt_search_query(query):
    try:
        search_expr = f"ytsearch5:{query}"
        return yt_dlp_streams_from_url(search_expr)
    except Exception:
        return []

# ------------------------------------------------------------
# 4) API routes
# ------------------------------------------------------------
@app.route("/")
def index():
    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Media Stream Dashboard</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: #121a2a;
      --panel-2: #1a2438;
      --text: #edf2ff;
      --muted: #a9b5d1;
      --accent: #7c9cff;
      --accent-2: #5de2b0;
      --danger: #ff7b7b;
      --border: rgba(255,255,255,0.08);
      --shadow: 0 14px 40px rgba(0,0,0,0.25);
    }

    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100%;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: linear-gradient(180deg, #090d17 0%, #101827 100%);
      color: var(--text);
    }
    body {
      display: flex;
      justify-content: center;
      padding: 20px;
    }
    .app {
      width: min(1100px, 100%);
    }
    .header {
      background: rgba(18,26,42,0.9);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 18px 18px 10px;
      margin-bottom: 18px;
      backdrop-filter: blur(10px);
    }
    .title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .brand {
      font-size: clamp(1.2rem, 2vw, 1.8rem);
      font-weight: 700;
      letter-spacing: 0.02em;
    }
    .badge {
      background: rgba(124, 156, 255, 0.15);
      border: 1px solid rgba(124,156,255,0.3);
      color: var(--accent);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 600;
    }

    .search-wrap {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    input[type="text"] {
      flex: 1 1 520px;
      min-width: 200px;
      background: rgba(255,255,255,0.04);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
      outline: none;
      font-size: 1rem;
    }
    input[type="text"]::placeholder { color: var(--muted); }
    button {
      border: none;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--accent), #9b8cff);
      color: white;
      padding: 16px 22px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 8px 20px rgba(124,156,255,0.35);
    }
    button.secondary {
      background: rgba(255,255,255,0.06);
      border: 1px solid var(--border);
      box-shadow: none;
      color: var(--text);
    }
    .status {
      min-height: 22px;
      color: var(--muted);
      font-size: 0.92rem;
      margin-top: 10px;
    }
    .content {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(290px, 0.9fr);
      gap: 18px;
    }
    .panel {
      background: rgba(18,26,42,0.9);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .video-panel {
      padding: 12px;
    }
    video {
      width: 100%;
      height: clamp(260px, 58vw, 520px);
      background: #000;
      border-radius: 14px;
      display: block;
      object-fit: contain;
    }
    .meta {
      padding: 16px 18px 4px;
    }
    .meta h2 {
      margin: 0 0 6px;
      font-size: 1.08rem;
    }
    .meta .small {
      color: var(--muted);
      font-size: 0.9rem;
    }

    .stream-panel {
      display: flex;
      flex-direction: column;
      min-height: 200px;
    }
    .stream-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 18px;
      border-bottom: 1px solid var(--border);
      background: rgba(255,255,255,0.02);
    }
    .stream-header h3 {
      margin: 0;
      font-size: 1rem;
    }
    .stream-list {
      list-style: none;
      margin: 0;
      padding: 10px;
      overflow-y: auto;
      max-height: 540px;
    }
    .stream-item {
      background: rgba(255,255,255,0.025);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 12px;
      margin-bottom: 10px;
      cursor: pointer;
      transition: 0.2s ease;
    }
    .stream-item:hover {
      background: rgba(255,255,255,0.04);
      transform: translateY(-1px);
    }
    .stream-item.active {
      border-color: rgba(124,156,255,0.9);
      box-shadow: inset 0 0 0 1px rgba(124,156,255,0.5);
      background: rgba(124,156,255,0.08);
    }
    .stream-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }
    .stream-title {
      font-weight: 700;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }
    .pill {
      background: rgba(93,226,176,0.12);
      color: var(--accent-2);
      padding: 4px 8px;
      font-size: 11px;
      border-radius: 999px;
      border: 1px solid rgba(93,226,176,0.25);
      white-space: nowrap;
    }
    .stream-url {
      color: var(--muted);
      font-size: 0.8rem;
      overflow-wrap: anywhere;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      text-align: center;
    }

    @media (max-width: 820px) {
      .content {
        grid-template-columns: 1fr;
      }
      .stream-list {
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="header">
      <div class="title-row">
        <div class="brand">Media Stream Dashboard</div>
        <div class="badge">Android / Termux</div>
      </div>

      <form id="searchForm">
        <div class="search-wrap">
          <input id="queryInput" type="text" autocomplete="off" placeholder="Paste a media page URL or search query (YouTube, direct MP4, etc.)" />
          <button type="submit">Search</button>
          <button type="button" class="secondary" id="sampleBtn">Load sample streams</button>
        </div>
      </form>
      <div id="statusText" class="status">Ready to search.</div>
    </div>

    <div class="content">
      <div class="panel video-panel">
        <video id="player" controls playsinline preload="auto"></video>
        <div class="meta">
          <h2 id="currentTitle">No stream selected</h2>
          <div id="currentSource" class="small">Choose a stream from the list</div>
        </div>
      </div>

      <div class="panel stream-panel">
        <div class="stream-header">
          <h3>Available streams</h3>
        </div>
        <ul id="streamList" class="stream-list">
          <li class="empty">No streams yet. Search for a URL or use “Load sample streams”.</li>
        </ul>
      </div>
    </div>
  </div>

  <script>
    const streamList = document.getElementById('streamList');
    const statusText = document.getElementById('statusText');
    const player = document.getElementById('player');
    const currentTitle = document.getElementById('currentTitle');
    const currentSource = document.getElementById('currentSource');

    let currentStreams = [];

    function setStatus(message, isError = false) {
      statusText.textContent = message;
      statusText.style.color = isError ? '#ff7b7b' : '#a9b5d1';
    }

    function renderStreams(streams) {
      streamList.innerHTML = '';
      currentStreams = streams || [];

      if (!currentStreams.length) {
        streamList.innerHTML = '<li class=\"empty\">No streams found. Try a different URL or search query.</li>';
        return;
      }

      currentStreams.forEach((stream, index) => {
        const item = document.createElement('li');
        item.className = 'stream-item' + (index === 0 ? ' active' : '');
        item.innerHTML = `
          <div class="stream-top">
            <div class="stream-title">${escapeHtml(stream.title || 'Media stream')}</div>
            <span class="pill">${escapeHtml(stream.quality || 'Auto')}</span>
          </div>
          <div class="stream-url">${escapeHtml(stream.url)}</div>
        `;
        item.addEventListener('click', () => {
          selectStream(stream, item);
        });
        streamList.appendChild(item);
      });

      const first = currentStreams[0];
      const firstItem = streamList.children[0];
      if (first && firstItem) {
        selectStream(first, firstItem);
      }
    }

    function selectStream(stream, itemNode) {
      const allItems = document.querySelectorAll('.stream-item');
      allItems.forEach((node) => node.classList.remove('active'));
      if (itemNode) itemNode.classList.add('active');

      currentTitle.textContent = stream.title || 'Media stream';
      currentSource.textContent = 'Source: ' + (stream.source || 'direct');

      try {
        player.src = stream.url;
        player.load();
        player.play().catch(() => {
          // Autoplay may be blocked; allow user to press play manually.
        });
      } catch (err) {
        // ignore
      }
    }

    function escapeHtml(str) {
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    async function searchMedia(value) {
      const cleaned = (value || '').trim();
      if (!cleaned) {
        setStatus('Please enter a URL or search query.', true);
        return;
      }

      setStatus('Searching and extracting media streams...');
      try {
        const res = await fetch('/api/extract', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: cleaned })
        });

        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.error || 'Unable to find streams.');
        }

        if (!data.media || !data.media.length) {
          renderStreams([]);
          setStatus('No media streams were found for that input.', true);
          return;
        }

        renderStreams(data.media);
        setStatus(`Found ${data.media.length} stream${data.media.length > 1 ? 's' : ''}.`);
      } catch (err) {
        renderStreams([]);
        setStatus(err.message || 'Search failed.', true);
      }
    }

    document.getElementById('searchForm').addEventListener('submit', (e) => {
      e.preventDefault();
      searchMedia(document.getElementById('queryInput').value);
    });

    document.getElementById('sampleBtn').addEventListener('click', async () => {
      setStatus('Loading sample streams...');
      try {
        const res = await fetch('/api/samples', { method: 'GET' });
        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.error || 'Could not load sample streams.');
        }
        renderStreams(data.media);
        setStatus(`Loaded ${data.media.length} sample streams.`);
      } catch (err) {
        setStatus(err.message || 'Unable to load sample streams.', true);
      }
    });

    // Load sample streams by default
    fetch('/api/samples')
      .then((res) => res.json())
      .then((data) => {
        if (data && data.success && data.media && data.media.length) {
          renderStreams(data.media);
        }
      })
      .catch(() => {
        // Ignore default load failure; UI still works
      });
  </script>
</body>
</html>
    """)

@app.route("/api/samples")
def samples():
    return jsonify({"success": True, "media": SAMPLE_STREAMS})

@app.route("/api/extract", methods=["POST"])
def extract_media():
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "Please provide a URL or search query."}), 400

    normalized = query.strip()
    inferred_url = re.match(r"^https?://", normalized, re.I) is not None

    results = []

    # 1) Direct URL: check if it is itself a media URL or page with media embeds
    if inferred_url:
        # Try direct page extraction first
        page_results = extract_from_direct_url(normalized)
        if page_results:
            results.extend(page_results)

        # Try YouTube / extractable platform URLs
        yt_results = yt_dlp_streams_from_url(normalized)
        if yt_results:
            results.extend(yt_results)

        # If still empty, try a sample fallback to keep UI usable
        if not results:
            results = SAMPLE_STREAMS[:3]

    # 2) Search query: use yt-dlp search
    else:
        results = yt_search_query(normalized)
        if not results:
            # Lightweight fallback: sample streams if search is generic
            results = SAMPLE_STREAMS[:4]

    if not results:
        return jsonify({"success": False, "error": "No playable media streams were found for that input."}), 404

    cleaned = []
    for item in results:
        stream = normalize_stream(item)
        if stream:
            cleaned.append(stream)

    cleaned = dedupe(cleaned)[:12]
    if not cleaned:
        return jsonify({"success": False, "error": "Media discovery returned unusable streams."}), 404

    return jsonify({"success": True, "media": cleaned, "query": query})

# ------------------------------------------------------------
# 5) Run app
# ------------------------------------------------------------
if __name__ == "__main__":
    print(f"[app] Starting Flask media dashboard on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)