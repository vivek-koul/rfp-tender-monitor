# Tender Monitor

A Python script that monitors government and institutional tender/procurement pages for new postings, generates plain-English summaries using **Gemini AI**, and sends email alerts via **Gmail**.

## What it does

- Scrapes tender/procurement pages from multiple configurable sources
- Detects new tenders by comparing against a local seen list
- Generates a concise AI summary of each tender using Google Gemini
- Sends a formatted HTML email with summaries, key dates, and PDF download links
- Supports scheduling via macOS `launchd` or Linux `cron`

## Prerequisites

- Python 3.9+
- A Gmail account with [App Password](https://myaccount.google.com/apppasswords) (requires 2-Step Verification enabled)
- A free [Google Gemini API key](https://aistudio.google.com/apikey)
- (Optional) If any of your sources use a CDN/WAF that blocks automated requests, Playwright is needed for browser-based scraping

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/vivek-koul/rfp-tender-monitor.git
cd rfp-tender-monitor
```

### 2. Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Install Playwright browser (needed for WAF-protected sites)

```bash
playwright install chromium
```

### 4. Configure your tender sources

Open `tender_monitor.py` and edit the `SOURCES` dictionary at the top of the file. Each source needs:

```python
SOURCES = {
    "source1": {
        "name": "Source1",                    # Short display name
        "full_name": "Full Organization Name", # Used in logs
        "url": "https://example.com/tenders",  # URL to scrape
        "seen_file": SCRIPT_DIR / "source1_seen_tenders.json",  # State file
        "color": "#003366",                    # Email header color (hex)
    },
    "source2": {
        "name": "Source2",
        "full_name": "Another Organization",
        "url": "https://example.org/procurement",
        "seen_file": SCRIPT_DIR / "source2_seen_tenders.json",
        "color": "#1a5276",
    },
}
```

For each source, you also need to ensure there is a matching **scraper function** and **summarizer function**. Look for these sections in the script:

| What to customize | Where in the script | What it does |
|-------------------|---------------------|-------------|
| `SOURCES` dict | Top of file (~line 35) | Source names, URLs, colors |
| `scrape_<source>()` | Scraping section | Parses the HTML structure of that site |
| `summarize_<source>()` | AI Summarization section | Gemini prompt tailored to that source |
| `fallback_<source>()` | Below each summarizer | Fallback text when AI is unavailable |
| `build_email_html()` | Email section | HTML template for each source's tender cards |
| `run_source()` | Source Runners section | Maps source key to its scraper and summarizer |
| `seed_source()` | Below `run_source()` | Seeds initial seen list |
| `main()` argument parser | Bottom of file | `--source` choices list and default sources list |

### 5. Configure credentials

```bash
cp config.example.json config.json
```

Edit `config.json` with your details:

```json
{
  "gmail_address": "your-email@gmail.com",
  "gmail_app_password": "your-16-char-app-password",
  "notify_email": "recipient@gmail.com",
  "gemini_api_key": "your-gemini-api-key"
}
```

| Field | How to get it |
|-------|--------------|
| `gmail_address` | Your Gmail address (used to send the email) |
| `gmail_app_password` | [Google Account > Security > App Passwords](https://myaccount.google.com/apppasswords) — generate one for "Mail". This is a 16-character code, NOT your Gmail password |
| `notify_email` | Where to receive alerts (can be the same as `gmail_address`) |
| `gemini_api_key` | [Google AI Studio](https://aistudio.google.com/apikey) — click "Create API key" (free, 15 req/min, 1M tokens/day) |

### 6. Seed existing tenders

This marks all current tenders as "seen" so you only get alerts for new ones going forward:

```bash
python tender_monitor.py --seed
```

### 7. Test with a dry run

```bash
python tender_monitor.py --source source1 --dry-run --limit 2
```

This scrapes, summarizes, and saves an email preview to `source1_preview.html` without sending anything. Open the preview to verify:

```bash
open source1_preview.html   # macOS
xdg-open source1_preview.html   # Linux
```

### 8. Run for real

```bash
python tender_monitor.py
```

## Usage

```
python tender_monitor.py [options]
```

| Option | Description |
|--------|-------------|
| (no options) | Check all sources for new tenders, email if found |
| `--source {source1,source2,...,all}` | Check a specific source (default: all) |
| `--last N` | Email the last N tenders from the selected source(s) |
| `--dry-run` | Preview only — saves HTML file, no email sent |
| `--seed` | Mark all current tenders as seen (run once during setup) |
| `--limit N` | Process at most N new tenders |

### Examples

```bash
# Check all sources for new tenders
python tender_monitor.py

# Check only one source
python tender_monitor.py --source source1

# Email last 10 tenders from a source
python tender_monitor.py --source source2 --last 10

# Preview without sending email
python tender_monitor.py --source source1 --last 5 --dry-run
```

## Adding a new source

To add a new tender source:

1. Add an entry to the `SOURCES` dict with name, URL, seen file, and color
2. Write a `scrape_<source>()` function that returns a list of tender dicts with at least: `source`, `id`, `title`, `pub_date`, `last_date`, `pdf_link`
3. Write a `summarize_<source>()` function with a Gemini prompt tailored to that source
4. Write a `fallback_<source>()` function for when AI is unavailable
5. Add the source to `build_email_html()` for email formatting
6. Add the source to the scraper/summarizer dispatch in `run_source()`
7. Add the source key to the `--source` choices and default list in `main()`

Each tender dict must have a unique `id` field — this is how the script tracks what it has already seen.

## Scheduling (macOS)

Create a launchd plist to run automatically (e.g. every Friday at 3 PM):

```bash
cat > ~/Library/LaunchAgents/com.tender-monitor.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tender-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/tender-monitor/.venv/bin/python</string>
        <string>/path/to/tender-monitor/tender_monitor.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/tender-monitor</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>5</integer>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/path/to/tender-monitor/monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/tender-monitor/monitor.log</string>
</dict>
</plist>
EOF
```

Replace `/path/to/tender-monitor` with your actual path, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.tender-monitor.plist
```

**Schedule options** — edit the `StartCalendarInterval` section:

| Key | Value | Meaning |
|-----|-------|---------|
| `Weekday` | 1=Mon, 2=Tue ... 5=Fri, 7=Sun | Day of week (remove for daily) |
| `Hour` | 0-23 | Hour in your system timezone |
| `Minute` | 0-59 | Minute |

If your Mac is asleep at the scheduled time, the job runs as soon as it wakes up.

## Scheduling (Linux)

```bash
crontab -e
```

```
# Every Friday at 3 PM
0 15 * * 5 cd /path/to/tender-monitor && .venv/bin/python tender_monitor.py >> monitor.log 2>&1
```

Note: Unlike macOS launchd, cron does NOT catch up missed jobs after sleep.

## Files

| File | Purpose | On GitHub? |
|------|---------|------------|
| `tender_monitor.py` | Main script | Yes |
| `config.example.json` | Template for credentials | Yes |
| `requirements.txt` | Python dependencies | Yes |
| `.gitignore` | Keeps secrets out of git | Yes |
| `config.json` | Your credentials | No (git-ignored) |
| `*_seen_tenders.json` | Tracks processed tenders (auto-generated) | No (git-ignored) |
| `*_preview.html` | Email previews from `--dry-run` | No (git-ignored) |
| `monitor.log` | Output from scheduled runs | No (git-ignored) |

## How it works

1. **Scrapes** the configured tender pages (via HTTP requests or Playwright for WAF-protected sites)
2. **Compares** scraped tender IDs against the local seen list
3. **Fetches** detail pages for additional context (where available)
4. **Summarizes** each tender using Google Gemini AI — covering scope, who should apply, key dates, and estimated value
5. **Emails** a formatted HTML alert via Gmail SMTP
6. **Updates** the seen list so the same tender isn't reported twice
