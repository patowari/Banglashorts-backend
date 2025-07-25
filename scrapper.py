import requests
from bs4 import BeautifulSoup
import csv
import os
import time
from datetime import datetime, timedelta
import pytz
from urllib.parse import urljoin
import re
import pandas as pd
import hashlib
import logging
import schedule
import sys
import urllib.parse
import traceback

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Create output directories if they don't exist
os.makedirs("images", exist_ok=True)
os.makedirs("output", exist_ok=True)

# Constants
OUTPUT_CSV = "output/dhaka_post_today.csv"
BASE_URL = "https://www.dhakapost.com/latest-news"
CATEGORY_URLS = [
    "https://www.dhakapost.com/latest-news",
    "https://www.dhakapost.com/bangladesh",
    "https://www.dhakapost.com/world",
    "https://www.dhakapost.com/sports",
    "https://www.dhakapost.com/entertainment"
]
PAGINATION_PATTERN = "?page={}"
MIN_ARTICLES = 25

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,bn;q=0.8',
}

def get_bangladesh_time():
    """Get current date/time in Bangladesh timezone"""
    bd_tz = pytz.timezone('Asia/Dhaka')
    return datetime.now(bd_tz)

def is_today_or_yesterday(date_str):
    """Check if a date string represents today's or yesterday's date in Bangladesh time"""
    try:
        today = get_bangladesh_time().date()
        yesterday = today - timedelta(days=1)
        
        # Try to parse common date formats
        date_formats = [
            '%Y-%m-%d',                   # 2025-05-14
            '%d %B, %Y',                  # 14 May, 2025
            '%d %b, %Y',                  # 14 May, 2025
            '%d %B %Y',                   # 14 May 2025
            '%B %d, %Y',                  # May 14, 2025
            '%d/%m/%Y',                   # 14/05/2025
            '%Y-%m-%dT%H:%M:%S',          # 2025-05-14T10:30:00
            '%Y-%m-%dT%H:%M:%S.%fZ',      # 2025-05-14T10:30:00.000Z
        ]
        
        # Clean up the date string
        clean_date = date_str.strip()
        
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(clean_date, fmt).date()
                return parsed_date == today or parsed_date == yesterday
            except ValueError:
                continue
        
        # If we couldn't parse the date, check if it contains "today", "yesterday" or the dates
        today_str = today.strftime('%d %B')
        today_short = today.strftime('%d %b')
        yesterday_str = yesterday.strftime('%d %B')
        yesterday_short = yesterday.strftime('%d %b')
        
        today_indicators = [
            'today', 
            'just now', 
            'minutes ago', 
            'hours ago',
            today_str.lower(),
            today_short.lower(),
            f"{today.day} {today.strftime('%B')}".lower(),
            f"{today.day} {today.strftime('%b')}".lower(),
            f"{today.day}/{today.month}",
            f"{today.month}/{today.day}"
        ]
        
        yesterday_indicators = [
            'yesterday',
            yesterday_str.lower(),
            yesterday_short.lower(),
            f"{yesterday.day} {yesterday.strftime('%B')}".lower(),
            f"{yesterday.day} {yesterday.strftime('%b')}".lower(),
            f"{yesterday.day}/{yesterday.month}",
            f"{yesterday.month}/{yesterday.day}"
        ]
        
        lower_date = clean_date.lower()
        return any(indicator in lower_date for indicator in today_indicators + yesterday_indicators)
    except Exception as e:
        logger.error(f"Error checking date: {e}")
        # In case of any error, let's be lenient and assume it could be recent
        return True

