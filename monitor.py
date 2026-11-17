
import os
import json
import time
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

GOOGLE_SHEET_WEBHOOK = os.environ.get(
    "GOOGLE_SHEET_WEBHOOK", ""
).strip()

SKIP_TEXT = {
    "home", "about", "about us", "contact", "contact us",
    "login", "log in", "sign in", "register", "privacy policy",
    "terms", "facebook", "instagram", "linkedin",
    "youtube", "twitter", "x"
}

COUNTRY_CODES = {
    "at": "Austria", "be": "Belgium", "bg": "Bulgaria",
    "hr": "Croatia", "cy": "Cyprus", "cz": "Czechia",
    "dk": "Denmark", "ee": "Estonia", "fi": "Finland",
    "fr": "France", "de": "Germany", "gr": "Greece",
    "hu": "Hungary", "ie": "Ireland", "it": "Italy",
    "lv": "Latvia", "lt": "Lithuania", "lu": "Luxembourg",
    "mt": "Malta", "nl": "Netherlands", "pl": "Poland",
    "pt": "Portugal", "ro": "Romania", "sk": "Slovakia",
    "si": "Slovenia", "es": "Spain", "se": "Sweden",
    "uk": "United Kingdom"
}

def infer_country(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    first = host.split(".")[0]

    if first in COUNTRY_CODES:
        return COUNTRY_CODES[first]

    parts = host.split(".")
    if parts:
        suffix = parts[-1]
        if suffix in COUNTRY_CODES:
            return COUNTRY_CODES[suffix]

    return "Unknown"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            print("Warning: state.json could not be read.")
    return {}


def load_sites():
    sites = []

    if not URLS_FILE.exists():
        raise SystemExit("ERROR: websites.txt not found.")

    content = URLS_FILE.read_text(encoding="utf-8-sig")

    for raw in content.splitlines():
        line = raw.strip().lstrip("\ufeff\u200b").strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split("|", 2)
        url = parts[0].strip()

        if not url.lower().startswith(("http://", "https://")):
            print(f"INVALID URL: {line}")
            continue

        country = infer_country(url)
        selector = ""

        if len(parts) == 2:
            second = parts[1].strip()

            if second.lower() in {
                name.lower() for name in COUNTRY_CODES.values()
            }:
                country = second
            else:
                selector = second

        elif len(parts) == 3:
            country = parts[1].strip() or country
            selector = parts[2].strip()

        sites.append((url, country, selector))

    print(f"Total valid websites: {len(sites)}")
    return sites


def clean_url(url):
    url, _fragment = urldefrag(url)
    return url.rstrip("/")


def fetch_site(site):
    page_url, country, selector = site
    headers = {"User-Agent": USER_AGENT}

    try:
        response = requests.get(
            page_url, headers=headers, timeout=TIMEOUT
        )
        response.raise_for_status()

        content = response.content
        content_type = response.headers.get(
            "content-type", ""
        ).lower()

        parsed_feed = feedparser.parse(content)

        if parsed_feed.entries and (
            "xml" in content_type
            or "rss" in content_type
            or "atom" in content_type
            or b"<rss" in content[:1000].lower()
            or b"<feed" in content[:1000].lower()
        ):
            items = {}

            for entry in parsed_feed.entries[:MAX_ITEMS_PER_SITE]:
                link = entry.get("link", "")
                title = entry.get("title", "(untitled post)")

                if link:
                    items[clean_url(link)] = (
                        " ".join(str(title).split())[:180]
                    )

            return page_url, country, items, None

        soup = BeautifulSoup(content, "html.parser")

        if selector:
            containers = soup.select(selector)
        else:
            containers = soup.select(
                "main, [role='main'], article"
            )

            if not containers:
                containers = [soup.body or soup]

        items = {}

        for container in containers:
            for a in container.select("a[href]"):
                href = a.get("href", "").strip()
                title = " ".join(
                    a.get_text(" ", strip=True).split()
                )

                if not href or not title or len(title) < 3:
                    continue

                if title.lower() in SKIP_TEXT:
                    continue

                if href.startswith(
                    ("#", "mailto:", "tel:", "javascript:")
                ):
                    continue

                absolute = clean_url(urljoin(page_url, href))
                parsed = urlparse(absolute)

                if parsed.scheme not in ("http", "https"):
                    continue

                if absolute == clean_url(page_url):
                    continue

                host = parsed.netloc.lower()

                if any(x in host for x in (
                    "facebook.com", "instagram.com",
                    "linkedin.com", "youtube.com",
                    "twitter.com", "tiktok.com"
                )):
                    continue

                items[absolute] = title[:180]

        if len(items) > MAX_ITEMS_PER_SITE:
            items = dict(
                list(items.items())[:MAX_ITEMS_PER_SITE]
            )

        return page_url, country, items, None

    except Exception as exc:
        return page_url, country, {}, (
            f"{type(exc).__name__}: {exc}"
        )


def send_google_sheet(country, website, title, link):
    if not GOOGLE_SHEET_WEBHOOK:
        print("ERROR: GOOGLE_SHEET_WEBHOOK is missing.")
        return False

    payload = {
        "date": time.strftime(
            "%Y-%m-%d %H:%M:%S UTC", time.gmtime()
        ),
        "country": country,
        "website": website,
        "title": title,
        "link": link
    }

    try:
        response = requests.post(
            GOOGLE_SHEET_WEBHOOK,
            json=payload,
            timeout=30
        )
        response.raise_for_status()

        result = response.json()

        if result.get("success"):
            if result.get("duplicate"):
                print(f"DUPLICATE: {link}")
            else:
                print(f"SHEET ADDED: {title}")
            return True

        print(f"Google Sheets error: {result}")
        return False

    except Exception as exc:
        print(f"Google Sheets send failed: {exc}")
        return False


def send_telegram(token, chat_id, message):
    api = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            api,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True
            },
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


