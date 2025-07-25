# from flask import Flask, jsonify

# app = Flask(__name__)

# @app.route('/api/hello', methods=['GET'])
# def hello():
#     return jsonify({'message': 'Hello, World!'})

# if __name__ == '__main__':
#     app.run(debug=True)

# ================ test 2 ================
# from flask import Flask, jsonify
# from scrapper import scrape_data  # Assuming this is the function you want to use

# app = Flask(__name__)

# @app.route('/api/scrape', methods=['GET'])
# def scrape_api():
#     try:
#         data = scrape_data()  # Call the scraping function
#         return jsonify({'status': 'success', 'data': data})
#     except Exception as e:
#         return jsonify({'status': 'error', 'message': str(e)}), 500

# if __name__ == '__main__':
#     app.run(debug=True)



# ====================== test 3 ================
from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
import pytz
import re
import hashlib
import logging

app = Flask(__name__)

# Configure logging (console only for API simplicity)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
}

CATEGORY_URLS = [
    "https://www.dhakapost.com/latest-news",
    "https://www.dhakapost.com/bangladesh",
    "https://www.dhakapost.com/world",
    "https://www.dhakapost.com/sports",
    "https://www.dhakapost.com/entertainment"
]
PAGINATION_PATTERN = "?page={}"
MIN_ARTICLES = 10  # limit for API call, can increase

def get_bangladesh_time():
    bd_tz = pytz.timezone('Asia/Dhaka')
    return datetime.now(bd_tz)

def is_today_or_yesterday(date_str):
    try:
        today = get_bangladesh_time().date()
        yesterday = today - timedelta(days=1)
        date_formats = [
            '%Y-%m-%d',
            '%d %B, %Y',
            '%d %b, %Y',
            '%d %B %Y',
            '%B %d, %Y',
            '%d/%m/%Y',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%fZ',
        ]
        clean_date = date_str.strip()
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(clean_date, fmt).date()
                return parsed_date == today or parsed_date == yesterday
            except ValueError:
                continue

        lower_date = clean_date.lower()
        today_indicators = ['today', 'just now', 'minutes ago', 'hours ago']
        yesterday_indicators = ['yesterday']
        return any(ind in lower_date for ind in today_indicators + yesterday_indicators)
    except Exception:
        return True

def get_article_links_from_page(url):
    logger.info(f"Fetching article links from {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        article_links = []
        article_containers = soup.select('.card, .news-item, article, .list-item, .news-card, .news-list, .article-list')
        if not article_containers:
            article_containers = soup.select('div[class*="news"], div[class*="article"], div[class*="post"], a[href*="/news/"]')
        if not article_containers:
            article_containers = [soup]

        for container in article_containers:
            links = container.find_all('a')
            for link in links:
                href = link.get('href')
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue
                article_indicators = ['/news/', '/article/', '/story/', '/latest-news/',
                                      '/bangladesh/', '/world/', '/sports/', '/entertainment/']
                if any(ind in href for ind in article_indicators):
                    full_url = urljoin(url, href)
                    if full_url not in article_links:
                        article_links.append(full_url)
        return article_links
    except Exception as e:
        logger.error(f"Error fetching article links from {url}: {e}")
        return []

def get_article_links():
    all_links = []
    for category_url in CATEGORY_URLS:
        links = get_article_links_from_page(category_url)
        all_links.extend(links)
        if len(set(all_links)) < MIN_ARTICLES * 2:
            for page in range(2, 4):
                paginated_url = f"{category_url}{PAGINATION_PATTERN.format(page)}"
                page_links = get_article_links_from_page(paginated_url)
                if not page_links:
                    break
                all_links.extend(page_links)
    return list(set(all_links))

def extract_article_content(url):
    logger.info(f"Extracting article from {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Title extraction
        title = None
        for sel in ['h1', '.article-title', '.news-title', '.title', '.headline', '.entry-title']:
            els = soup.select(sel)
            if els and els[0].text.strip():
                title = els[0].text.strip()
                break
        if not title:
            return None

        # Date extraction
        date_text = None
        for sel in ['time', '.date', '.published-date', '.article-date', '[itemprop="datePublished"]']:
            els = soup.select(sel)
            if els:
                dt = els[0].get('datetime')
                if dt:
                    date_text = dt
                    break
                text = els[0].text.strip()
                if text:
                    date_text = text
                    break
        if not date_text:
            date_text = ""

        if not is_today_or_yesterday(date_text):
            logger.info(f"Article date not today or yesterday: {date_text}")
            # To keep articles recent, but still return anyway
            # return None

        # Content extraction
        content = ""
        for sel in ['article p', '.article-body p', '.content p', '#content p', '.news-content p']:
            paras = soup.select(sel)
            if paras:
                content = "\n\n".join(p.text.strip() for p in paras if len(p.text.strip()) > 20)
                if content:
                    break
        if not content:
            content = "Content not available"

        # Category detection by URL path
        path = urlparse(url).path
        category = "General"
        if "/bangladesh/" in path:
            category = "Bangladesh"
        elif "/world/" in path:
            category = "World"
        elif "/sports/" in path:
            category = "Sports"
        elif "/entertainment/" in path:
            category = "Entertainment"

        # Author extraction (simple)
        author = None
        for sel in ['.author', '.reporter', '.byline', '[rel="author"]']:
            els = soup.select(sel)
            if els and els[0].text.strip():
                author = els[0].text.strip()
                break
        if not author:
            author = "Unknown"

        article_data = {
            'title': title,
            'date': date_text,
            'url': url,
            'content': content,
            'category': category,
            'author': author,
            'scraped_at': get_bangladesh_time().strftime('%Y-%m-%d %H:%M:%S')
        }
        return article_data
    except Exception as e:
        logger.error(f"Error extracting article {url}: {e}")
        return None

@app.route('/articles', methods=['GET'])
def get_articles():
    try:
        article_links = get_article_links()
        articles = []
        count = 0
        for link in article_links:
            if count >= MIN_ARTICLES:
                break
            article = extract_article_content(link)
            if article:
                articles.append(article)
                count += 1
        return jsonify({"count": len(articles), "articles": articles})
    except Exception as e:
        logger.error(f"Error in /articles endpoint: {e}")
        return jsonify({"error": "Failed to fetch articles"}), 500

if __name__ == '__main__':
    app.run(debug=True)
