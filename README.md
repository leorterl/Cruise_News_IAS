# 🚢 עדכון יומי IAS — Cruise News Digest

An automated daily cruise news pipeline that crawls 30+ industry sources every morning, clusters and summarizes the top stories in Hebrew using AI, and delivers them to a web dashboard where an editor can generate full SEO-ready articles in one click.

---

## What it does

Every morning at 03:00 (Israel time), the system:

1. **Crawls** 30+ cruise and travel news sites
2. **Deduplicates** links seen in the past 48 hours
3. **Summarizes** the day's stories into a Hebrew digest using Gemini AI
4. **Saves** the digest to the repo so the dashboard can read it
5. **Notifies** the editor via Telegram with a link to the dashboard
6. **Deploys** the updated dashboard automatically to GitHub Pages

The editor opens the dashboard, reads the digest, picks a story, clicks "כתוב כתבה", and gets a full Hebrew article with 3 title options, a deck sentence, tags, and a meta description — ready to copy-paste into WordPress.

---

## Architecture

```
GitHub Actions (runs at 03:00 IST)
│
├── crawler.py        → scrapes 30+ sites
├── ai.py             → Gemini clusters + summarizes
├── main.py           → orchestrates the flow
└── send.py           → Telegram notification with dashboard link
         │
         ▼
    digest.json       → saved to repo (today's stories + raw items)
    digest-YYYY-MM-DD.json  → archived dated copies (7-day history)
    seen_links.json   → deduplication memory
         │
         ▼
GitHub Pages → index.html (the dashboard)
```

---

## File Structure

```
cruise-digest/
├── main.py                  # Orchestrator — runs the full pipeline
├── crawler.py               # Multi-strategy news crawler
├── ai.py                    # Gemini summarization
├── send.py                  # Telegram delivery
├── config.yaml              # Site list and crawler settings
├── index.html               # The web dashboard (served via GitHub Pages)
├── digest.json              # Today's digest (auto-generated)
├── digest-YYYY-MM-DD.json   # Daily archives (auto-generated)
├── seen_links.json          # Deduplication store (auto-generated)
└── .github/
    └── workflows/
        └── daily_digest.yml # GitHub Actions schedule
```

---

## The Crawler (`crawler.py`)

Sites are categorized in `config.yaml` by how they need to be scraped. Four strategies are used:

| Strategy | Library | Used for |
|---|---|---|
| `rss` | feedparser | Sites with RSS feeds — fastest and most reliable |
| `html` | requests + BeautifulSoup | Simple sites that don't block bots |
| `stealth` | curl_cffi | Sites that return 403 to normal requests — impersonates a real Chrome browser |
| `playwright` | patchright | JavaScript-rendered SPAs that require a full browser to load |

Each site in `config.yaml` is tagged with its type, CSS selectors for finding article links and content, and an optional `max_articles` cap. The crawler skips any URL already in `seen_links.json`, so the same story is never sent twice.

---

## The AI Layer (`ai.py`)

After crawling, all new articles are sent to **Gemini 2.0 Flash**. The prompt instructs it to:

- Group articles that cover the same story
- Write a Hebrew summary (2–3 sentences) for each cluster
- Format output as `🚢 Story title` blocks the dashboard can parse
- Avoid AI writing patterns (no "pivotal moment", no hollow conclusions)

The same Gemini API key is used for article generation in the browser (see Dashboard below).

---

## The Dashboard (`index.html`)

A single-file web app served via GitHub Pages. No server required.

**Features:**

- **Stories tab** — AI-clustered digest for the day, sorted by source priority
- **Sources tab** — all raw links collected that day, unprocessed, with search/filter
- **7-day archive** — sidebar lets the editor navigate back up to 7 days
- **Article generation** — click "✍️ כתוב כתבה" on any story → Gemini writes a full Hebrew article in ~15 seconds
- **Site selector** — choose IAS or CruiseIn before generating; each gets its own closing text automatically appended
- **Copy buttons** — separate copy buttons for titles, deck, article body, tags, and meta description
- **Source links** — original URLs shown in the article modal for fact-checking
- **Light/dark mode** — toggle saved in browser localStorage
- **API key storage** — Gemini key stored in browser localStorage, never in the repo

