import os
import sys
import subprocess

# 1. ऑटोमैटिक डिपेंडेंसी चेक और इंस्टॉलेशन
required_libraries = ['flask', 'requests', 'bs4']
for lib in required_libraries:
    try:
        __import__(lib if lib != 'bs4' else 'bs4')
    except ImportError:
        print(f"[+] आवश्यक लाइब्रेरी '{lib}' इंस्टॉल की जा रही है...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

from flask import Flask, render_template_string, request
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# 2. स्टाइलिश और मॉडर्न फुल-व्यू UI टेम्पलेट (Dark Theme)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Termux Ultimate Movie Streamer</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f1016; color: #fff; margin: 0; padding: 0; text-align: center; }
        header { background: #1a1c23; padding: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }
        h1 { color: #e50914; margin: 0; font-size: 28px; }
        .container { padding: 20px; max-width: 900px; margin: auto; }
        form { margin-bottom: 25px; }
        input[type="text"] { width: 65%; padding: 12px; font-size: 16px; border-radius: 6px; border: none; background: #2b2e3b; color: white; outline: none; }
        button { padding: 12px 20px; font-size: 16px; background-color: #e50914; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; }
        button:hover { background: #b20710; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; margin-top: 15px; text-align: left; }
        .movie-card { background: #1a1c23; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.3s; }
        .movie-card:hover { transform: translateY(-5px); }
        .movie-info { padding: 15px; }
        .movie-info h3 { margin: 0 0 8px 0; font-size: 18px; color: #fff; }
        .movie-info p { color: #a0a0a0; font-size: 13px; margin-bottom: 15px; }
        .btn-play { display: block; background: #0084ff; color: white; text-align: center; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; }
        .btn-play:hover { background: #006ae6; }
        video { width: 100%; max-width: 800px; border-radius: 10px; margin-top: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.8); }
        .back-btn { display: inline-block; margin-top: 15px; color: #e50914; text-decoration: none; font-weight: bold; }
    </style>
</head>
<body>
    <header>
        <h1>🎬 Termux Movie Hub</h1>
    </header>
    <div class="container">
        <form action="/search" method="get">
            <input type="text" name="query" placeholder="मूवी खोजें (जैसे: Spiderman, Action...)" required>
            <button type="submit">खोजें</button>
        </form>

        {% if play_url %}
            <h2>चल रही मूवी: {{ movie_title }}</h2>
            <video controls autoplay>
                <source src="{{ play_url }}" type="video/mp4">
                आपका ब्राउज़र वीडियो टैग को सपोर्ट नहीं करता।
            </video>
            <br><a class="back-btn" href="/">&larr; होम पेज पर वापस जाएं</a>
        {% else %}
            <h2>🔥 Trending & New Movies</h2>
            <div class="grid">
                {% for movie in movies %}
                    <div class="movie-card">
                        <div class="movie-info">
                            <h3>{{ movie.title }}</h3>
                            <p>{{ movie.desc }}</p>
                            <a class="btn-play" href="/play?url={{ movie.stream_url }}&title={{ movie.title }}">ऑनलाइन देखें (Play)</a>
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
    # ट्रेंडिंग और नई मूवीज़ (स्पाइडरमैन और अन्य लोकप्रिय कैटालॉग)
    default_movies = [
        {'title': 'Spiderman: No Way Home', 'desc': 'Action, Sci-Fi | Latest Blockbuster HD', 'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4'},
        {'title': 'Spider-Man: Across the Spider-Verse', 'desc': 'Animation, Adventure, Sci-Fi', 'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4'},
        {'title': 'The New Journey (Action HD)', 'desc': 'Thriller, Drama | Full HD Print', 'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/TearsOfSteel.mp4'},
        {'title': 'Avengers: Endgame Special', 'desc': 'Sci-Fi, Action | Full HD Stream', 'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4'}
    ]
    return render_template_string(HTML_TEMPLATE, movies=default_movies)

@app.route('/search')
def search():
    query = request.args.get('query', '').lower()
    
    # यहाँ आप चाहें तो BeautifulSoup का उपयोग करके किसी एक्सटर्नल साइट से लाइव वेब स्क्रैपिंग भी कर सकते हैं
    # उदाहरण के लिए स्क्रैपिंग लॉजिक का स्ट्रक्चर:
    """
    url = f"https://example-movie-site.com/search?q={query}"
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(response.text, 'html.parser')
    # लूप चलाकर रिजल्ट्स निकालें...
    """

    # खोज परिणाम का डेमो डेटा
    searched_movies = [
        {'title': f'Result for: {query.capitalize()}', 'desc': 'High Speed Web Stream Available', 'stream_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4'}
    ]
    return render_template_string(HTML_TEMPLATE, movies=searched_movies)

@app.route('/play')
def play():
    url = request.args.get('url')
    title = request.args.get('title', 'Movie')
    return render_template_string(HTML_TEMPLATE, play_url=url, movie_title=title)

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 सर्वर सफलतापूर्वक तैयार हो रहा है...")
    print("🌐 अपने ब्राउज़र में खोलें: http://localhost:8080")
    print("="*50 + "\n")
    app.run(host='0.0.0.0', port=8080, debug=False)
