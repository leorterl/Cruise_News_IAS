import json
import os
from datetime import date, timedelta

from crawler import collect
from ai import summarize
from send import send_message

SEEN_FILE = "seen_links.json"


# ── Deduplication helpers ──────────────────────────────────────────────────────

def load_seen() -> dict:
    """
    Load seen links from disk.
    Returns a dict of {url: date_string}.
    Automatically drops links older than 2 days to keep the file small.
    """
    if not os.path.exists(SEEN_FILE):
        return {}

    with open(SEEN_FILE) as f:
        data = json.load(f)

    cutoff = str(date.today() - timedelta(days=2))
    return {link: d for link, d in data.items() if d >= cutoff}


def save_seen(seen: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def mark_seen(seen: dict, new_items: list) -> dict:
    today = str(date.today())
    for item in new_items:
        seen[item["link"]] = today
    return seen


# ── Main flow ──────────────────────────────────────────────────────────────────

def build_greeting() -> str:
    return (
        "בוקר טוב עוזי 🌅\n"
        "הנה החדשות החמות בעולם הקרוזים והתיירות:\n\n"
    )


if __name__ == "__main__":
    # 1. Load previously seen links
    seen = load_seen()
    print(f"[main] Loaded {len(seen)} previously seen links.")

    # 2. Crawl — skip already-seen links
    new_items = collect(seen_links=set(seen.keys()))

    if not new_items:
        print("[main] No new items found. Sending a short notice.")
        send_message("בוקר טוב עוזי 🌅\nלא נמצאו חדשות חדשות היום בעולם הקרוזים.")
    else:
        # 3. Summarize with AI
        print(f"[main] Sending {len(new_items)} items to AI...")
        digest = summarize(new_items)

        # 4. Prepend greeting
        full_message = build_greeting() + digest

        # 5. Send to Telegram
        send_message(full_message)

        # 6. Persist seen links so tomorrow we skip these
        seen = mark_seen(seen, new_items)
        save_seen(seen)
        print(f"[main] Done. Saved {len(seen)} total seen links.")