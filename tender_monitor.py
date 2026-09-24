#!/usr/bin/env python3
"""
Tender Monitor
Monitors multiple tender/procurement pages, summarizes new postings
using Gemini AI, and sends email notifications via Gmail.
"""

import argparse
import json
import logging
import os
import smtplib
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

SOURCES = {
    "rbi": {
        "name": "RBI",
        "full_name": "Reserve Bank of India",
        "url": "https://www.rbi.org.in/Scripts/Bs_ViewTenders.aspx",
        "seen_file": SCRIPT_DIR / "seen_tenders.json",
        "color": "#003366",
    },
    "mcx": {
        "name": "MCX",
        "full_name": "Multi Commodity Exchange",
        "url": "https://www.mcxindia.com/procurement-corner",
        "seen_file": SCRIPT_DIR / "mcx_seen_tenders.json",
        "color": "#1a5276",
    },
    "nabfid": {
        "name": "NaBFID",
        "full_name": "National Bank for Financing Infrastructure and Development",
        "url": "https://nabfid.org/tenders",
        "seen_file": SCRIPT_DIR / "nabfid_seen_tenders.json",
        "color": "#0d6938",
    },
}

RBI_BASE_URL = "https://www.rbi.org.in/Scripts/"


# --- Config & State ---

def load_config():
    if not CONFIG_FILE.exists():
        log.error(f"Config file not found: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_seen(source):
    path = SOURCES[source]["seen_file"]
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_seen(source, seen):
    path = SOURCES[source]["seen_file"]
    with open(path, "w") as f:
        json.dump(seen, f, indent=2)


# --- RBI Scraping ---

def scrape_rbi():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(SOURCES["rbi"]["url"], headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tenders = []

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        title_link = cells[1].find("a") if len(cells) > 1 else None
        if not title_link or not title_link.get("href"):
            continue
        href = title_link["href"]
        if "Id=" not in href:
            continue

        tender_id = href.split("Id=")[-1].strip()
        pdf_link = ""
        pdf_size = ""
        if len(cells) > 3:
            pdf_anchor = cells[3].find("a")
            if pdf_anchor and pdf_anchor.get("href"):
                pdf_link = pdf_anchor["href"]
                pdf_size = cells[3].get_text(strip=True)

        detail_url = f"{RBI_BASE_URL}{href}" if not href.startswith("http") else href

        tenders.append({
            "source": "rbi",
            "id": tender_id,
            "title": title_link.get_text(strip=True),
            "pub_date": cells[0].get_text(strip=True),
            "last_date": cells[2].get_text(strip=True) if len(cells) > 2 else "",
            "detail_url": detail_url,
            "pdf_link": pdf_link,
            "pdf_size": pdf_size,
        })

    log.info(f"[RBI] Scraped {len(tenders)} tenders")
    return tenders


def fetch_rbi_detail(detail_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(detail_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        content_div = soup.find("div", {"id": "example"}) or soup.find("div", class_="tablecontent")
        if not content_div:
            for div in soup.find_all("div"):
                if len(div.get_text(strip=True)) > 200:
                    content_div = div
                    break

        if content_div:
            return content_div.get_text(separator="\n", strip=True)
        body = soup.find("body")
        return body.get_text(separator="\n", strip=True)[:3000] if body else ""
    except Exception as e:
        log.warning(f"[RBI] Failed to fetch detail page: {e}")
        return ""


# --- MCX Scraping ---

def scrape_mcx():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto(SOURCES["mcx"]["url"], wait_until="networkidle", timeout=45000)
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    tenders = []

    for div in soup.find_all("div", class_="procurement-content"):
        rfp_span = div.find("span", class_="id")
        rfp_no = rfp_span.get_text(strip=True) if rfp_span else ""
        if not rfp_no:
            continue

        dates = div.find_all("span", class_="date")
        start_date = dates[0].get_text(strip=True) if len(dates) > 0 else ""
        end_date = dates[1].get_text(strip=True) if len(dates) > 1 else ""

        desc_tag = div.find("p", class_="desc-sec")
        if desc_tag:
            desc_span = desc_tag.find("span", class_="desc")
            description = desc_span.get_text(strip=True) if desc_span else desc_tag.get_text(strip=True)
        else:
            all_text = div.get_text(separator="|", strip=True)
            parts = all_text.split("Description:")
            description = parts[1].split("|")[0].strip() if len(parts) > 1 else ""

        pdf_link = ""
        apply_link = ""
        contact = ""
        for a in div.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            if text == "View Pdf":
                pdf_link = href
            elif text == "Apply":
                apply_link = f"https://www.mcxindia.com{href}" if href.startswith("/") else href

        remarks_text = div.get_text(separator="|", strip=True)
        if "Remarks:" in remarks_text:
            contact = remarks_text.split("Remarks:")[1].split("Note")[0].strip().strip("|")

        tenders.append({
            "source": "mcx",
            "id": rfp_no,
            "title": description,
            "rfp_no": rfp_no,
            "pub_date": start_date,
            "last_date": end_date,
            "pdf_link": pdf_link,
            "apply_link": apply_link,
            "contact": contact,
        })

    log.info(f"[MCX] Scraped {len(tenders)} RFPs")
    return tenders


# --- NaBFID Scraping ---

def scrape_nabfid():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(SOURCES["nabfid"]["url"], headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tenders = []

    for item in soup.find_all("div", class_="tender-item"):
        title_tag = item.find("h2", class_="tender-title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        pub_date = ""
        last_date = ""
        date_div = item.find("div", class_="important-date")
        if date_div:
            date_text = date_div.get_text(separator="|", strip=True)
            for part in date_text.split("|"):
                part = part.strip()
                if "Publish Date:" in part:
                    pub_date = part.replace("Publish Date:", "").strip()
                elif "Last Date:" in part:
                    last_date = part.replace("Last Date:", "").strip()

        pdf_link = ""
        pdf_size = ""
        for a in item.find_all("a", href=True):
            if "Download" in a.get_text(strip=True):
                pdf_link = a["href"]
                views_div = a.find_parent("div", class_="views")
                if views_div:
                    size_text = views_div.get_text(strip=True)
                    if "KB" in size_text or "MB" in size_text:
                        for part in size_text.split():
                            if "KB" in part or "MB" in part:
                                pdf_size = part
                break

        tender_id = title[:50].strip().replace(" ", "_").lower()
        if pub_date:
            tender_id = f"{pub_date}_{tender_id}"

        tenders.append({
            "source": "nabfid",
            "id": tender_id,
            "title": title,
            "pub_date": pub_date,
            "last_date": last_date,
            "pdf_link": pdf_link,
            "pdf_size": pdf_size,
            "detail_url": SOURCES["nabfid"]["url"],
        })

    log.info(f"[NaBFID] Scraped {len(tenders)} tenders")
    return tenders


# --- AI Summarization ---

def get_gemini_client(config):
    from google import genai
    api_key = config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def call_gemini(client, prompt):
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
            )
            return response.text
        except Exception as e:
            if ("503" in str(e) or "429" in str(e)) and attempt < 2:
                wait = 15 * (attempt + 1)
                log.info(f"Gemini busy, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def summarize_rbi(tender, detail_text, client):
    if not client:
        return fallback_rbi(tender, detail_text)
    try:
        prompt = f"""Analyze this RBI (Reserve Bank of India) tender and provide a concise brief.

TENDER TITLE: {tender['title']}
PUBLISHED: {tender['pub_date']}
LAST DATE FOR SUBMISSION: {tender['last_date']}

DETAIL PAGE CONTENT:
{detail_text[:4000]}

Provide a brief summary covering:
1. **What it's about**: One-line plain-English summary of what RBI is looking for
2. **Scope**: Key deliverables or services required
3. **Who should apply**: Type of vendor/organization this is relevant for
4. **Key dates**: Submission deadline and any other important dates
5. **Location**: Where the work/supply is needed
6. **Estimated value**: If mentioned, otherwise say "Not disclosed"

Keep it concise — aim for 150-200 words total. Use plain language, avoid bureaucratic jargon."""
        return call_gemini(client, prompt)
    except Exception as e:
        log.warning(f"[RBI] AI summarization failed: {e}")
        return fallback_rbi(tender, detail_text)


def fallback_rbi(tender, detail_text):
    lines = [l.strip() for l in detail_text.split("\n") if l.strip()]
    preview = "\n".join(lines[:20])
    return f"Title: {tender['title']}\nDeadline: {tender['last_date']}\n\nDetails:\n{preview}"


def summarize_mcx(tender, client):
    if not client:
        return fallback_mcx(tender)
    try:
        prompt = f"""Analyze this MCX (Multi Commodity Exchange of India) procurement RFP and provide a concise brief.

RFP NUMBER: {tender.get('rfp_no', '')}
DESCRIPTION: {tender['title']}
START DATE: {tender['pub_date']}
END DATE: {tender['last_date']}
CONTACT: {tender.get('contact', '')}

Provide a brief summary covering:
1. **What it's about**: One-line plain-English summary of what MCX is looking for
2. **Scope**: Key deliverables or services required (infer from the description)
3. **Who should apply**: Type of vendor/organization this is relevant for
4. **Key dates**: Start and end dates
5. **Estimated value**: If inferable, otherwise say "Not disclosed"

Keep it concise — aim for 100-150 words total. Use plain language."""
        return call_gemini(client, prompt)
    except Exception as e:
        log.warning(f"[MCX] AI summarization failed: {e}")
        return fallback_mcx(tender)


def fallback_mcx(tender):
    return (
        f"RFP: {tender.get('rfp_no', '')}\n"
        f"Description: {tender['title']}\n"
        f"Deadline: {tender['last_date']}\n"
        f"Contact: {tender.get('contact', '')}"
    )


def summarize_nabfid(tender, client):
    if not client:
        return fallback_nabfid(tender)
    try:
        prompt = f"""Analyze this NaBFID (National Bank for Financing Infrastructure and Development) tender and provide a concise brief.

TENDER TITLE: {tender['title']}
PUBLISHED: {tender['pub_date']}
LAST DATE FOR SUBMISSION: {tender['last_date']}

Provide a brief summary covering:
1. **What it's about**: One-line plain-English summary of what NaBFID is looking for
2. **Scope**: Key deliverables or services required (infer from the title)
3. **Who should apply**: Type of vendor/organization this is relevant for
4. **Key dates**: Submission deadline
5. **Estimated value**: If inferable, otherwise say "Not disclosed"

Keep it concise — aim for 100-150 words total. Use plain language."""
        return call_gemini(client, prompt)
    except Exception as e:
        log.warning(f"[NaBFID] AI summarization failed: {e}")
        return fallback_nabfid(tender)


def fallback_nabfid(tender):
    return (
        f"Title: {tender['title']}\n"
        f"Published: {tender['pub_date']}\n"
        f"Deadline: {tender['last_date']}"
    )


# --- Email ---

def build_email_html(source_key, tenders, summaries):
    src = SOURCES[source_key]
    count = len(tenders)
    date_str = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    color = src["color"]

    html_parts = [f"""
<html>
<head>
<style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
    .container {{ max-width: 700px; margin: 0 auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    .header {{ background: {color}; color: white; padding: 24px 30px; }}
    .header h1 {{ margin: 0; font-size: 22px; font-weight: 600; }}
    .header p {{ margin: 8px 0 0; font-size: 13px; opacity: 0.85; }}
    .tender {{ padding: 24px 30px; border-bottom: 1px solid #e8e8e8; }}
    .tender:last-child {{ border-bottom: none; }}
    .tender-title {{ font-size: 16px; font-weight: 600; color: {color}; margin: 0 0 8px; }}
    .tender-title a {{ color: {color}; text-decoration: none; }}
    .tender-title a:hover {{ text-decoration: underline; }}
    .rfp-no {{ font-size: 13px; color: #888; margin-bottom: 8px; }}
    .meta {{ font-size: 12px; color: #666; margin-bottom: 14px; }}
    .meta span {{ margin-right: 16px; }}
    .summary {{ font-size: 14px; line-height: 1.6; color: #333; }}
    .summary strong {{ color: {color}; }}
    .links {{ margin-top: 14px; }}
    .links a {{ display: inline-block; font-size: 13px; color: #0066cc; text-decoration: none; margin-right: 16px; }}
    .links a:hover {{ text-decoration: underline; }}
    .contact {{ font-size: 12px; color: #888; margin-top: 10px; font-style: italic; }}
    .footer {{ padding: 16px 30px; background: #f9f9f9; font-size: 11px; color: #999; text-align: center; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{src['name']} Tender Alert</h1>
        <p>{count} new tender(s) found &mdash; {date_str}</p>
    </div>
"""]

    for tender, summary in zip(tenders, summaries):
        summary_html = summary.replace("\n", "<br>")

        if source_key == "rbi":
            title_html = f'<a href="{tender.get("detail_url", "")}">{tender["title"]}</a>'
            rfp_html = ""
            meta_html = f'<span>Published: {tender["pub_date"]}</span><span>Last Date: {tender["last_date"]}</span>'
            pdf_html = f'<a href="{tender["pdf_link"]}">Download PDF ({tender.get("pdf_size", "")})</a>' if tender.get("pdf_link") else ""
            detail_html = f'<a href="{tender.get("detail_url", "")}">View on RBI Website &rarr;</a>' if tender.get("detail_url") else ""
            contact_html = ""
            links_html = f"{detail_html} {pdf_html}"
        elif source_key == "mcx":
            title_html = tender["title"]
            rfp_html = f'<div class="rfp-no">RFP No. {tender.get("rfp_no", "")}</div>'
            meta_html = f'<span>Start: {tender["pub_date"]}</span><span>End: {tender["last_date"]}</span>'
            pdf_html = f'<a href="{tender["pdf_link"]}">Download PDF</a>' if tender.get("pdf_link") else ""
            apply_html = f'<a href="{tender.get("apply_link", "")}">Apply Online</a>' if tender.get("apply_link") else ""
            contact_html = f'<div class="contact">{tender.get("contact", "")}</div>' if tender.get("contact") else ""
            links_html = f"{pdf_html} {apply_html}"
        else:
            title_html = tender["title"]
            rfp_html = ""
            meta_html = f'<span>Published: {tender["pub_date"]}</span><span>Last Date: {tender["last_date"]}</span>'
            pdf_html = f'<a href="{tender["pdf_link"]}">Download PDF ({tender.get("pdf_size", "")})</a>' if tender.get("pdf_link") else ""
            contact_html = ""
            links_html = pdf_html

        html_parts.append(f"""
    <div class="tender">
        <h2 class="tender-title">{title_html}</h2>
        {rfp_html}
        <div class="meta">{meta_html}</div>
        <div class="summary">{summary_html}</div>
        <div class="links">{links_html}</div>
        {contact_html}
    </div>
""")

    html_parts.append(f"""
    <div class="footer">
        {src['name']} Tender Monitor &mdash; Automated notification service<br>
        Source: <a href="{src['url']}" style="color:#999;">{src['url'].split('//')[1].split('/')[0]}</a>
    </div>
</div>
</body>
</html>
""")

    return "".join(html_parts)


def send_email(config, subject, html_body, sender_label="Tender Monitor"):
    sender = config["gmail_address"]
    password = config["gmail_app_password"]
    recipient = config.get("notify_email", sender)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_label} <{sender}>"
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    log.info(f"Email sent to {recipient}")


# --- Source Runners ---

def run_source(source_key, config, client, dry_run=False, last=0, limit=0):
    src = SOURCES[source_key]
    tag = f"[{src['name']}]"

    log.info(f"{tag} Checking {src['full_name']}...")

    scrapers = {"rbi": scrape_rbi, "mcx": scrape_mcx, "nabfid": scrape_nabfid}
    tenders = scrapers[source_key]()

    if not tenders:
        log.warning(f"{tag} No tenders found — site may be down or structure changed")
        return

    if last > 0:
        selected = tenders[:last]
        log.info(f"{tag} Processing last {len(selected)} tender(s)")
    else:
        seen = load_seen(source_key)
        selected = [t for t in tenders if t["id"] not in seen]
        if not selected:
            log.info(f"{tag} No new tenders found")
            return
        if limit > 0:
            selected = selected[:limit]
        log.info(f"{tag} Found {len(selected)} new tender(s)")

    summaries = []
    for tender in selected:
        log.info(f"{tag} Processing: {tender['title'][:80]}...")
        if source_key == "rbi":
            detail_text = fetch_rbi_detail(tender.get("detail_url", ""))
            summary = summarize_rbi(tender, detail_text, client)
        elif source_key == "mcx":
            summary = summarize_mcx(tender, client)
        else:
            summary = summarize_nabfid(tender, client)
        summaries.append(summary)

    label = "Last" if last > 0 else "New"
    subject = f"{src['name']} Tender Alert: {len(selected)} {label} Tender(s) — {datetime.now().strftime('%b %d, %Y')}"
    html_body = build_email_html(source_key, selected, summaries)

    if dry_run:
        preview_file = SCRIPT_DIR / f"{source_key}_preview.html"
        with open(preview_file, "w") as f:
            f.write(html_body)
        log.info(f"{tag} Dry run — preview saved to {preview_file}")
        return

    send_email(config, subject, html_body, sender_label=f"{src['name']} Tender Monitor")

    if last == 0:
        seen = load_seen(source_key)
        for tender in selected:
            seen[tender["id"]] = {
                "title": tender["title"],
                "pub_date": tender["pub_date"],
                "first_seen": datetime.now().isoformat(),
            }
        save_seen(source_key, seen)

    log.info(f"{tag} Done")


def seed_source(source_key):
    src = SOURCES[source_key]
    tag = f"[{src['name']}]"

    scrapers = {"rbi": scrape_rbi, "mcx": scrape_mcx, "nabfid": scrape_nabfid}
    tenders = scrapers[source_key]()

    seen = {}
    for t in tenders:
        seen[t["id"]] = {
            "title": t["title"],
            "pub_date": t["pub_date"],
            "first_seen": datetime.now().isoformat(),
        }
    save_seen(source_key, seen)
    log.info(f"{tag} Seeded {len(seen)} existing tenders")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Tender Monitor — multi-source tender tracker",
        epilog="Examples:\n"
               "  %(prog)s                       Check all sources for new tenders\n"
               "  %(prog)s --source rbi          Check only RBI\n"
               "  %(prog)s --source nabfid       Check only NaBFID\n"
               "  %(prog)s --last 10             Email last 10 from all sources\n"
               "  %(prog)s --source mcx --last 5 --dry-run\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", choices=["rbi", "mcx", "nabfid", "all"], default="all",
                        help="Which source to check (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — don't send email or update seen list")
    parser.add_argument("--seed", action="store_true",
                        help="Mark all current tenders as seen (run once during setup)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of new tenders to process (0 = all)")
    parser.add_argument("--last", type=int, default=0,
                        help="Email the last N tenders (e.g. --last 10)")
    args = parser.parse_args()

    sources = ["rbi", "mcx", "nabfid"] if args.source == "all" else [args.source]

    if args.seed:
        for src in sources:
            seed_source(src)
        return

    config = load_config()
    client = get_gemini_client(config)

    for src in sources:
        try:
            run_source(src, config, client,
                       dry_run=args.dry_run, last=args.last, limit=args.limit)
        except Exception as e:
            log.error(f"[{SOURCES[src]['name']}] Failed: {e}")


if __name__ == "__main__":
    main()
