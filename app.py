import os
import sys
import subprocess

# ऑटोमैटिक डिपेंडेंसी इंस्टॉलेशन (बिना किसी मैन्युअल कमांड के)
required_libraries = ['flask', 'requests', 'bs4']
for lib in required_libraries:
    try:
        __import__(lib if lib != 'bs4' else 'bs4')
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

from flask import Flask, render_template_string, request
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# साफ-सुथरा इंग्लिश यूज़र इंटरफेस (Modern Dark Cinematic UI)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Termux Movie Streamer & Scraper</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0d0f12; color: #fff; margin: 0; padding: 0; text-align: center; }
        header { background: #161922; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }
        h1 { color: #ff3366; margin: 0; font-size: 26px; letter-spacing: 1px; }
        .container { padding: 25px; max-width: 1000px; margin: auto; }
        form { margin-bottom: 30px; }
        input[type="text"] { width: 60%; padding: 12px 15px; font-size: 16px; border-radius: 8px; border: none; background: #212530; color: white; outline: none; box-shadow: inset 0 2px 5px rgba(0,0,0,0.3); }
        button { padding: 12px 22px; font-size: 16px; background-color: #ff3366; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; margin-left: 8px; transition: background 0.3s; }
        button:hover { background: #e02e5b; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-top: 20px; text-align: left; }
        .movie-card { background: #161922; border-radius: 12px; overflow: hidden; box-shadow: 0 6px 18px rgba(0,0,0,0.4); display: flex; flex-direction: column; justify-content: space-between; transition: transform 0.3s; }
        .movie-card:hover { transform: translateY(-6px); }
        .movie-card img { width: 100%; height: 280px; object-fit: cover; border-bottom: 2px solid #212530; }
        .movie-info { padding: 15px; }
        .movie-info h3 { margin: 0 0 8px 0; font-size: 17px; color: #fff; }
        .movie-info p { color: #9da3af; font-size: 12px; margin-bottom: 12px; }
        .btn-group { display: flex; gap: 8px; }
        .btn { flex: 1; text-align: center; padding: 8px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 13px; }
        .btn-play { background: #0070f3; color: white; }
        .btn-play:hover { background: #005bb5; }
        .btn-download { background: #28a745; color: white; }
        .btn-download:hover { background: #218838; }
        video { width: 100%; max-width: 800px; border-radius: 10px; margin-top: 20px; box-shadow: 0 8px 25px rgba(0,0,0,0.8); }
        .back-link { display: inline-block; margin-top: 20px; color: #ff3366; text-decoration: none; font-weight: bold; }
        .error-box { background: #2d1519; border: 1px solid #ff3366; padding: 15px; border-radius: 8px; margin-top: 20px; color: #ff99aa; }
    </style>
</head>
<body>
    <header>
        <h1>🎬 CineWeb Scraper Hub</h1>
    </header>
    <div class="container">
        <form action="/search" method="get">
            <input type="text" name="query" placeholder="Search movies (e.g., Spiderman, Avatar...)" required>
            <button type="submit">Search</button>
        </form>

        {% if play_url %}
            <h2>Now Playing: {{ movie_title }}</h2>
            {% if is_playable %}
                <video controls autoplay>
                    <source src="{{ play_url }}" type="video/mp4">
                    Your browser does not support the video tag.
                </video>
            {% else %}
                <div class="error-box">
                    <p>⚠️ Direct playback is restricted or unsupported for this source format.</p>
                    <p>You can use the secure download/stream link below to view it externally:</p>
                    <a href="{{ play_url }}" target="_blank" class="btn btn-download" style="display:inline-block; padding: 10px 20px; margin-top: 10px;">Open External Stream / Download Link</a>
                </div>
            {% endif %}
            <br><a class="back-link" href="/">&larr; Back to Home</a>
        {% else %}
            <h2>{{ heading }}</h2>
            <div class="grid">
                {% for movie in movies %}
                    <div class="movie-card">
                        <img src="{{ movie.thumbnail }}" alt="{{ movie.title }}">
                        <div class="movie-info">
                            <h3>{{ movie.title }}</h3>
                            <p>{{ movie.desc }}</p>
                            <div class="btn-group">
                                <a class="btn btn-play" href="/play?url={{ movie.stream_url }}&title={{ movie.title }}&playable={{ movie.playable }}">Play</a>
                                <a class="btn btn-download" href="{{ movie.stream_url }}" target="_blank">Download</a>
                            </div>
                        </div>
                    </div>
                {% endfor %}
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    # ट्रेंडिंग और नई मूवीज़ (स्पाइडरमैन और अन्य लोकप्रिय कैटालॉग थंबनेल के साथ)
    default_movies = [
        {
            'title': 'Spider-Man: No Way Home',
            'desc': 'Action, Sci-Fi | HD Quality',
            'thumbnail': 'https://image.tmdb.org/t/p/w500/1g0dhYtq4irTY1GPXvft6k4YLjm.jpg',
            'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
            'playable': 'true'
        },
        {
            'title': 'Spider-Man: Across the Spider-Verse',
            'desc': 'Animation, Adventure | Full HD',
            'thumbnail': 'https://image.tmdb.org/t/p/w500/8Vt6mWEReuy4Of61Lnj5Xj704m8.jpg',
            'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4',
            'playable': 'true'
        },
        {
            'title': 'The New Journey Action',
            'desc': 'Thriller, Drama | Latest Release',
            'thumbnail': 'https://image.tmdb.org/t/p/w500/qNBAXBIQlnOThrVvA6mA2B5ggV6.jpg',
            'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/TearsOfSteel.mp4',
            'playable': 'true'
        },
        {
            'title': 'Avengers: Endgame',
            'desc': 'Sci-Fi, Action | Blockbuster',
            'thumbnail': 'https://image.tmdb.org/t/p/w500/or06FN3Dka5tukK1e9sl16pB3iy.jpg',
            'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4',
            'playable': 'true'
        }
    ]
    return render_template_string(HTML_TEMPLATE, movies=default_movies, heading="🔥 Trending & New Movies")

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    
    # यहाँ वेब स्क्रैपिंग लॉजिक काम करेगा जो वेबसाइट से यूज़र के सर्च के हिसाब से मूवी ढूंढेगा और थंबनेल निकालेगा:
    """
    search_url = f"https://example-movie-site.com/search?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(search_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    scraped_movies = []
    for item in soup.find_all('div', class_='movie-item'):
        title = item.find('h2').text
        thumbnail = item.find('img')['src']
        stream_url = item.find('a')['href']
        scraped_movies.append({'title': title, 'thumbnail': thumbnail, 'stream_url': stream_url, 'desc': 'Scraped Result', 'playable': 'true'})
    """

    # खोज परिणाम का डायनामिक डेमो डेटा (थंबनेल के साथ)
    searched_movies = [
        {
            'title': f'Search Results for: {query.title()}',
            'desc': 'Web Scraped High Quality Stream',
            'thumbnail': 'https://image.tmdb.org/t/p/w500/sv1xJUazXeYqALzczSZ3O6nkH75.jpg',
            'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
            'playable': 'true'
        }
    ]
    return render_template_string(HTML_TEMPLATE, movies=searched_movies, heading=f"Search Results for '{query}'")

@app.route('/play')
def play():
    url = request.args.get('url')
    title = request.args.get('title', 'Movie')
    playable = request.args.get('playable', 'true') == 'true'
    return render_template_string(HTML_TEMPLATE, play_url=url, movie_title=title, is_playable=playable)

if __name__ == '__main__':
    print("Server starting successfully. Open http://localhost:8080 in your browser.")
    app.run(host='0.0.0.0', port=8080, debug=False)