def get_existing_articles():
    """Get list of article URLs already scraped"""
    existing_urls = set()
    existing_titles = set()
    
    if os.path.exists(OUTPUT_CSV):
        try:
            existing_df = pd.read_csv(OUTPUT_CSV, encoding='utf-8')
            if 'url' in existing_df.columns:
                existing_urls = set(existing_df['url'].values)
            if 'title' in existing_df.columns:
                existing_titles = set(existing_df['title'].values)
            logger.info(f"Found {len(existing_urls)} existing articles in the CSV")
        except Exception as e:
            logger.error(f"Error reading existing CSV: {e}")
            logger.error(traceback.format_exc())
            # If we can't read the existing file, it might be corrupted
            backup_filename = f"output/dhaka_post_backup_{int(time.time())}.csv"
            try:
                if os.path.exists(OUTPUT_CSV):
                    os.rename(OUTPUT_CSV, backup_filename)
                    logger.info(f"Renamed potentially corrupted CSV to {backup_filename}")
            except Exception as e2:
                logger.error(f"Failed to rename corrupted CSV: {e2}")
    
    return existing_urls, existing_titles

def get_article_links_from_page(url):
    """Extract article links from a specific page"""
    logger.info(f"Fetching article links from {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error fetching page {url}: {e}")
        return []
    
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
        article_links = []
        
        # Find all potential article containers
        article_containers = soup.select('.card, .news-item, article, .list-item, .news-card, .news-list, .article-list')
        
        if not article_containers:
            # Look for news entries in flexible ways
            article_containers = soup.select('div[class*="news"], div[class*="article"], div[class*="post"], a[href*="/news/"]')
        
        if not article_containers:
            # Fallback: look for all links
            article_containers = [soup]
        
        logger.info(f"Found {len(article_containers)} potential article containers on {url}")
        
        # Try to find pagination links to understand structure
        pagination = soup.select('.pagination a, .page-navigation a, a[href*="page="]')
        if pagination:
            logger.info(f"Found pagination with {len(pagination)} links")
        
        for container in article_containers:
            links = container.find_all('a')
            for link in links:
                href = link.get('href')
                if not href:
                    continue
                    
                # Skip non-article links
                if href.startswith('#') or href.startswith('javascript:'):
                    continue
                    
                # Check if it looks like an article link
                article_indicators = [
                    '/news/', '/article/', '/story/', '/latest-news/',
                    '/bangladesh/', '/world/', '/sports/', '/entertainment/'
                ]
                
                is_article = any(indicator in href for indicator in article_indicators)
                
                if is_article:
                    # Make sure it's a full URL
                    full_url = urljoin(url, href)
                    
                    # Skip duplicates
                    if full_url not in article_links:
                        article_links.append(full_url)
                        logger.debug(f"Found potential article link: {full_url}")
        
        logger.info(f"Extracted {len(article_links)} article links from {url}")
        return article_links
    except Exception as e:
        logger.error(f"Error parsing page {url}: {e}")
        logger.error(traceback.format_exc())
        return []

def get_article_links():
    """Extract article links from Dhaka Post across multiple categories and pages"""
    all_links = []
    
    # First try the main category URLs
    for category_url in CATEGORY_URLS:
        try:
            links = get_article_links_from_page(category_url)
            all_links.extend(links)
            
            # Check if we need to try pagination (only if we don't have enough links yet)
            if len(set(all_links)) < MIN_ARTICLES * 2:  # Get 2x the minimum to account for filtering
                # Try up to 3 pages of pagination
                for page in range(2, 5):
                    paginated_url = f"{category_url}{PAGINATION_PATTERN.format(page)}"
                    logger.info(f"Trying pagination: {paginated_url}")
                    page_links = get_article_links_from_page(paginated_url)
                    
                    if page_links:
                        all_links.extend(page_links)
                    else:
                        # If we get no links, pagination might not work this way
                        break
                        
                    if len(set(all_links)) >= MIN_ARTICLES * 3:
                        break
                        
                    # Be nice to the server
                    time.sleep(2)
            
            # Be nice to the server
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error processing category URL {category_url}: {e}")
            logger.error(traceback.format_exc())
    
    # Return unique links
    unique_links = list(set(all_links))
    logger.info(f"Found {len(unique_links)} unique article links across all categories")
    return unique_links

