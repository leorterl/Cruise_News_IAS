from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

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

PROMO_SNIPPET_PATTERNS = [
    r"all the latest on .{0,80}?explore now",
    r"explore now",
    r"presented by ",
    r"advertisement",
    r"sponsored",
]

DEFAULT_BLOCK_URL_PATTERNS = [
    r"/page/\d+/?$",
    r"/tag/",
    r"/tags/",
    r"/category/[^/]+/?$",
    r"/author/",
    r"/search",
    r"/news-releases/news-releases-list/?$",
    r"/news-releases/all-public-company-news/?$",
    r"/news-releases/english-releases/?$",
    r"/news-releases/multimedia/?$",
    r"/news-releases/multimedia/multimedia-list/?$",
    r"/group/",
    r"/people/",
    r"/work-with-us/?$",
    r"/governance/?$",
    r"/sustainability/?$",
    r"/innovation/?$",
    r"/cookie",
    r"/privacy",
    r"/terms",
    r"/contact",
]

DEFAULT_BLOCK_TITLE_PATTERNS = [
    r"^home$",
    r"^news$",
    r"^blog$",
    r"^media$",
    r"^about$",
    r"^contact$",
    r"^learn more$",
    r"^press releases$",
    r"^all news releases$",
    r"^all public company$",
    r"^english-only$",
    r"^multimedia gallery$",
    r"^all multimedia$",
    r"^innovation$",
    r"^governance and ethics$",
    r"^sustainability$",
    r"^living at .*$",
    r"^work with us$",
    r"^cookie policy$",
    r"^privacy policy$",
    r"^terms(?: and conditions)?$",
]

DEFAULT_EVERGREEN_TITLE_PATTERNS = [
    r"\bbest\b",
    r"\btips?\b",
    r"\btricks?\b",
    r"\bfreebies\b",
    r"\bguide\b",
    r"\beverything to know\b",
    r"\bcosts?\b",
    r"\bhotels?\b",
    r"\bshuttles?\b",
    r"\bdrinks?\b",
    r"\bparking guide\b",
]


@dataclass
class Article:
    title: str
    url: str
    content: str
    source: str
    published_date: datetime | None = None
    category: str = "cruise"


def collect(seen_links: set) -> list[dict]:
    config = _load_config()
    articles = _scrape_all(config, seen_links)
    results = []
    for a in articles:
        results.append(
            {
                "title": a.title,
                "link": a.url,
                "snippet": a.content[:300] if a.content else "",
                "source": a.source,
                "category": a.category,
            }
        )
    logger.info("[crawler] Collected %s new items.", len(results))
    return results


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    logger.warning("config.yaml not found — using fallback site list")
    return {"settings": {}, "sites": _fallback_sites()}


def _fallback_sites() -> list[dict]:
    return [
        {
            "name": "Royal Caribbean Blog",
            "url": "https://www.royalcaribbeanblog.com/",
            "type": "rss",
            "rss_url": "https://www.royalcaribbeanblog.com/rss.xml",
            "content_selector": ".field-item p",
        },
        {
            "name": "Cruise Fever",
            "url": "https://cruisefever.net/",
            "type": "html",
            "listing_selector": ".entry-title a",
            "content_selector": ".td-post-content p",
        },
    ]


