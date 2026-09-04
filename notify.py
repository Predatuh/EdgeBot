"""Post the daily card to a Discord webhook (set DISCORD_WEBHOOK_URL secret)."""
import os
import requests


def post(text):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("No DISCORD_WEBHOOK_URL set; printing instead:\n")
        print(text)
        return
    # Discord max 2000 chars per message
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 1900:
            requests.post(url, json={"content": chunk}, timeout=20)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        requests.post(url, json={"content": chunk}, timeout=20)