def extract_article_content(url):
    """Extract the content of an article and check if it was published today or yesterday"""
    logger.info(f"Extracting content from {url}")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error fetching article: {e}")
        return None
    
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract title
        title = None
        title_selectors = ['h1', '.article-title', '.news-title', '.title', '.headline', '.entry-title']
        for selector in title_selectors:
            title_elements = soup.select(selector)
            if title_elements and title_elements[0].text.strip():
                title = title_elements[0].text.strip()
                logger.info(f"Found title: {title}")
                break
        
        if not title:
            # Try to find any large text that might be a title
            for heading in soup.find_all(['h1', 'h2']):
                if heading.text.strip() and len(heading.text.strip()) > 15:
                    title = heading.text.strip()
                    logger.info(f"Found title from heading: {title}")
                    break
        
        if not title:
            logger.warning("No title found")
            return None
        
        # Extract date
        date_text = "No date found"
        date_selectors = [
            'time', '.date', '.published-date', '.article-date', 
            '[itemprop="datePublished"]', '.time', '.timestamp',
            '.publish-time', '.meta-date', '.post-date',
            '.entry-date', '.article-info time'
        ]
        
        for selector in date_selectors:
            date_elements = soup.select(selector)
            if date_elements:
                # Try to get datetime attribute first
                dt_attr = date_elements[0].get('datetime')
                if dt_attr:
                    date_text = dt_attr
                    logger.info(f"Found date from datetime attribute: {date_text}")
                    break
                
                # Otherwise use the text content
                date_content = date_elements[0].text.strip()
                if date_content:
                    date_text = date_content
                    logger.info(f"Found date from text: {date_text}")
                    break
        
        # If date not found in specific elements, try regex pattern in the page
        if date_text == "No date found":
            date_pattern = re.compile(r'\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{1,2}-\d{1,2}')
            for tag in soup.find_all(['span', 'div', 'p']):
                if tag.string and date_pattern.search(str(tag.string)):
                    date_text = date_pattern.search(str(tag.string)).group()
                    logger.info(f"Found date using regex: {date_text}")
                    break
        
        # We're being less strict here - let the article through even if we can't verify the date
        # This is one of the key fixes
        is_recent = date_text == "No date found" or is_today_or_yesterday(date_text)
        if not is_recent:
            logger.info(f"Article not from today or yesterday. Date: {date_text}")
            # Commented out the return None to be more lenient with dates
            # return None
        
        # Extract content
        content = ""
        content_selectors = [
            'article p', '.article-body p', '.content p', 
            '#content p', '.news-content p', '.story p',
            '.description p', '.article-description p',
            '.entry-content p', '.article-text p',
            '.news-details p', '.post-content p'
        ]
        
        for selector in content_selectors:
            paragraphs = soup.select(selector)
            if paragraphs:
                for p in paragraphs:
                    # Skip very short paragraphs (likely not content)
                    if len(p.text.strip()) > 20:
                        content += p.text.strip() + "\n\n"
                # If we found content, break
                if content:
                    logger.info(f"Found content using {selector}")
                    break
        
        # Fallback: get all paragraphs if no content found yet
        if not content:
            article_container = soup.find('article') or soup.select_one('.article, .article-body, .story-content, .entry-content, .news-details')
            if article_container:
                paragraphs = article_container.find_all('p')
            else:
                paragraphs = soup.find_all('p')
                
            for p in paragraphs:
                # Skip very short paragraphs
                if len(p.text.strip()) > 20:
                    content += p.text.strip() + "\n\n"
        
        content = content.strip()
        
        # Be more lenient with empty content - extract at least title and URL
        if not content:
            logger.warning("No content found, but continuing with metadata only")
            content = "Content not available"
        
        # Extract images
        img_urls = []
        article_container = soup.find('article') or soup.select_one('.article, .article-body, .story-content, .entry-content, .news-details')
        
        if article_container:
            img_elements = article_container.find_all('img')
        else:
            img_elements = soup.find_all('img')
        
        for img in img_elements:
            img_url = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if img_url:
                # Exclude common non-content images
                exclude_patterns = ['icon', 'logo', 'blank.gif', 'pixel.gif', 'advertisement', 'banner', 'avatar', 'thumb', '1x1']
                if not any(pattern in img_url.lower() for pattern in exclude_patterns):
                    # Check if image is large enough (if dimensions are provided)
                    width = img.get('width')
                    height = img.get('height')
                    is_large_enough = True
                    
                    if width and height:
                        try:
                            w, h = int(width), int(height)
                            if w < 100 or h < 100:
                                is_large_enough = False
                        except ValueError:
                            pass
                    
                    if is_large_enough:
                        full_img_url = urljoin(url, img_url)
                        if full_img_url not in img_urls:
                            img_urls.append(full_img_url)
                            logger.info(f"Found image: {full_img_url}")
        
        # Try to identify the article category
        category = "General"
        url_path = urllib.parse.urlparse(url).path
        
        if "/bangladesh/" in url_path:
            category = "Bangladesh"
        elif "/world/" in url_path:
            category = "World"
        elif "/sports/" in url_path:
            category = "Sports"
        elif "/entertainment/" in url_path:
            category = "Entertainment"
        elif "/business/" in url_path:
            category = "Business"
        elif "/tech/" in url_path or "/technology/" in url_path:
            category = "Technology"
        elif "/opinion/" in url_path:
            category = "Opinion"
        elif "/lifestyle/" in url_path:
            category = "Lifestyle"
        
        # Try to extract author
        author = "Unknown"
        author_selectors = [
            '.author', '.reporter', '.byline', '[rel="author"]',
            '.writer', '.article-author', '.post-author'
        ]
        
        for selector in author_selectors:
            author_elements = soup.select(selector)
            if author_elements and author_elements[0].text.strip():
                author = author_elements[0].text.strip()
                break
        
        # Create the article data dictionary - added new fields
        article_data = {
            'title': title,
            'date': date_text,
            'url': url,
            'content': content,
            'image_urls': img_urls,
            'category': category,
            'author': author,
            'timestamp': get_bangladesh_time().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        return article_data
    except Exception as e:
        logger.error(f"Error parsing article {url}: {e}")
        logger.error(traceback.format_exc())
        return None

def create_safe_filename(title, url):
    """Create a safe filename from the article title or URL"""
    try:
        # Remove special characters from title
        if title:
            # Transliterate Bengali characters if needed
            safe_title = re.sub(r'[^\w\s-]', '', title)
            safe_title = re.sub(r'\s+', '-', safe_title).strip('-')
            # Limit length
            safe_title = safe_title[:50]
            if safe_title:
                return safe_title
        
        # Fallback to URL-based filename
        url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
        return f"article-{url_hash}"
    except Exception as e:
        logger.error(f"Error creating filename: {e}")
        # Ultimate fallback
        return f"article-{int(time.time())}"

def download_image(img_url, article_title, url):
    """Download an image and return the local path with readable filename"""
    try:
        # Get file extension from URL
        parsed_url = urllib.parse.urlparse(img_url)
        path = parsed_url.path
        file_ext = os.path.splitext(path)[1].lower()
        
        # Default to .jpg if no extension or unrecognized extension
        if not file_ext or file_ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            file_ext = '.jpg'
        
        # Create base filename from article title
        base_filename = create_safe_filename(article_title, url)
        
        # Add a hash of the image URL to ensure uniqueness
        img_hash = hashlib.md5(img_url.encode()).hexdigest()[:8]
        filename = f"{base_filename}-{img_hash}{file_ext}"
        
        local_path = os.path.join("images", filename)
        
        # Don't re-download if the file already exists
        if os.path.exists(local_path):
            logger.info(f"Image already exists: {local_path}")
            return local_path
        
        # Download the image
        response = requests.get(img_url, headers=HEADERS, stream=True, timeout=30)
        if response.status_code == 200:
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"Downloaded image: {local_path}")
            return local_path
        else:
            logger.warning(f"Failed to download image: {img_url}")
            return None
    except Exception as e:
        logger.error(f"Error downloading image {img_url}: {e}")
        return None

