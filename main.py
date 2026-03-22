import json
import os
from datetime import date, timedelta
from pathlib import Path

from crawler import collect
from ai import summarize
from send import send_message, send_link

SEEN_FILE = "seen_links.json"
DIGEST_DIR = Path("digests")
DIGEST_FILE = DIGEST_DIR / "digest.json"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://YOUR_USERNAME.github.io/cruise-digest")


# ── Deduplication ──────────────────────────────────────────────────────────────

def load_seen() -> dict:
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


def save_digest(raw_items: list, ai_summary: str):
    """Save digest as both digests/digest.json (latest) and digests/digest-YYYY-MM-DD.json (history)."""
    DIGEST_DIR.mkdir(exist_ok=True)
    today = str(date.today())
    digest = {
        "date": today,
        "summary": ai_summary,
        "items": raw_items
    }
    payload = json.dumps(digest, ensure_ascii=False, indent=2)

    with open(DIGEST_FILE, "w", encoding="utf-8") as f:
        f.write(payload)

    dated_file = DIGEST_DIR / f"digest-{today}.json"
    if not dated_file.exists():
        with open(dated_file, "w", encoding="utf-8") as f:
            f.write(payload)

    print(f"[main] Saved {DIGEST_FILE} and {dated_file} ({len(raw_items)} items).")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    seen = load_seen()
    print(f"[main] Loaded {len(seen)} previously seen links.")

    new_items = collect(seen_links=set(seen.keys()))

    if not new_items:
        print("[main] No new items found.")
        save_digest([], "לא נמצאו חדשות חדשות היום.")
        send_message("בוקר טוב עוזי 🌅\nלא נמצאו חדשות חדשות היום בעולם הקרוזים.")
    else:
        print(f"[main] Sending {len(new_items[:25])} items to AI...")
        digest_text = summarize(new_items[:25])

        save_digest(new_items, digest_text)

        send_link(
            text="בוקר טוב עוזי 🌅\nהדיגסט של היום מוכן. לחץ כאן לצפייה וכתיבת כתבות:",
            url=DASHBOARD_URL,
            label="פתח דשבורד"
        )

        seen = mark_seen(seen, new_items)
        save_seen(seen)
        print(f"[main] Done. Saved {len(seen)} total seen links.")
