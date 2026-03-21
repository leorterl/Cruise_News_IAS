import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def send_message(text: str):
    """Send a plain text message to Telegram."""
    if len(text) > 4096:
        text = text[:4090] + "\n..."

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": CHAT_ID, "text": text})

    if not resp.ok:
        print("Telegram error:", resp.text)
    else:
        print("Message sent successfully.")


def send_link(text: str, url: str, label: str):
    """Send a Telegram message with an inline button that opens a URL."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(api_url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": label, "url": url}
            ]]
        }
    })

    if not resp.ok:
        print("Telegram error:", resp.text)
    else:
        print("Link message sent successfully.")
