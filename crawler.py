import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

URLS = [
    "http://www.royalcaribbeanpresscenter.com/",
    "https://www.celebritycruisespresscenter.com/",
    "https://www.breakingtravelnews.com/news/category/cruise/",
    "https://thepointsguy.com/cruise/",
    "https://www.mscpressarea.com/en_INT/",
    "https://www.mscpressarea.com/en_US/press-releases/",
    "https://cruiseindustrynews.com/cruise-news/",
    "https://cruisefever.net/",
    "https://www.travelandtourworld.com/news/article/category/cruise-news/",
    "https://www.nclhltd.com/news-media/press-releases",
    "https://www.carnivalcorp.com/media-center/news-releases/",
    "https://www.carnival-news.com/",
    "https://www.royalcaribbeangroup.com/",
    "https://www.royalcaribbeanblog.com/",
    "https://www.ncl.com/il/he/newsroom?h=1&t=news",
    "https://www.travelpulse.com/trending/cruise-trends",
    "https://www.hollandamerica.com/en/newsroom",
    "https://www.travelweekly.com/Cruise-Travel",
    "https://www.usatoday.com/travel/cruises/",
    "https://www.oceaniacruises.com/news",
    "https://disneyexperiences.com/dcl-press/",
    "https://www.cunard.com/en-gb/contact-us/press-releases",
    "https://www.pocruises.com/blog",
    "https://www.rssc.com/news",
    "https://www.seabourn.com/en/news/press-release",
    "https://www.crystalcruises.com/",
    "https://www.windstarcruises.com/press-media/press-releases/",
    "https://www.princess.com/en-int/news/news-releases"
]

def extract_links(url):
    """Extract up to 10 article links from a news listing page."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        seen_links = set()

        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            link = a["href"]

            # Normalize relative URLs
            if link.startswith("/"):
                from urllib.parse import urlparse
                base = urlparse(url)
                link = f"{base.scheme}://{base.netloc}{link}"

            if (
                len(title) > 30
                and link.startswith("http")
                and link not in seen_links
                and link != url  # skip the page itself
            ):
                seen_links.add(link)
                results.append({"title": title, "link": link})

            if len(results) >= 10:
                break

        return results

    except Exception as e:
        print(f"  [crawler] Failed to extract links from {url}: {e}")
        return []


def get_snippet(url):
    """Grab the first meaningful paragraph from an article page."""
    try:
        r = requests.get(url, timeout=10, headers=HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text[:250]
        return ""

    except Exception as e:
        print(f"  [crawler] Failed to get snippet from {url}: {e}")
        return ""


def collect(seen_links: set) -> list:
    """
    Crawl all sites, extract headlines, skip already-seen links,
    and fetch snippets for new ones.

    Returns a list of dicts: {title, link, snippet}
    """
    data = []

    for site in URLS:
        print(f"[crawler] Crawling {site}")
        items = extract_links(site)

        for item in items:
            if item["link"] in seen_links:
                continue
            snippet = get_snippet(item["link"])
            data.append({
                "title": item["title"],
                "link": item["link"],
                "snippet": snippet
            })

    print(f"[crawler] Collected {len(data)} new items.")
    return data