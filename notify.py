"""Post the daily card to a Discord webhook (set DISCORD_WEBHOOK_URL secret).

Chunks are paced and rate-limit aware: the old version fired every chunk back to
back and ignored the response, so a long card was silently dropped by Discord's
429s - the failure looked like "the bot posted nothing"."""
import os
import time

import requests

MAX_CHARS = 1900        # Discord's limit is 2000
GAP = 0.6               # seconds between chunks


def _send(url, content, tries=4):
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


def post(text):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("No DISCORD_WEBHOOK_URL set; printing instead:\n")
        print(text)
        return
    chunks, chunk = [], ""
    for line in text.split("\n"):
        while len(line) > MAX_CHARS:            # a single over-long line would never fit
            chunks.append(line[:MAX_CHARS])
            line = line[MAX_CHARS:]
        if len(chunk) + len(line) + 1 > MAX_CHARS:
            chunks.append(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        chunks.append(chunk)
    sent = 0
    for i, c in enumerate(chunks):
        if i:
            time.sleep(GAP)
        sent += bool(_send(url, c))
    print(f"[notify] posted {sent}/{len(chunks)} chunk(s)")
