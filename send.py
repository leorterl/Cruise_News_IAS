import requests

BOT_TOKEN = "8370574339:AAE_X_nxsveEfou7LnT5LMQcZsWNcpHOCr4"
CHAT_ID = "448738115"

def send_message(text):
    print("Sending Telegram message to", CHAT_ID)

    # Telegram max message length is 4096 chars — truncate if needed
    if len(text) > 4096:
        text = text[:4090] + "\n..."

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text
    })

    if not resp.ok:
        print("Telegram error:", resp.text)
    else:
        print("Message sent successfully.")