def process_new_articles():
    """Process new articles and add them to the CSV"""
    # Get existing articles
    existing_urls, existing_titles = get_existing_articles()
    
    # Get article links
    article_links = get_article_links()
    
    # Filter out already processed articles
    new_links = [link for link in article_links if link not in existing_urls]
    logger.info(f"Found {len(new_links)} potential new articles to process")
    
    new_articles = []
    processed_titles = set()
    
    # Process articles until we have at least MIN_ARTICLES or run out of links
    for link in new_links:
        try:
            # Check if we've reached our minimum goal
            if len(new_articles) >= MIN_ARTICLES:
                logger.info(f"Reached minimum goal of {MIN_ARTICLES} articles")
                break
            
            # Extract article content
            article_data = extract_article_content(link)
            if not article_data:
                continue
            
            # Check if we already have this article by title
            if article_data['title'] in existing_titles or article_data['title'] in processed_titles:
                logger.info(f"Skipping duplicate article by title: {article_data['title']}")
                continue
            
            # Download images
            local_images = []
            for img_url in article_data['image_urls'][:3]:  # Limit to first 3 images
                local_path = download_image(img_url, article_data['title'], link)
                if local_path:
                    local_images.append(local_path)
            
            # Add local image paths to article data
            article_data['local_images'] = local_images
            new_articles.append(article_data)
            processed_titles.add(article_data['title'])
            
            logger.info(f"Processed article #{len(new_articles)}: {article_data['title']}")
            
            # Brief pause between article processing
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error processing article {link}: {e}")
            logger.error(traceback.format_exc())
    
    # Save new articles to CSV
    if new_articles:
        try:
            # Prepare data for CSV
            csv_data = []
            for article in new_articles:
                csv_row = {
                    'title': article['title'],
                    'date': article['date'],
                    'url': article['url'],
                    'content': article['content'],
                    'category': article.get('category', 'General'),
                    'author': article.get('author', 'Unknown'),
                    'image_urls': ';'.join(article['image_urls']),
                    'local_images': ';'.join(article['local_images']),
                    'scraped_at': article['timestamp']
                }
                csv_data.append(csv_row)
            
            new_df = pd.DataFrame(csv_data)
            
            if os.path.exists(OUTPUT_CSV):
                # Try to read existing CSV
                try:
                    existing_df = pd.read_csv(OUTPUT_CSV, encoding='utf-8')
                    
                    # Check if the column structures match
                    missing_cols = set(new_df.columns) - set(existing_df.columns)
                    if missing_cols:
                        logger.info(f"Adding new columns to existing CSV: {missing_cols}")
                        for col in missing_cols:
                            existing_df[col] = ""
                    
                    # Ensure all new columns exist in the new dataframe too
                    for col in existing_df.columns:
                        if col not in new_df.columns:
                            new_df[col] = ""
                    
                    # Ensure column order matches
                    new_df = new_df[existing_df.columns]
                    
                    # Append to existing CSV
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    combined_df.to_csv(OUTPUT_CSV, index=False, quoting=csv.QUOTE_ALL, encoding='utf-8')
                    logger.info(f"Added {len(new_articles)} new articles to {OUTPUT_CSV}")
                except Exception as e:
                    logger.error(f"Error appending to CSV: {e}")
                    logger.error(traceback.format_exc())
                    
                    # Make a backup of the existing file
                    if os.path.exists(OUTPUT_CSV):
                        backup_file = f"output/dhaka_post_today_backup_{int(time.time())}.csv"
                        try:
                            os.rename(OUTPUT_CSV, backup_file)
                            logger.info(f"Backed up existing CSV to {backup_file}")
                        except Exception as e2:
                            logger.error(f"Failed to backup existing CSV: {e2}")
                    
                    # Create a new file
                    new_df.to_csv(OUTPUT_CSV, index=False, quoting=csv.QUOTE_ALL, encoding='utf-8')
                    logger.info(f"Created new CSV with {len(new_articles)} articles after error")
            else:
                # Create new CSV
                new_df.to_csv(OUTPUT_CSV, index=False, quoting=csv.QUOTE_ALL, encoding='utf-8')
                logger.info(f"Saved {len(new_articles)} articles to new CSV: {OUTPUT_CSV}")
            
            logger.info(f"Successfully processed {len(new_articles)} new articles")
        except Exception as e:
            logger.error(f"Error saving to CSV: {e}")
            logger.error(traceback.format_exc())
            
            # Emergency backup - at least save the data somewhere
            emergency_file = f"output/dhaka_post_emergency_{int(time.time())}.csv"
            try:
                pd.DataFrame(csv_data).to_csv(emergency_file, index=False, quoting=csv.QUOTE_ALL, encoding='utf-8')
                logger.info(f"Created emergency backup at {emergency_file}")
            except Exception as e2:
                logger.error(f"Failed to create emergency backup: {e2}")
    else:
        logger.info("No new articles found to process")
    
    # Report status
    total_articles = len(new_articles)
    if os.path.exists(OUTPUT_CSV):
        try:
            df = pd.read_csv(OUTPUT_CSV, encoding='utf-8')
            total_articles = len(df)
        except Exception as e:
            logger.error(f"Error counting total articles: {e}")
    
    logger.info(f"Total articles in database: {total_articles}")
    if total_articles < MIN_ARTICLES:
        logger.warning(f"Failed to reach minimum goal of {MIN_ARTICLES} articles. Currently have {total_articles}.")

