"""
crawler.py — Multi-strategy cruise news crawler.

Strategies (matched per site in config.yaml):
  rss       — feedparser, fastest and most reliable
  html      — requests + BeautifulSoup, for simple sites
  stealth   — curl_cffi impersonating Chrome, bypasses 403 bot-blocks
  playwright — full headless browser, for JS-rendered SPAs

Public interface (unchanged):
  collect(seen_links: set) -> list[{title, link, snippet}]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.yaml"

FALLBACK_CONTENT_SELECTORS = [
    "article p",
    ".entry-content p",
    ".post-content p",
    ".article-body p",
    ".content p",
    "main p",
]


# ── Article dataclass ──────────────────────────────────────────────────────────

@dataclass
class Article:
    title: str
    url: str
    content: str
    source: str
    published_date: datetime | None = None


# ── Public interface ───────────────────────────────────────────────────────────

def collect(seen_links: set) -> list[dict]:
    """
    Crawl all sites in config.yaml, skip already-seen links,
    and return a list of {title, link, snippet} dicts for main.py.
    """
    config = _load_config()
    articles = _scrape_all(config, seen_links)

    results = []
    for a in articles:
        results.append({
            "title": a.title,
            "link": a.url,
            "snippet": a.content[:300] if a.content else "",
        })

    logger.info(f"[crawler] Collected {len(results)} new items.")
    return results


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    # Fallback minimal config if no config.yaml present
    logger.warning("config.yaml not found — using fallback site list")
    return {"settings": {}, "sites": _fallback_sites()}


def _fallback_sites() -> list[dict]:
    """Minimal fallback so the crawler still works without config.yaml."""
    return [
        {"name": "Royal Caribbean Blog", "url": "https://www.royalcaribbeanblog.com/", "type": "rss", "rss_url": "https://www.royalcaribbeanblog.com/rss.xml", "content_selector": ".field-item p"},
        {"name": "Cruise Fever", "url": "https://cruisefever.net/", "type": "html", "listing_selector": ".entry-title a", "content_selector": ".td-post-content p"},
        {"name": "Travel and Tour World", "url": "https://www.travelandtourworld.com/news/article/category/cruise-news/", "type": "rss", "rss_url": "https://www.travelandtourworld.com/news/article/category/cruise-news/feed/", "content_selector": ".details p, article p"},
    ]


# ── Orchestrator ───────────────────────────────────────────────────────────────

def _scrape_all(config: dict, seen_links: set) -> list[Article]:
    settings   = config.get("settings", {})
    timeout    = settings.get("request_timeout", 15)
    user_agent = settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    max_age_h  = settings.get("time_window_hours", 24)
    cutoff     = datetime.now(timezone.utc) - timedelta(hours=max_age_h)
    headers    = {"User-Agent": user_agent}

    all_articles: list[Article] = []
    playwright_sites: list[dict] = []

    for site in config.get("sites", []):
        if site.get("enabled") is False:
            continue

        site_type = site.get("type", "html")
        name = site.get("name", site["url"])

        # Playwright sites are batched together to reuse one browser instance
        if site_type == "playwright":
            playwright_sites.append(site)
            continue

        try:
            if site_type == "rss":
                articles = _scrape_rss(site, headers, timeout, cutoff, seen_links)
            elif site_type == "stealth":
                articles = _scrape_stealth(site, timeout, cutoff, seen_links)
            else:
                articles = _scrape_html(site, headers, timeout, cutoff, seen_links)

            all_articles.extend(articles)
            logger.info(f"[{name}] {len(articles)} articles")
        except Exception as e:
            logger.error(f"[{name}] failed: {e}")

    if playwright_sites:
        try:
            pw = _scrape_playwright_batch(playwright_sites, cutoff, seen_links)
            all_articles.extend(pw)
        except Exception as e:
            logger.error(f"[Playwright] failed: {e}")

    return all_articles


# ── RSS ────────────────────────────────────────────────────────────────────────

def _scrape_rss(site, headers, timeout, cutoff, seen_links) -> list[Article]:
    feed = feedparser.parse(site["rss_url"])
    articles = []

    for entry in feed.entries:
        published = _parse_feed_date(entry)
        if published and published < cutoff:
            continue

        url   = entry.get("link", "")
        title = entry.get("title", "").strip()
        if not url or not title or url in seen_links:
            continue

        content, _ = _fetch_article_content(url, site, headers, timeout)
        if not content:
            content = _extract_feed_content(entry)

        articles.append(Article(title=title, url=url, content=content,
                                source=site["name"], published_date=published))

    return articles


# ── HTML ───────────────────────────────────────────────────────────────────────

def _scrape_html(site, headers, timeout, cutoff, seen_links) -> list[Article]:
    listing_selector = site.get("listing_selector")
    if not listing_selector:
        return []

    resp = requests.get(site["url"], headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    max_per_site = site.get("max_articles", 5)
    links = soup.select(listing_selector)[: max_per_site * 4]
    title_elements = soup.select(site["title_selector"]) if site.get("title_selector") else []

    articles = []
    seen_urls: set[str] = set()

    for i, link in enumerate(links):
        href = link.get("href", "")
        if not href:
            continue
        url = _resolve_url(href, site)
        if url in seen_urls or url in seen_links:
            continue
        seen_urls.add(url)

        title = link.get_text(strip=True)
        if len(title) < 10 and i < len(title_elements):
            title = title_elements[i].get_text(strip=True)
        if len(title) < 10:
            continue

        content, pub_date = _fetch_article_content(url, site, headers, timeout, cutoff)
        if not content:
            continue

        articles.append(Article(title=title, url=url, content=content,
                                source=site["name"], published_date=pub_date))
        if len(articles) >= max_per_site:
            break

    return articles


# ── Stealth (curl_cffi) ────────────────────────────────────────────────────────

def _scrape_stealth(site, timeout, cutoff, seen_links) -> list[Article]:
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        logger.warning(f"[{site['name']}] curl_cffi not installed — falling back to requests")
        return _scrape_html(site, {}, timeout, cutoff, seen_links)

    listing_selector = site.get("listing_selector")
    if not listing_selector:
        return []

    resp = cffi.get(site["url"], impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    max_per_site = site.get("max_articles", 5)
    elements = soup.select(listing_selector)[: max_per_site * 4]
    title_elements = soup.select(site["title_selector"]) if site.get("title_selector") else []

    articles = []
    seen_urls: set[str] = set()

    for i, el in enumerate(elements):
        if el.name == "a":
            href  = el.get("href", "")
            title = el.get_text(strip=True)
        else:
            title  = el.get_text(strip=True)
            parent = el.find_parent(["div", "article", "li", "section"])
            a_tag  = parent.find("a", href=True) if parent else el.find("a", href=True)
            href   = a_tag.get("href", "") if a_tag else ""

        if not href:
            continue
        url = _resolve_url(href, site)
        if url in seen_urls or url in seen_links:
            continue
        seen_urls.add(url)

        if len(title) < 10:
            if i < len(title_elements):
                title = title_elements[i].get_text(strip=True)
        if len(title) < 10:
            parent = el.find_parent(["div", "article", "li", "section"])
            if parent:
                h = parent.find(["h1", "h2", "h3", "h4", "h5", "h6"])
                if h:
                    title = h.get_text(strip=True)
        if len(title) < 10:
            continue

        try:
            art_resp = cffi.get(url, impersonate="chrome", timeout=timeout)
            art_soup = BeautifulSoup(art_resp.text, "html.parser")
            pub_date = _extract_pub_date(art_soup, url)
            if cutoff and pub_date and pub_date < cutoff:
                logger.debug(f"Skipping old article ({pub_date.date()}): {url}")
                continue
            content  = _extract_content(art_soup, site.get("content_selector"))
        except Exception as e:
            logger.warning(f"[{site['name']}] failed to fetch {url}: {e}")
            content = ""
            pub_date = None

        if not content:
            continue

        articles.append(Article(title=title, url=url, content=content,
                                source=site["name"], published_date=pub_date))
        if len(articles) >= max_per_site:
            break

    return articles


# ── Playwright ─────────────────────────────────────────────────────────────────

def _scrape_playwright_batch(sites, cutoff, seen_links) -> list[Article]:
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        logger.warning("patchright not installed — skipping Playwright sites")
        return []

    articles: list[Article] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = {runtime: {}};
        """)
        page = context.new_page()

        for site in sites:
            if site.get("enabled") is False:
                continue
            name = site.get("name", "unknown")
            max_per_site = site.get("max_articles", 5)

            try:
                page.goto(site["url"], timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                listing_selector = site.get("listing_selector", "article a")
                links = page.eval_on_selector_all(
                    listing_selector,
                    """els => els.map(el => {
                        let text = el.textContent.trim();
                        if (text.length < 15) {
                            const c = el.closest('div,article,li,section,.card');
                            if (c) {
                                const h = c.querySelector('h1,h2,h3,h4,h5,h6,[class*=title],[class*=headline]');
                                if (h) text = h.textContent.trim();
                            }
                        }
                        return {href: el.href, text: text};
                    })"""
                )

                seen_urls: set[str] = set()
                site_count = 0

                for link_data in links[:max_per_site * 4]:
                    href  = link_data.get("href", "")
                    title = link_data.get("text", "")
                    if not href or len(title) < 10 or href in seen_urls or href in seen_links:
                        continue
                    seen_urls.add(href)

                    try:
                        page.goto(href, timeout=20000, wait_until="domcontentloaded")
                        page.wait_for_timeout(1500)

                        # Extract publication date from meta tags before fetching full content
                        page_html = page.content()
                        art_soup = BeautifulSoup(page_html, "html.parser")
                        pub_date = _extract_pub_date(art_soup, href)
                        if cutoff and pub_date and pub_date < cutoff:
                            logger.debug(f"[{name}] Skipping old article ({pub_date.date()}): {href}")
                            continue

                        content_sel = site.get("content_selector", "article p")
                        paragraphs  = page.eval_on_selector_all(
                            content_sel, "els => els.map(el => el.textContent.trim())"
                        )
                        content = " ".join(paragraphs)
                        if len(content) > 100:
                            articles.append(Article(
                                title=title, url=href,
                                content=content, source=name,
                                published_date=pub_date,
                            ))
                            site_count += 1
                    except Exception as e:
                        logger.warning(f"[{name}] failed to fetch {href}: {e}")

                    if site_count >= max_per_site:
                        break

                logger.info(f"[{name}] {site_count} articles")
            except Exception as e:
                logger.error(f"[{name}] playwright failed: {e}")

        browser.close()

    return articles


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_url(href: str, site: dict) -> str:
    url_prefix = site.get("url_prefix")
    if url_prefix and href.startswith("/"):
        return url_prefix.rstrip("/") + href
    return urljoin(site["url"], href)


def _extract_pub_date(soup: BeautifulSoup, url: str) -> datetime | None:
    """
    Try multiple strategies to find an article's publication date.
    Returns a timezone-aware datetime or None if not found.

    Strategy order:
      1. <meta> Open Graph / schema tags
      2. <time datetime="..."> elements
      3. JSON-LD structured data
      4. URL date pattern  e.g. /2026/03/21/
      5. Dateline in article text  e.g. "MIAMI, March 11, 2026" or "June 17, 2025"
    """
    import re, json as _json

    # 1. Meta tags
    for prop in (
        "article:published_time", "article:modified_time",
        "og:updated_time", "datePublished", "pubdate",
    ):
        tag = soup.find("meta", attrs={"property": prop}) or \
              soup.find("meta", attrs={"name": prop}) or \
              soup.find("meta", attrs={"itemprop": prop})
        if tag and tag.get("content"):
            d = _try_parse_iso(tag["content"])
            if d:
                return d

    # 2. <time> element
    for time_el in soup.find_all("time", datetime=True):
        d = _try_parse_iso(time_el["datetime"])
        if d:
            return d

    # 3. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            for key in ("datePublished", "dateModified", "uploadDate"):
                if key in data:
                    d = _try_parse_iso(data[key])
                    if d:
                        return d
        except Exception:
            pass

    # 4. URL pattern  /2025/08/14/ or /2025-08-14
    m = re.search(r'[/_-](\d{4})[/_-](0[1-9]|1[0-2])[/_-](0[1-9]|[12]\d|3[01])', url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass

    # 5. Dateline in article body text
    # Catches patterns like:
    #   "MIAMI, March 11, 2026"
    #   "SEATTLE, June 17, 2025 –"
    #   "Geneva, Switzerland – February 26, 2026"
    #   "March 21, 2026" (standalone)
    MONTHS = {
        "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
        "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
        "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,
        "sep":9,"oct":10,"nov":11,"dec":12,
    }
    # Search first 2000 chars of body text only — datelines always appear near the top
    body_text = soup.get_text(" ")[:2000]
    # Pattern: optional city prefix, then "Month D, YYYY" or "Month DD, YYYY"
    date_pat = re.compile(
        r'\b(' + '|'.join(MONTHS.keys()) + r')\w*\.?\s+(\d{1,2}),?\s+(20\d{2})\b',
        re.IGNORECASE
    )
    m = date_pat.search(body_text)
    if m:
        try:
            month = MONTHS[m.group(1).lower()[:3]]
            day   = int(m.group(2))
            year  = int(m.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def _try_parse_iso(s: str) -> datetime | None:
    """Parse an ISO-8601 date string into a UTC-aware datetime."""
    import re
    if not s:
        return None
    # Normalise: remove fractional seconds, handle Z
    s = re.sub(r'\.\d+', '', s.strip()).replace('Z', '+00:00')
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s[:len(fmt) + 6], fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except ValueError:
            continue
    return None


def _fetch_article_content(url: str, site: dict, headers: dict, timeout: int,
                            cutoff: datetime | None = None) -> tuple[str, datetime | None]:
    """
    Fetch an article page. Returns (content, pub_date).
    If cutoff is given and the article is older, returns ("", pub_date) so the
    caller can skip it without re-fetching.
    """
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        pub_date = _extract_pub_date(soup, url)
        if cutoff and pub_date and pub_date < cutoff:
            logger.debug(f"Skipping old article ({pub_date.date()}): {url}")
            return "", pub_date
        content = _extract_content(soup, site.get("content_selector"))
        return content, pub_date
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return "", None


def _extract_content(soup: BeautifulSoup, content_selector: str | None) -> str:
    selectors = []
    if content_selector:
        selectors.extend(s.strip() for s in content_selector.split(","))
    selectors.extend(FALLBACK_CONTENT_SELECTORS)

    for selector in selectors:
        elements = soup.select(selector)
        if elements:
            text = " ".join(el.get_text(strip=True) for el in elements)
            if len(text) > 100:
                return text[:2000]  # cap snippet length

    return ""


def _extract_feed_content(entry) -> str:
    for field in ("content", "summary_detail"):
        val = getattr(entry, field, None)
        if val:
            html = (val[0] if isinstance(val, list) else val).get("value", "")
            if html:
                text = BeautifulSoup(html, "html.parser").get_text(strip=True)
                if len(text) > 100:
                    return text[:2000]
    summary = entry.get("summary", "")
    if len(summary) > 100:
        return BeautifulSoup(summary, "html.parser").get_text(strip=True)[:2000]
    return ""


def _parse_feed_date(entry) -> datetime | None:
    from time import mktime
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            except Exception:
                pass
    return None