**Article output for each story:**
- 3 headline options
- Deck sentence (sub-headline)
- Full article (450+ words, with H2 structure, SEO-optimized)
- Tags (comma-separated)
- Meta description (max 155 characters with live character count)
- Site-specific closing text (IAS or CruiseIn)

---

## Setup

### Prerequisites

- A GitHub account
- A [Gemini API key](https://aistudio.google.com) (free tier works)
- A Telegram bot token and chat ID

### 1. Fork or clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/cruise-digest.git
cd cruise-digest
```

### 2. Add GitHub Secrets

Go to **Settings → Secrets and Variables → Actions** and add:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | Your Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat/user ID |

### 3. Enable GitHub Pages

Go to **Settings → Pages → Source** and set it to **GitHub Actions**.

### 4. Enable workflow write permissions

Go to **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**.

### 5. Install dependencies (for local testing)

```bash
pip install requests beautifulsoup4 feedparser pyyaml google-genai curl_cffi patchright
patchright install chromium --with-deps
```

### 6. Run locally

```bash
python main.py
```

Or trigger the GitHub Actions workflow manually from the **Actions** tab → **Daily Cruise Digest** → **Run workflow**.

---

## Configuration (`config.yaml`)

```yaml
settings:
  time_window_hours: 24     # Only collect articles from the last N hours
  request_timeout: 15       # Seconds before a site request times out
  max_articles: 3           # Max articles per site (keeps digest balanced)
  user_agent: "Mozilla/5.0 ..."

sites:
  - name: "Royal Caribbean Blog"
    url: "https://www.royalcaribbeanblog.com/"
    type: "rss"
    rss_url: "https://www.royalcaribbeanblog.com/rss.xml"
    content_selector: ".field-item p"
```

To disable a site without removing it, add `enabled: false` to its entry.

---

## Dashboard Setup (first time)

1. Open the dashboard at `https://YOUR_USERNAME.github.io/cruise-digest`
2. Click **⚙️ הגדרות סיומות** (Settings)
3. Paste your Gemini API key and save
4. Optionally update the IAS and CruiseIn closing texts

The Gemini key is stored only in your browser. It is never committed to the repo.

---

## How deduplication works

After each run, `seen_links.json` is updated with all collected URLs and today's date. On the next run, any URL already in this file is skipped. Links older than 48 hours are automatically pruned so the file stays small. The file is committed back to the repo after each run so the memory persists between GitHub Actions jobs.

---

## Telegram notification

The editor receives a morning message:

> בוקר טוב עוזי 🌅
> הדיגסט של היום מוכן. לחץ כאן לצפייה וכתיבת כתבות:
> **[פתח דשבורד]**

Clicking the button opens the dashboard directly.

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | Standard HTTP requests |
| `beautifulsoup4` | HTML parsing |
| `feedparser` | RSS feed parsing |
| `pyyaml` | Reading `config.yaml` |
| `google-genai` | Gemini API (server-side summarization) |
| `curl_cffi` | Stealth HTTP — bypasses bot detection |
| `patchright` | Playwright fork — renders JS-heavy sites |

---

## Limitations

- **GitHub Actions cron** can run 15–30 minutes late. This is a GitHub limitation, not a bug. If timing is critical, a VPS with a real cron job is more reliable.
- **Sites that block GitHub IPs** — some sites detect cloud provider IP ranges even with stealth mode. These will fail silently and log a warning.
- **Playwright on GitHub Actions** adds ~3–5 minutes to each run for the Chromium install. This is expected.
- **Article generation requires a Gemini API key** saved in the editor's browser. It does not use the server-side key from GitHub Secrets.