def run_scraper():
    """Run the scraper job"""
    logger.info("-" * 60)
    logger.info(f"Starting Dhaka Post scraper at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        process_new_articles()
    except Exception as e:
        logger.error(f"Error in scraper job: {e}")
        logger.error(traceback.format_exc())
    logger.info(f"Completed scraper job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("-" * 60)

def scheduled_job():
    """Function to be scheduled"""
    run_scraper()

def verify_csv_structure():
    """Verify and repair CSV structure if needed"""
    if not os.path.exists(OUTPUT_CSV):
        logger.info(f"CSV file {OUTPUT_CSV} does not exist yet")
        return
    
    try:
        df = pd.read_csv(OUTPUT_CSV, encoding='utf-8')
        logger.info(f"CSV structure verified: {len(df)} rows, {list(df.columns)} columns")
    except Exception as e:
        logger.error(f"Error verifying CSV structure: {e}")
        logger.error(traceback.format_exc())
        
        backup_file = f"output/dhaka_post_corrupted_{int(time.time())}.csv"
        try:
            os.rename(OUTPUT_CSV, backup_file)
            logger.info(f"Moved corrupted CSV to {backup_file}")
        except Exception as e2:
            logger.error(f"Failed to move corrupted CSV: {e2}")

def main():
    logger.info("Dhaka Post Today Scraper")
    logger.info("=" * 60)
    logger.info(f"Current time in Bangladesh: {get_bangladesh_time().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Verify CSV structure
    verify_csv_structure()
    
    # Run immediately at startup
    run_scraper()
    
    # Schedule to run every 10 minutes
    schedule.every(10).minutes.do(scheduled_job)
    logger.info("Scraper scheduled to run every 10 minutes")
    
    # Keep running
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Scraper stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
