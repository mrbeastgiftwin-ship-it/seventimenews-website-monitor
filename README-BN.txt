# Seventimenews Website Monitor (free starter)

This project checks public RSS/Atom feeds or public listing pages once per hour and sends newly discovered links to a Telegram chat.

## Important limitations
- Use a **public GitHub repository** if you want GitHub-hosted Actions to run without using the private-repository monthly minutes quota. Public repository code is visible to everyone. Never put bot tokens or passwords in files.
- Telegram credentials must be added under GitHub Actions Secrets.
- The first successful run creates a baseline and does not alert on old posts.
- The HTML fallback looks for links in `main`, `[role=main]`, or `article` (or the whole body if those are absent). Some sites need a CSS selector in `websites.txt`.
- A website may block automated requests, require JavaScript, or change its layout. No monitor can promise perfect detection for every site.
- Respect each site's terms, robots guidance, and reasonable request rates. Do not monitor private or login-only content.
- GitHub scheduled workflows can be delayed and may be disabled in public repositories after 60 days without repository activity. Check Actions occasionally and manually run the workflow if needed.

## 1. Create a Telegram bot
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot`, follow the prompts, and copy the bot token.
3. Open your new bot's chat and press **Start** (or send `/start`).
4. In a browser, open this URL, replacing `YOUR_TOKEN` with your token:
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
5. Find `"chat":{"id":123456789...}` in the response. Copy that number as your chat ID.
6. Keep the token private. If it leaks, use BotFather to revoke/regenerate it.

## 2. Create a GitHub repository
1. Create a new repository named `seventimenews-website-monitor`.
2. Set it to **Public** to use standard GitHub-hosted Actions without consuming the private-repository minutes allowance. The code and website list will be public.
3. Add the files from this folder, keeping the same folder structure.
4. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
5. Add:
   - `TELEGRAM_BOT_TOKEN` = the token from BotFather
   - `TELEGRAM_CHAT_ID` = your chat ID number

## 3. Add websites
Edit `websites.txt`. Put one page URL on each line:
- Prefer RSS/Atom feed URLs where available.
- Otherwise use the page that lists the latest news/posts/jobs, not the site's homepage.
- Optional CSS selector format:
  `https://example.com/jobs | main .vacancy-list`
  The selector should point to the container that holds the post links.

## 4. Enable and test
1. Open the repository's **Actions** tab and enable workflows if GitHub asks.
2. Open **Seventimenews Website Monitor**.
3. Click **Run workflow** to test immediately.
4. The first run only records existing links. It does not send alerts for old posts.
5. After that, a newly appearing link should trigger a Telegram message on a later run.

## 5. Add 500 websites carefully
Start with 10–20 sites. Check the Actions run log and Telegram alerts for a few days. Fix selectors for noisy or missed sites, then add more in batches. 500 pages may take a while and some sites may block frequent checks. If runs take longer than an hour, change the cron interval to every 2 or 3 hours, or split the list into multiple batches/workflows.

## File list
- `monitor.py`: checks feeds and pages, detects newly seen links, sends Telegram messages.
- `websites.txt`: the list of pages to monitor.
- `.github/workflows/monitor.yml`: hourly GitHub Actions schedule.
- `requirements.txt`: Python dependencies.
- `state.json`: created/updated automatically after the first run; stores already-seen links.