def _scrape_all(config: dict, seen_links: set) -> list[Article]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    settings = config.get("settings", {})
    timeout = settings.get("request_timeout", 15)
    user_agent = settings.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    max_age_h = settings.get("time_window_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_h)
    headers = {"User-Agent": user_agent}

    all_articles: list[Article] = []
    playwright_sites: list[dict] = []
    fetch_jobs: list[dict] = []

    for site in config.get("sites", []):
        if site.get("enabled") is False:
            continue
        site_type = site.get("type", "html")
        if site_type == "playwright":
            playwright_sites.append(site)
        else:
            fetch_jobs.append(site)

    def _scrape_site(site):
        site_type = site.get("type", "html")
        name = site.get("name", site.get("url", "unknown"))
        site_window = site.get("time_window_hours")
        site_cutoff = datetime.now(timezone.utc) - timedelta(hours=site_window) if site_window else cutoff
        try:
            if site_type == "rss":
                articles = _scrape_rss(site, headers, timeout, site_cutoff, seen_links)
            elif site_type == "stealth":
                articles = _scrape_stealth(site, timeout, site_cutoff, seen_links)
            elif site_type == "wp_api":
                articles = _scrape_wp_api(site, timeout, site_cutoff, seen_links)
            else:
                articles = _scrape_html(site, headers, timeout, site_cutoff, seen_links)
            logger.info("[%s] %s articles", name, len(articles))
            return articles
        except Exception as e:
            logger.error("[%s] failed: %s", name, e)
            return []

    max_workers = settings.get("max_workers", 8)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scrape_site, site): site for site in fetch_jobs}
        for future in as_completed(futures):
            all_articles.extend(future.result())

    if playwright_sites:
        try:
            pw = _scrape_playwright_batch(playwright_sites, cutoff, seen_links)
            all_articles.extend(pw)
        except Exception as e:
            logger.error("[Playwright] failed: %s", e)

    return all_articles


def _scrape_rss(site, headers, timeout, cutoff, seen_links) -> list[Article]:
    feed = feedparser.parse(site["rss_url"])
    articles = []
    max_per_site = site.get("max_articles", 5)

    for entry in feed.entries:
        published = _parse_feed_date(entry)
        if published and published < cutoff:
            continue

        url = entry.get("link", "").strip()
        title = entry.get("title", "").strip()
        if not url or not title or url in seen_links:
            continue
        if _should_skip_url(url, site) or _should_skip_title(title, site):
            continue

        content = _extract_feed_content(entry)
        if not content or len(content) < 200:
            try:
                content_page, pub_from_page = _fetch_article_content(url, site, headers, timeout, cutoff)
                if content_page:
                    content = content_page
                published = published or pub_from_page
            except Exception:
                pass
        if not content:
            continue
        if _should_reject_article(site, title, url, content, published, cutoff):
            continue

        articles.append(Article(title=title, url=url, content=content, source=site["name"], published_date=published, category=site.get("category", "cruise")))
        if len(articles) >= max_per_site:
            break
    return articles


