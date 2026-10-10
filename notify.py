"""Post the daily card to Discord webhooks.

Supports multiple webhooks (one per channel). Falls back to DISCORD_WEBHOOK_URL
if a specific one is not set. Chunks are paced and rate-limit aware.
"""
import os
import time

import requests

MAX_CHARS = 1900        # Discord's limit is 2000
GAP = 0.6               # seconds between chunks


def _send(url, content, tries=4):
    if not url:
        return False
    for i in range(tries):
        try:
            r = requests.post(url, json={"content": content}, timeout=20)
        except requests.RequestException as e:
            print(f"[notify] post failed ({type(e).__name__}); retrying")
            time.sleep(1.5 * (i + 1))
            continue
        if r.status_code == 429:
            wait = 1.0
            try:
                wait = float(r.json().get("retry_after", 1.0))
            except (ValueError, AttributeError):
                pass
            print(f"[notify] rate limited, waiting {wait:.1f}s")
            time.sleep(min(wait + 0.25, 30))
            continue
        if r.status_code >= 400:
            print(f"[notify] Discord returned {r.status_code}: {r.text[:160]}")
            return False
        return True
    print("[notify] gave up on a chunk after retries")
    return False


def post(text, webhook_key=None):
    """Post text to Discord.

    webhook_key: optional short name (e.g. "ncaaf", "strong", "records").
    Looks up DISCORD_WEBHOOK_<KEY> first, then falls back to DISCORD_WEBHOOK_URL.
    """
    url = None
    if webhook_key:
        url = os.environ.get(f"DISCORD_WEBHOOK_{webhook_key.upper()}", "").strip()
    if not url:
        url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print(f"No webhook for {webhook_key or 'default'}; printing instead:\\n")
        print(text)
        return

    chunks, chunk = [], ""
    for line in text.split("\\n"):
        while len(line) > MAX_CHARS:
            chunks.append(line[:MAX_CHARS])
            line = line[MAX_CHARS:]
        if len(chunk) + len(line) + 1 > MAX_CHARS:
            chunks.append(chunk)
            chunk = ""
        chunk += line + "\\n"
    if chunk.strip():
        chunks.append(chunk)
    sent = 0
    for i, c in enumerate(chunks):
        if i:
            time.sleep(GAP)
        sent += bool(_send(url, c))
    print(f"[notify] posted {sent}/{len(chunks)} chunk(s) to {webhook_key or 'default'}")
