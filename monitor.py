import os
import json
import time
import hashlib
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
URLS_FILE = ROOT / "websites.txt"
STATE_FILE = ROOT / "state.json"
TIMEOUT = 12
MAX_ITEMS_PER_SITE = 250
MAX_ALERTS_PER_SITE = 15
USER_AGENT = "SeventimenewsMonitor/1.0 (+polite public-page monitoring)"
SKIP_TEXT = {
    "home", "about", "about us", "contact", "contact us", "login", "log in",
    "sign in", "register", "privacy policy", "terms", "facebook", "instagram",
    "linkedin", "youtube", "twitter", "x"
}

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            print("Warning: state.json could not be read; starting with empty state.")
    return {}

def load_sites():
    sites = []
    for raw in URLS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Format: URL | optional CSS selector
        parts = line.split("|", 1)
        url = parts[0].strip()
        selector = parts[1].strip() if len(parts) > 1 else ""
        if url.startswith("http://") or url.startswith("https://"):
            sites.append((url, selector))
        else:
            print(f"Skipping invalid URL line: {line}")
    return sites

def clean_url(url):
    url, _fragment = urldefrag(url)
    return url.rstrip("/")

def fetch_site(site):
    page_url, selector = site
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(page_url, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "").lower()

        # Try RSS/Atom first. If the supplied URL is a feed, parse entries.
        parsed_feed = feedparser.parse(content)
        if parsed_feed.entries and (
            "xml" in content_type or "rss" in content_type or "atom" in content_type
            or b"<rss" in content[:1000].lower()
            or b"<feed" in content[:1000].lower()
        ):
            items = {}
            for entry in parsed_feed.entries[:MAX_ITEMS_PER_SITE]:
                link = entry.get("link", "")
                title = entry.get("title", "(untitled post)")
                if link:
                    items[clean_url(link)] = " ".join(str(title).split())[:180]
            return page_url, items, None

        soup = BeautifulSoup(content, "html.parser")
        if selector:
            selected = soup.select(selector)
            containers = selected
        else:
            containers = soup.select("main, [role='main'], article")
            if not containers:
                containers = [soup.body or soup]

        items = {}
        page_host = urlparse(page_url).netloc.lower().removeprefix("www.")
        for container in containers:
            for a in container.select("a[href]"):
                href = a.get("href", "").strip()
                title = " ".join(a.get_text(" ", strip=True).split())
                if not href or not title or len(title) < 3:
                    continue
                if title.lower() in SKIP_TEXT:
                    continue
                if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = clean_url(urljoin(page_url, href))
                parsed = urlparse(absolute)
                if parsed.scheme not in ("http", "https"):
                    continue
                if absolute == clean_url(page_url):
                    continue
                # Avoid obvious social/share links; keep other external links because
                # some job sites publish listings on external application platforms.
                host = parsed.netloc.lower()
                if any(x in host for x in ("facebook.com", "instagram.com", "linkedin.com",
                                           "youtube.com", "twitter.com", "tiktok.com")):
                    continue
                items[absolute] = title[:180]

        # Stable cap to keep the state file manageable.
        if len(items) > MAX_ITEMS_PER_SITE:
            items = dict(list(items.items())[:MAX_ITEMS_PER_SITE])
        return page_url, items, None
    except Exception as exc:
        return page_url, {}, f"{type(exc).__name__}: {exc}"

def send_telegram(token, chat_id, text):
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            api,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20
        )
        data = response.json()
        if not response.ok or not data.get("ok"):
            print(f"Telegram error: {data}")
            return False
        return True
    except Exception as exc:
        print(f"Telegram send failed: {exc}")
        return False

def chunks_for_telegram(site_url, new_items):
    header = f"🆕 New posts detected\\nSource: {site_url}\\n\\n"
    messages, current = [], header
    for link, title in new_items[:MAX_ALERTS_PER_SITE]:
        piece = f"• {title}\\n{link}\\n\\n"
        if len(current) + len(piece) > 3800:
            messages.append(current.rstrip())
            current = header
        current += piece
    if current.strip() != header.strip():
        messages.append(current.rstrip())
    remaining = len(new_items) - min(len(new_items), MAX_ALERTS_PER_SITE)
    if remaining > 0 and messages:
        messages[-1] += f"\\n…and {remaining} more new links were found on this page."
    return messages

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secrets.")

    sites = load_sites()
    if not sites:
        raise SystemExit("websites.txt has no valid website URLs.")

    state = load_state()
    first_run = not bool(state)
    print(f"Checking {len(sites)} website pages. First run: {first_run}")

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_site, site) for site in sites]
        for future in as_completed(futures):
            results.append(future.result())

    success_count = 0
    error_count = 0
    new_count = 0

    for page_url, current_items, error in results:
        if error:
            error_count += 1
            print(f"ERROR {page_url}: {error}")
            continue

        success_count += 1
        old_items = state.get(page_url, {})
        if not isinstance(old_items, dict):
            old_items = {}

        if first_run or page_url not in state:
            # First run creates a baseline and intentionally sends no old-post alerts.
            state[page_url] = current_items
            print(f"BASELINE {page_url}: saved {len(current_items)} links")
            continue

        new_items = [(link, title) for link, title in current_items.items()
                     if link not in old_items]
        if new_items:
            messages = chunks_for_telegram(page_url, new_items)
            all_sent = True
            for message in messages:
                if not send_telegram(token, chat_id, message):
                    all_sent = False
                    break
                time.sleep(0.4)
            if all_sent:
                new_count += len(new_items)
                state[page_url] = current_items
                print(f"NEW {page_url}: {len(new_items)} links")
            else:
                print(f"Will retry next run; Telegram delivery failed for {page_url}")
        else:
            state[page_url] = current_items
            print(f"OK {page_url}: no new links")

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"Finished. Successful pages: {success_count}; errors: {error_count}; new links: {new_count}")

if __name__ == "__main__":
    main()