def _scrape_wp_api(site, timeout, cutoff, seen_links) -> list[Article]:
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        logger.warning("[%s] curl_cffi not installed — skipping wp_api site", site["name"])
        return []

    api_url = site["wp_api_url"]
    max_per_site = site.get("max_articles", 10)
    resp = cffi.get(api_url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    posts = resp.json()

    articles = []
    for post in posts:
        url = post.get("link", "").strip()
        title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text(strip=True)
        if not url or not title or url in seen_links:
            continue
        pub_str = post.get("date_gmt", post.get("date", ""))
        pub_date = _try_parse_iso(pub_str)
        if pub_date and pub_date < cutoff:
            continue
        content = BeautifulSoup(post.get("content", {}).get("rendered", ""), "html.parser").get_text(" ", strip=True)
        if _should_reject_article(site, title, url, content, pub_date, cutoff):
            continue
        articles.append(Article(title=title, url=url, content=content, source=site["name"], published_date=pub_date, category=site.get("category", "cruise")))
        if len(articles) >= max_per_site:
            break
    return articles


def _scrape_html(site, headers, timeout, cutoff, seen_links) -> list[Article]:
    listing_selector = site.get("listing_selector")
    if not listing_selector:
        return []

    resp = requests.get(site["url"], headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return _scrape_from_listing_soup(site, soup, headers, timeout, cutoff, seen_links)


def _scrape_stealth(site, timeout, cutoff, seen_links) -> list[Article]:
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        logger.warning("[%s] curl_cffi not installed — falling back to requests", site["name"])
        return _scrape_html(site, {}, timeout, cutoff, seen_links)

    listing_selector = site.get("listing_selector")
    if not listing_selector:
        return []

    resp = cffi.get(site["url"], impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return _scrape_from_listing_soup(site, soup, {}, timeout, cutoff, seen_links, cffi=cffi)


def _scrape_from_listing_soup(site, soup, headers, timeout, cutoff, seen_links, cffi=None) -> list[Article]:
    listing_selector = site.get("listing_selector")
    elements = soup.select(listing_selector)
    max_per_site = site.get("max_articles", 5)
    title_elements = soup.select(site["title_selector"]) if site.get("title_selector") else []
    articles: list[Article] = []
    seen_urls: set[str] = set()

    for i, el in enumerate(elements[: max_per_site * 5]):
        href, title = _extract_link_and_title(el)
        if not href:
            continue
        url = _resolve_url(href, site)
        if url in seen_urls or url in seen_links:
            continue
        seen_urls.add(url)

        if len(title) < 10 and i < len(title_elements):
            title = title_elements[i].get_text(" ", strip=True)
        if len(title) < 10:
            continue
        if _should_skip_url(url, site) or _should_skip_title(title, site):
            continue

        try:
            if cffi is None:
                content, pub_date = _fetch_article_content(url, site, headers, timeout, cutoff)
            else:
                art_resp = cffi.get(url, impersonate="chrome", timeout=timeout)
                art_resp.raise_for_status()
                art_soup = BeautifulSoup(art_resp.text, "html.parser")
                pub_date = _extract_pub_date(art_soup, url)
                content = _extract_content(art_soup, site.get("content_selector"))
        except Exception as e:
            logger.warning("[%s] failed to fetch %s: %s", site["name"], url, e)
            continue

        if _should_reject_article(site, title, url, content, pub_date, cutoff):
            continue

        articles.append(Article(title=title, url=url, content=content, source=site["name"], published_date=pub_date, category=site.get("category", "cruise")))
        if len(articles) >= max_per_site:
            break
    return articles


def _scrape_playwright_batch(sites, cutoff, seen_links) -> list[Article]:
    import asyncio

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        logger.warning("patchright not installed — skipping Playwright sites")
        return []

    active_sites = [s for s in sites if s.get("enabled") is not False]
    if not active_sites:
        return []

    async def _scrape_pw_site(site, browser, semaphore):
        async with semaphore:
            name = site.get("name", "unknown")
            max_per_site = site.get("max_articles", 5)
            site_window = site.get("time_window_hours")
            site_cutoff = datetime.now(timezone.utc) - timedelta(hours=site_window) if site_window else cutoff
            articles = []

            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            await ctx.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
                """
            )
            await ctx.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot,mp4,webm}", lambda route: route.abort())
            await ctx.route("**/{analytics,tracking,ads,pixel,beacon}**", lambda route: route.abort())
            page = await ctx.new_page()

            try:
                await page.goto(site["url"], timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(site.get("listing_wait_ms", 3000))
                listing_selector = site.get("listing_selector", "article a")
                links = await page.eval_on_selector_all(
                    listing_selector,
                    """els => els.map(el => {
                        let text = (el.textContent || '').trim();
                        if (text.length < 15) {
                            const c = el.closest('div,article,li,section,.card');
                            if (c) {
                                const h = c.querySelector('h1,h2,h3,h4,h5,h6,[class*=title],[class*=headline]');
                                if (h) text = (h.textContent || '').trim();
                            }
                        }
                        return {href: el.href, text};
                    })""",
                )
            except Exception as e:
                logger.error("[%s] playwright failed on listing: %s", name, e)
                await ctx.close()
                return []

            seen_urls: set[str] = set()
            site_count = 0
            for link_data in links[: max_per_site * 5]:
                href = (link_data.get("href") or "").strip()
                title = (link_data.get("text") or "").strip()
                if not href or len(title) < 10 or href in seen_urls or href in seen_links:
                    continue
                seen_urls.add(href)
                if _should_skip_url(href, site) or _should_skip_title(title, site):
                    continue

                title_date = _try_parse_dateline(title)
                if title_date and title_date < site_cutoff:
                    continue

                try:
                    await page.goto(href, timeout=20000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(site.get("article_wait_ms", 1500))
                    html = await page.content()
                    art_soup = BeautifulSoup(html, "html.parser")
                    pub_date = title_date or _extract_pub_date(art_soup, href)
                    content_sel = site.get("content_selector", "article p")
                    paragraphs = await page.eval_on_selector_all(
                        content_sel, "els => els.map(el => (el.textContent || '').trim())"
                    )
                    content = " ".join(p2 for p2 in paragraphs if p2)
                except Exception as e:
                    logger.warning("[%s] failed to fetch %s: %s", name, href, e)
                    continue

                if _should_reject_article(site, title, href, content, pub_date, site_cutoff):
                    continue

                articles.append(Article(title=title, url=href, content=content, source=name, published_date=pub_date, category=site.get("category", "cruise")))
                site_count += 1
                if site_count >= max_per_site:
                    break

            logger.info("[%s] %s articles", name, site_count)
            await ctx.close()
            return articles

    async def _run_all():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            semaphore = asyncio.Semaphore(4)
            tasks = [_scrape_pw_site(site, browser, semaphore) for site in active_sites]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            await browser.close()
        all_articles = []
        for r in results:
            if isinstance(r, list):
                all_articles.extend(r)
            elif isinstance(r, Exception):
                logger.error("[Playwright] task failed: %s", r)
        return all_articles

    return asyncio.run(_run_all())


def _extract_link_and_title(el) -> tuple[str, str]:
    if getattr(el, "name", None) == "a":
        return el.get("href", ""), el.get_text(" ", strip=True)
    title = el.get_text(" ", strip=True)
    parent = el.find_parent(["div", "article", "li", "section"]) if hasattr(el, "find_parent") else None
    a_tag = parent.find("a", href=True) if parent else None
    if not a_tag and hasattr(el, "find"):
        a_tag = el.find("a", href=True)
    href = a_tag.get("href", "") if a_tag else ""
    return href, title


def _resolve_url(href: str, site: dict) -> str:
    url_prefix = site.get("url_prefix")
    if url_prefix and href.startswith("/"):
        return url_prefix.rstrip("/") + href
    return urljoin(site["url"], href)


def _fetch_article_content(url, site, headers, timeout, cutoff=None) -> tuple[str, datetime | None]:
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    pub_date = _extract_pub_date(soup, url)
    content = _extract_content(soup, site.get("content_selector"))
    return content, pub_date


def _extract_content(soup: BeautifulSoup, selector: str | None) -> str:
    selectors = [selector] if selector else []
    selectors.extend(s for s in FALLBACK_CONTENT_SELECTORS if s not in selectors)
    for sel in selectors:
        try:
            paragraphs = [_clean_text(p.get_text(" ", strip=True)) for p in soup.select(sel)]
        except Exception:
            continue
        paragraphs = [p for p in paragraphs if _valid_paragraph(p)]
        text = " ".join(paragraphs)
        text = _clean_text(text)
        if len(text) >= 120:
            return text
    return ""




def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    for pattern in PROMO_SNIPPET_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _valid_paragraph(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    lowered = text.lower()
    if any(re.search(p, lowered, re.I) for p in PROMO_SNIPPET_PATTERNS):
        return False
    if lowered in {"explore now", "read more", "advertisement"}:
        return False
    return True

def _extract_pub_date(soup: BeautifulSoup, url: str) -> datetime | None:
    host = urlparse(url).netloc.lower()

    if "cruisemapper.com" in host:
        for sel in ["meta[property='article:published_time']", "meta[name='pubdate']", ".news-date", ".date", "time"]:
            tag = soup.select_one(sel)
            if tag:
                value = tag.get("content") or tag.get("datetime") or tag.get_text(" ", strip=True)
                dt = _try_parse_iso(value) or _try_parse_dateline(value)
                if dt:
                    return dt
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:expected arrival on|announced on|published on|updated on)\s+([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,\s+\d{4})", text)
        if m:
            cleaned = re.sub(r"(st|nd|rd|th),", ",", m.group(1))
            dt = _try_parse_iso(cleaned) or _try_parse_dateline(cleaned)
            if dt:
                return dt

    for prop in (
        "article:published_time",
        "article:modified_time",
        "og:updated_time",
        "datePublished",
        "pubdate",
        "publish-date",
        "parsely-pub-date",
    ):
        tag = (
            soup.find("meta", attrs={"property": prop})
            or soup.find("meta", attrs={"name": prop})
            or soup.find("meta", attrs={"itemprop": prop})
        )
        if tag and tag.get("content"):
            dt = _try_parse_iso(tag["content"])
            if dt:
                return dt

    for time_el in soup.find_all("time"):
        value = time_el.get("datetime") or time_el.get_text(" ", strip=True)
        dt = _try_parse_iso(value) or _try_parse_dateline(value)
        if dt:
            return dt

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for item in _walk_json(data):
            if isinstance(item, dict):
                for key in ("datePublished", "dateModified", "uploadDate"):
                    if key in item:
                        dt = _try_parse_iso(str(item[key]))
                        if dt:
                            return dt

    text = soup.get_text(" ", strip=True)[:4000]
    dt = _try_parse_dateline(text)
    if dt:
        return dt

    dt = _try_parse_dateline(url.replace("-", " ").replace("/", " "))
    return dt


def _walk_json(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_json(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json(item)


def _parse_feed_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        value = getattr(entry, attr, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        value = entry.get(attr)
        if value:
            dt = _try_parse_iso(value)
            if dt:
                return dt
            try:
                dt = parsedate_to_datetime(value)
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _extract_feed_content(entry) -> str:
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) >= 80:
                return text
    content = entry.get("content")
    if isinstance(content, list):
        for item in content:
            value = item.get("value")
            if value:
                text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) >= 80:
                    return text
    return ""


def _should_reject_article(site, title: str, url: str, content: str, pub_date: datetime | None, cutoff: datetime | None) -> bool:
    if _should_skip_url(url, site) or _should_skip_title(title, site):
        return True

    content = _clean_text(content)
    min_chars = site.get("min_content_chars", 120)
    if not content or len(content.strip()) < min_chars:
        return True

    min_paragraphs = site.get("min_paragraphs", 2)
    if content.count(". ") + content.count("! ") + content.count("? ") < min_paragraphs:
        return True

    if cutoff and pub_date and pub_date < cutoff:
        logger.debug("Skipping old article (%s): %s", pub_date.date(), url)
        return True

    allow_undated = site.get("allow_undated", False)
    if not pub_date and not allow_undated:
        logger.debug("Skipping undated article: %s", url)
        return True

    if site.get("filter_evergreen", True) and _looks_evergreen(title, url):
        logger.debug("Skipping evergreen article: %s", title)
        return True

    return False


def _should_skip_url(url: str, site: dict) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True

    allow_patterns = site.get("allow_url_patterns") or []
    if allow_patterns and not any(re.search(p, url, re.I) for p in allow_patterns):
        return True

    patterns = list(DEFAULT_BLOCK_URL_PATTERNS)
    patterns.extend(site.get("block_url_patterns") or [])
    return any(re.search(p, url, re.I) for p in patterns)


def _should_skip_title(title: str, site: dict) -> bool:
    title_norm = re.sub(r"\s+", " ", title).strip().lower()
    if len(title_norm) < 10:
        return True
    patterns = list(DEFAULT_BLOCK_TITLE_PATTERNS)
    patterns.extend(site.get("block_title_patterns") or [])
    return any(re.search(p, title_norm, re.I) for p in patterns)


def _looks_evergreen(title: str, url: str) -> bool:
    haystack = f"{title} {url}".lower()
    return any(re.search(p, haystack, re.I) for p in DEFAULT_EVERGREEN_TITLE_PATTERNS)


def _try_parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    value = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", value)
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    for candidate in (value, value.split(".")[0] if "." in value else value):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        dt = parsedate_to_datetime(value)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _try_parse_dateline(text: str) -> datetime | None:
    if not text:
        return None
    month_re = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    patterns = [
        rf"({month_re}\s+\d{{1,2}},\s+\d{{4}})",
        rf"(\d{{1,2}}\s+{month_re}\s+\d{{4}})",
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}/\d{2}/\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            dt = _try_parse_iso(m.group(1))
            if dt:
                return dt
    return None
