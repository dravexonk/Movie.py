#!/usr/bin/env python3
"""
Self-contained Flask media streaming dashboard for Termux / Android.
Features:
- Single-command startup: python app.py
- Auto-installs Flask, requests, yt-dlp if missing
- Accepts a media URL or search query
- Extracts media streams via yt-dlp
- Modern dark UI with HTML5 video player
- No complex folder structure required
"""

import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List

try:
    import flask
    from flask import Flask, jsonify, render_template_string, request
    import requests
    import yt_dlp
except Exception:
    print("[INFO] Installing required dependencies...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "Flask", "requests", "yt-dlp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "Flask", "requests", "yt-dlp"]
        )

    import flask
    from flask import Flask, jsonify, render_template_string, request
    import requests
    import yt_dlp

app = Flask(__name__)

DEFAULT_PORT = 5000
HOST = "0.0.0.0"

def is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value.strip(), re.I))

def safe_text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) and value.strip() else default

def normalize_streams(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    stream_map: Dict[str, Dict[str, Any]] = {}

    def add_stream(url: str, title: str, quality: str = "stream", kind: str = "media", mime: str = ""):
        if not url or not url.startswith(("http://", "https://")):
            return
        key = f"{title}|{url}"
        if key not in stream_map:
            stream_map[key] = {
                "title": title,
                "url": url,
                "quality": quality,
                "kind": kind,
                "mime": mime,
            }

    entries = []
    if not isinstance(info, dict):
        return []

    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
    elif info.get("entries") is not None:
        entries = info.get("entries") or []
    else:
        entries = [info]

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        title = safe_text(entry.get("title"), "Untitled stream")
        thumbnail = safe_text(entry.get("thumbnail"), "")
        direct_url = safe_text(entry.get("url"), "")
        if direct_url:
            add_stream(direct_url, title, "direct", "media", safe_text(entry.get("ext"), "video/mp4"))
            if thumbnail:
                pass

        formats = entry.get("formats") or []
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            fmt_url = safe_text(fmt.get("url"), "")
            if not fmt_url:
                continue
            fmt_id = safe_text(fmt.get("format_id"), "stream")
            format_note = safe_text(fmt.get("format_note"), "")
            quality = format_note or fmt_id
            vcodec = safe_text(fmt.get("vcodec"), "")
            acodec = safe_text(fmt.get("acodec"), "")
            if vcodec and vcodec.lower() != "none":
                kind = "video"
            elif acodec and acodec.lower() != "none":
                kind = "audio"
            else:
                kind = "media"
            ext = safe_text(fmt.get("ext"), "mp4")
            mime = f"video/{ext}" if kind in ("video", "media") else f"audio/{ext}"
            add_stream(fmt_url, f"{title} ({quality})", quality, kind, mime)

        # If there are no formats but there are URLs inside `requested_formats`, try them
        requested_formats = entry.get("requested_formats") or []
        for fmt in requested_formats:
            if not isinstance(fmt, dict):
                continue
            fmt_url = safe_text(fmt.get("url"), "")
            if fmt_url:
                add_stream(fmt_url, f"{title} (requested)", "requested", "media", "video/mp4")

    # Sort a little to prefer video streams first
    def sort_key(item):
        raw = item["kind"]
        kind_rank = {"video": 0, "media": 1, "audio": 2}
        return (kind_rank.get(raw, 99), item["quality"])

    streams = sorted(stream_map.values(), key=sort_key)
    return streams[:20]

def build_error(message: str):
    return {"error": message, "title": "Error", "streams": []}

def extract_media_from_query(query: str) -> Dict[str, Any]:
    raw_query = (query or "").strip()
    if not raw_query:
        raise ValueError("Please provide a URL or a search term.")

    if is_url(raw_query):
        return extract_media_from_url(raw_query)

    # Search YouTube using yt-dlp. This is light and works well for searching.
    search_term = f"ytsearch5:{raw_query}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_term, download=False)

    results = []
    entries = []
    if isinstance(info, dict):
        entries = info.get("entries") or []
    else:
        entries = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = safe_text(entry.get("title"), "Search result")
        webpage_url = safe_text(entry.get("webpage_url"), "")
        thumbnail = safe_text(entry.get("thumbnail"), "")
        if not webpage_url:
            continue
        results.append({
            "title": title,
            "url": webpage_url,
            "thumbnail": thumbnail,
            "source": "youtube-search",
        })

    # If search results are empty, try direct page extraction as last resort
    if not results:
        return {
            "title": f"Search results for: {raw_query}",
            "thumbnail": "",
            "streams": [],
            "results": [],
        }

    # For search results list, we don't want to display every URL as a stream by default.
    # We can display them as clickable entries and then let the user open them if needed.
    compact_streams = []
    for item in results[:8]:
        compact_streams.append({
            "title": item["title"],
            "url": item["url"],
            "quality": "webpage",
            "kind": "media",
            "mime": "video/mp4",
        })

    return {
        "title": f"Search results for: {raw_query}",
        "thumbnail": results[0].get("thumbnail", ""),
        "streams": compact_streams,
        "results": results,
    }

def extract_media_from_url(url: str) -> Dict[str, Any]:
    target = url.strip()
    if not target:
        raise ValueError("URL is empty.")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 2,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as exc:
        # Fallback: try to treat it as a direct media file or basic HTML page
        try:
            resp = requests.get(target, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                raise ValueError(f"Request failed ({resp.status_code}) for {target}")
            content_type = resp.headers.get("Content-Type", "").lower()
            if "video" in content_type or "audio" in content_type or target.lower().endswith((".mp4", ".m3u8", ".mp3", ".webm", ".m4a", ".m4v")):
                return {
                    "title": "Direct media URL",
                    "thumbnail": "",
                    "streams": [{
                        "title": "Direct media stream",
                        "url": target,
                        "quality": "direct",
                        "kind": "media",
                        "mime": content_type or "video/mp4",
                    }],
                    "results": [],
                }
            raise ValueError(f"Could not extract playable media from: {target}") from exc
        except Exception:
            raise ValueError(f"Could not extract playable media from: {target}") from exc

    title = safe_text((info or {}).get("title"), "Extracted media")
    streams = normalize_streams(info)

    if not streams:
        # Try a smart fallback from page source if the site exposes a single direct html video
        try:
            r = requests.get(target, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            text = r.text or ""
            candidates = re.findall(r'https?://[^\\s"\\\']+\\.(?:mp4|m3u8|webm|m4v|mp3|m4a)(?:\\?[^\\s"\\']*)?', text, re.I)
            if candidates:
                unique = []
                seen = set()
                for c in candidates[:10]:
                    if c not in seen:
                        unique.append(c)
                        seen.add(c)
                streams = [{
                    "title": f"Detected media {idx + 1}",
                    "url": c,
                    "quality": "detected",
                    "kind": "media",
                    "mime": "video/mp4",
                } for idx, c in enumerate(unique)]
        except Exception:
            pass

    if not streams:
        raise ValueError(f"No playable streams were found for {target}. Try a public video page or a direct .mp4 URL.")

    return {
        "title": title,
        "thumbnail": safe_text((info or {}).get("thumbnail"), ""),
        "streams": streams,
        "results": [],
    }


HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Media Stream Browser</title>
  <style>
    :root {
      --bg: #0f1117;
      --bg-2: #171b24;
      --panel: #1d2330;
      --panel-alt: #212a37;
      --line: rgba(255,255,255,0.08);
      --text: #edf3ff;
      --muted: #abb5c8;
      --primary: #5db4ff;
      --primary-2: #7f73ff;
      --success: #5dd19c;
      --danger: #ff7d7d;
      --shadow: rgba(0,0,0,0.30);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      background: radial-gradient(circle at top, #1a2232 0%, #0f1117 55%);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      min-height: 100%;
    }
    body {
      display: flex;
      justify-content: center;
      padding: 18px;
    }
    .app {
      width: min(1200px, 100%);
      background: rgba(10, 12, 18, 0.72);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 40px var(--shadow);
      overflow: hidden;
      backdrop-filter: blur(10px);
    }
    .topbar {
      padding: 18px 18px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.02);
    }
    .search-form {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }
    .search-box {
      flex: 1 1 420px;
      min-width: 180px;
      background: #121722;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px 14px;
      color: var(--text);
      font-size: 1rem;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    .search-box:focus {
      border-color: rgba(93,180,255,0.8);
      box-shadow: 0 0 0 4px rgba(93,180,255,0.13);
    }
    .primary-btn {
      border: 0;
      border-radius: 14px;
      background: linear-gradient(135deg, var(--primary), var(--primary-2));
      color: white;
      padding: 14px 18px;
      font-weight: 700;
      cursor: pointer;
      transition: transform 0.15s ease, filter 0.2s ease;
    }
    .primary-btn:hover { transform: translateY(-1px); filter: brightness(1.05); }
    .content {
      display: grid;
      grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
      min-height: 760px;
    }
    .sidebar {
      background: rgba(21, 26, 36, 0.9);
      border-right: 1px solid var(--line);
      padding: 18px 14px 10px;
    }
    .panel-title {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      padding: 0 6px;
    }
    .results {
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-height: 680px;
      overflow-y: auto;
      padding-right: 2px;
    }
    .result-card {
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      cursor: pointer;
      transition: border-color 0.15s ease, transform 0.15s ease;
    }
    .result-card:hover {
      border-color: rgba(93,180,255,0.6);
      transform: translateY(-1px);
    }
    .result-card.active {
      border-color: rgba(93,180,255,0.85);
      background: linear-gradient(180deg, rgba(93,180,255,0.08), rgba(255,255,255,0.01));
    }
    .thumb {
      width: 100%;
      height: 110px;
      border-radius: 10px;
      background: linear-gradient(135deg, #1d2433, #0f1117);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: 0.82rem;
      overflow: hidden;
      border: 1px solid var(--line);
      margin-bottom: 8px;
      background-size: cover;
      background-position: center;
    }
    .thumb.empty {
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      background: linear-gradient(135deg, #24314a, #101722);
    }
    .item-title {
      font-size: 0.96rem;
      line-height: 1.35;
      font-weight: 700;
      margin: 0 0 4px;
      color: var(--text);
    }
    .item-meta {
      color: var(--muted);
      font-size: 0.72rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .player-panel {
      display: flex;
      flex-direction: column;
      padding: 18px;
      background: rgba(12, 15, 22, 0.9);
    }
    .video-wrap {
      background: #000;
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 16px 26px rgba(0,0,0,0.35);
    }
    video {
      display: block;
      width: 100%;
      max-height: 65vh;
      background: #000;
      outline: none;
    }
    .status {
      margin-top: 14px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.02);
      color: var(--muted);
      font-size: 0.92rem;
      min-height: 48px;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
    }
    .status.success { color: var(--success); border-color: rgba(93,209,156,0.35); }
    .status.error { color: var(--danger); border-color: rgba(255,125,125,0.3); }
    .small-note {
      color: var(--muted);
      font-size: 0.8rem;
      margin-top: 10px;
    }
    @media (max-width: 860px) {
      .content {
        grid-template-columns: 1fr;
      }
      .sidebar {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .results {
        max-height: 360px;
      }
      body { padding: 10px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="topbar">
      <form id="searchForm" class="search-form">
        <input
          id="queryInput"
          class="search-box"
          type="text"
          placeholder="Paste a media URL or type a search term (YouTube, etc.)"
          autocomplete="off"
        />
        <button class="primary-btn" type="submit">Search</button>
      </form>
    </div>

    <div class="content">
      <aside class="sidebar">
        <h3 class="panel-title">Media Streams</h3>
        <div id="results" class="results"></div>
      </aside>

      <section class="player-panel">
        <div class="video-wrap">
          <video id="player" controls playsinline preload="metadata"></video>
        </div>
        <div id="status" class="status">Choose a stream or search for media to begin.</div>
        <div class="small-note">
          Tip: paste a direct .mp4 URL or a public video page URL. Search queries use YouTube search via yt-dlp.
        </div>
      </section>
    </div>
  </div>

  <script>
    const player = document.getElementById('player');
    const resultsEl = document.getElementById('results');
    const statusEl = document.getElementById('status');
    const form = document.getElementById('searchForm');
    const input = document.getElementById('queryInput');

    const sampleVideos = [
      {
        title: 'Big Buck Bunny',
        url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
        kind: 'video',
        quality: 'sample',
        mime: 'video/mp4'
      },
      {
        title: 'Elephant Dream',
        url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4',
        kind: 'video',
        quality: 'sample',
        mime: 'video/mp4'
      },
      {
        title: 'For Bigger Blazes',
        url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4',
        kind: 'video',
        quality: 'sample',
        mime: 'video/mp4'
      }
    ];

    function setStatus(message, success = false, error = false) {
      statusEl.textContent = message;
      statusEl.classList.remove('success', 'error');
      if (success) statusEl.classList.add('success');
      if (error) statusEl.classList.add('error');
    }

    function createThumb(item) {
      if (item?.thumbnail) {
        return `style="background-image: url('${item.thumbnail}'); background-size: cover; background-position: center;"`;
      }
      return 'class="thumb empty"';
    }

    function renderResults(items) {
      if (!Array.isArray(items) || items.length === 0) {
        resultsEl.innerHTML = '<div class=\"result-card\"><div class=\"item-title\">No streams found</div><div class=\"item-meta\">Try a direct video link</div></div>';
        return;
      }

      const cards = items.map((item, index) => {
        const title = item.title || 'Untitled';
        const quality = item.quality || 'stream';
        const kind = item.kind || 'media';
        const thumbMarkup = item.thumbnail
          ? `<div class="thumb" ${createThumb(item).replace(/^style=|^class=\"thumb empty\"/,'').replace(/style=\"/, 'style=\"')}> </div>`
          : `<div class="thumb empty">MEDIA</div>`;

        return `
          <div class="result-card" data-index="${index}" data-url="${item.url}" data-title="${title.replace(/"/g, '&quot;')}">
            ${thumbMarkup}
            <div class="item-title">${title}</div>
            <div class="item-meta">${kind.toUpperCase()} • ${quality}</div>
          </div>
        `;
      }).join('');

      resultsEl.innerHTML = cards;

      resultsEl.querySelectorAll('.result-card').forEach(card => {
        card.addEventListener('click', () => {
          const url = card.dataset.url;
          const title = card.dataset.title;
          if (!url) return;
          player.src = url;
          player.load();
          try { player.play(); } catch (e) {}
          setStatus(`Playing: ${title}`, true, false);
          resultsEl.querySelectorAll('.result-card').forEach(c => c.classList.remove('active'));
          card.classList.add('active');
        });
      });
    }

    function renderSampleVideos() {
      renderResults(sampleVideos);
      setStatus('Sample videos loaded. Pick one to preview.', true, false);
      const first = resultsEl.querySelector('.result-card');
      if (first) {
        first.classList.add('active');
        const url = first.dataset.url;
        const title = first.dataset.title;
        player.src = url;
        player.load();
        try { player.play(); } catch (e) {}
        setStatus(`Playing sample: ${title}`, true, false);
      }
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const query = input.value.trim();
      if (!query) {
        renderSampleVideos();
        return;
      }

      setStatus('Fetching streams...', false, false);

      try {
        const response = await fetch('/api/extract', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query })
        });

        const data = await response.json();

        if (!response.ok || data.error) {
          throw new Error(data.error || 'Unknown error');
        }

        const streams = Array.isArray(data.streams) ? data.streams : [];
        if (streams.length === 0) {
          renderSampleVideos();
          setStatus('No streams found for that query. Try a direct URL or another search term.', false, true);
          return;
        }

        renderResults(streams);
        const first = resultsEl.querySelector('.result-card');
        if (first) {
          first.classList.add('active');
          const url = first.dataset.url;
          const title = first.dataset.title;
          player.src = url;
          player.load();
          try { player.play(); } catch (e) {}
          setStatus(`Loaded ${streams.length} stream(s). Playing: ${title}`, true, false);
        } else {
          setStatus('Loaded stream list.', true, false);
        }
      } catch (error) {
        renderSampleVideos();
        setStatus(error.message || 'Failed to load media.', false, true);
      }
    });

    // Initial load
    renderSampleVideos();
  </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/api/extract", methods=["GET", "POST"])
def api_extract():
    try:
        if request.method == "GET":
            query = request.args.get("query", "").strip()
        else:
            payload = request.get_json(silent=True) or {}
            query = (payload.get("query") or "").strip()

        if not query:
            return jsonify(build_error("Please provide a media URL or search query.")), 400

        data = extract_media_from_query(query)
        if "error" in data:
            return jsonify(data), 400

        return jsonify(data), 200
    except Exception as exc:
        return jsonify(build_error(str(exc))), 400

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "media-stream-browser"}), 200

if __name__ == "__main__":
    print("=" * 60)
    print("Media Stream Browser")
    print(f"Running on http://{HOST}:{DEFAULT_PORT}")
    print("Press Ctrl+C to stop.")
    print("=" * 60)
    app.run(host=HOST, port=DEFAULT_PORT, debug=False, threaded=True)
