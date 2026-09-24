# Tender Monitor

Monitors tender and procurement pages from **RBI**, **MCX**, and **NaBFID**, summarizes new postings using **Gemini AI**, and sends email alerts via **Gmail**.

## What it does

- Scrapes tenders from three sources:
  - [RBI](https://www.rbi.org.in/Scripts/Bs_ViewTenders.aspx) — Reserve Bank of India
  - [MCX](https://www.mcxindia.com/procurement-corner) — Multi Commodity Exchange
  - [NaBFID](https://nabfid.org/tenders) — National Bank for Financing Infrastructure and Development
- Detects new tenders by comparing against a local seen list
- Generates a plain-English summary of each tender using Google Gemini AI
- Sends a formatted HTML email with summaries, key dates, and PDF download links

## Prerequisites

- Python 3.9+
- A Gmail account with [App Password](https://myaccount.google.com/apppasswords) (requires 2-Step Verification enabled)
- A free [Google Gemini API key](https://aistudio.google.com/apikey)
- (macOS only) For MCX scraping, a display session is needed as it uses a non-headless browser

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/vivek-koul/tender-monitor.git
cd tender-monitor
```

### 2. Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Install Playwright browser (required for MCX)

```bash
playwright install chromium
```

### 4. Configure credentials

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
| `gmail_address` | Your Gmail address |
| `gmail_app_password` | [Google Account > Security > App Passwords](https://myaccount.google.com/apppasswords) — generate one for "Mail" |
| `notify_email` | Where to receive alerts (can be the same as `gmail_address`) |
| `gemini_api_key` | [Google AI Studio](https://aistudio.google.com/apikey) — click "Create API key" (free) |

### 5. Seed existing tenders

This marks all current tenders as "seen" so you only get alerts for new ones going forward:

```bash
python tender_monitor.py --seed
```

### 6. Test with a dry run

```bash
python tender_monitor.py --source rbi --dry-run --limit 2
```

This scrapes, summarizes, and saves an email preview to `rbi_preview.html` without sending anything. Open the preview to verify it looks right:

```bash
open rbi_preview.html   # macOS
xdg-open rbi_preview.html   # Linux
```

### 7. Run for real

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
| `--source {rbi,mcx,nabfid,all}` | Check a specific source (default: all) |
| `--last N` | Email the last N tenders from the selected source(s) |
| `--dry-run` | Preview only — saves HTML file, no email sent |
| `--seed` | Mark all current tenders as seen (run once during setup) |
| `--limit N` | Process at most N new tenders |

### Examples

```bash
# Check all three sources for new tenders
python tender_monitor.py

# Check only RBI
python tender_monitor.py --source rbi

# Email last 10 NaBFID tenders with summaries
python tender_monitor.py --source nabfid --last 10

# Preview last 5 MCX tenders without sending email
python tender_monitor.py --source mcx --last 5 --dry-run
```

## Scheduling (macOS)

To run automatically every Friday at 3 PM, create a launchd plist:

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

If your Mac is asleep at the scheduled time, the job runs as soon as it wakes up.

## Scheduling (Linux)

Add a cron job:

```bash
crontab -e
```

```
0 15 * * 5 cd /path/to/tender-monitor && .venv/bin/python tender_monitor.py >> monitor.log 2>&1
```

## Files

| File | Purpose |
|------|---------|
| `tender_monitor.py` | Main script |
| `config.json` | Your credentials (git-ignored, never committed) |
| `config.example.json` | Template for config.json |
| `*_seen_tenders.json` | Tracks processed tenders (auto-generated, git-ignored) |
| `monitor.log` | Output from scheduled runs (git-ignored) |
| `*_preview.html` | Email previews from `--dry-run` (git-ignored) |

## How it works

1. **Scrapes** the tender pages (RBI and NaBFID via HTTP requests, MCX via Playwright headless browser)
2. **Compares** scraped tender IDs against the local seen list
3. **Fetches** detail pages for new tenders (RBI only — has separate detail pages)
4. **Summarizes** each tender using Google Gemini AI — covering scope, who should apply, key dates, and estimated value
5. **Emails** a formatted HTML alert via Gmail SMTP
6. **Updates** the seen list so the same tender isn't reported twice
