import requests

BOT_TOKEN = "8370574339:AAE_X_nxsveEfou7LnT5LMQcZsWNcpHOCr4"
CHAT_ID = "448738115"

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text
    })