def main():
    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip()

    chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    ).strip()

    if not GOOGLE_SHEET_WEBHOOK:
        raise SystemExit(
            "Missing GOOGLE_SHEET_WEBHOOK GitHub Secret."
        )

    if not token or not chat_id:
        print(
            "Telegram credentials missing. "
            "Google Sheets will still be used."
        )

    sites = load_sites()

    if not sites:
        raise SystemExit(
            "websites.txt has no valid website URLs."
        )

    state = load_state()
    first_run = not bool(state)

    print(
        f"Checking {len(sites)} websites. "
        f"First run: {first_run}"
    )

    results = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(fetch_site, site)
            for site in sites
        ]

        for future in as_completed(futures):
            results.append(future.result())

    success_count = 0
    error_count = 0
    new_count = 0

    for page_url, country, current_items, error in results:

        if error:
            error_count += 1
            print(f"ERROR {page_url}: {error}")
            continue

        success_count += 1

        old_items = state.get(page_url, {})

        if not isinstance(old_items, dict):
            old_items = {}

        if first_run or page_url not in state:
            state[page_url] = current_items
            print(
                f"BASELINE {page_url}: "
                f"saved {len(current_items)} links"
            )
            continue

        new_items = [
            (link, title)
            for link, title in current_items.items()
            if link not in old_items
        ]

        if not new_items:
            state[page_url] = current_items
            print(f"OK {page_url}: no new links")
            continue

        all_sheet_sent = True

        # Every new job gets its own Google Sheets row.
        for link, title in new_items:
            if not send_google_sheet(
                country, page_url, title, link
            ):
                all_sheet_sent = False
                break

            new_count += 1

        if not all_sheet_sent:
            print(
                f"WILL RETRY: Google Sheets failed for "
                f"{page_url}"
            )
            continue

        # Telegram remains enabled during testing.
        if token and chat_id:
            for link, title in new_items[:MAX_ALERTS_PER_SITE]:
                message = (
                    "🆕 New Job Post\n"
                    f"Country: {country}\n"
                    f"Source: {page_url}\n\n"
                    f"📌 {title}\n\n"
                    f"🔗 Apply / View Job:\n{link}"
                )

                send_telegram(token, chat_id, message)
                time.sleep(0.4)

        if len(new_items) > MAX_ALERTS_PER_SITE:
            print(
                f"Telegram alert limit: "
                f"{len(new_items) - MAX_ALERTS_PER_SITE} "
                f"additional posts not sent to Telegram."
            )

        state[page_url] = current_items

        print(
            f"NEW {page_url}: "
            f"{len(new_items)} links processed"
        )

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"Finished. Successful pages: {success_count}; "
        f"errors: {error_count}; "
        f"Google Sheets jobs processed: {new_count}"
    )


if __name__ == "__main__":
    main()
