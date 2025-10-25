# robust_linker.py
# The thinnest, most robust version using Zoom Webhooks and NTFY.SH for alerts.
# Designed to be run by an external scheduler like GitHub Actions.

import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import os
import pytz
import traceback

# ==============================================================================
# --- 1. CONFIGURATION (Loaded from GitHub Secrets) ---
# ==============================================================================

# All configuration is loaded from environment variables set by the GitHub Action
CONFIG = {
    "NTFY_TOPIC_URL": os.environ.get("NTFY_TOPIC_URL"),
    "ZOOM_WEBHOOK_URLS": os.environ.get("ZOOM_WEBHOOK_URLS", "").split(','),
    "ZOOM_VERIFICATION_TOKENS": os.environ.get("ZOOM_VERIFICATION_TOKENS", "").split(','),
    "WINNERS_MOBILE_NUMBER": os.environ.get("WINNERS_MOBILE_NUMBER"),
    "WINNERS_PASSWORD": os.environ.get("WINNERS_PASSWORD"),
    "TIMEZONE": "Asia/Dubai" 
}

# --- Validation ---
if not CONFIG["WINNERS_MOBILE_NUMBER"] or len(CONFIG["ZOOM_WEBHOOK_URLS"]) != len(CONFIG["ZOOM_VERIFICATION_TOKENS"]):
    print("FATAL: Secret configuration is invalid. Check WINNERS_MOBILE or ensure ZOOM_WEBHOOK_URLS and ZOOM_VERIFICATION_TOKENS have the same number of entries.")
    exit(1)

# ==============================================================================
# --- 2. CORE LOGIC ---
# ==============================================================================

def send_alert(message, title="Automation Alert ⚠️"):
    """Sends a crucial alert to the NTFY.SH topic."""
    if not CONFIG["NTFY_TOPIC_URL"]: 
        print(f"ALERT SKIPPED: NTFY URL not set. Message: {message}")
        return
    try:
        requests.post(
            CONFIG["NTFY_TOPIC_URL"], 
            data=message.encode('utf-8'),
            headers={"Title": title, "Priority": "urgent"},
            timeout=15
        ).raise_for_status()
        print(f"Sent NTFY alert: {message}")
    except Exception as e:
        print(f"CRITICAL: Failed to send NTFY alert: {e}")

def get_live_class_data():
    """Logs in and scrapes the page, adapting for weekdays vs. Sundays."""
    try:
        session = requests.Session()
        login_data = {"check": "post", "mob": CONFIG['WINNERS_MOBILE_NUMBER'], "password": CONFIG['WINNERS_PASSWORD']}
        
        login_response = session.post("https://www.winnerseduworld.com/parent-portal", data=login_data, timeout=30)
        login_response.raise_for_status()
        if "dashboard" not in login_response.url:
            raise ConnectionError("Login Failed. Check credentials or website layout.")

        live_class_response = session.get("https://www.winnerseduworld.com/parent-portal/live-class", timeout=30)
        live_class_response.raise_for_status()
        soup = BeautifulSoup(live_class_response.text, 'html.parser')

        is_sunday = (datetime.now(pytz.timezone(CONFIG["TIMEZONE"])).weekday() == 6)
        data = {"is_sunday": is_sunday, "sunday_classes": [], "weekday_links": {}}
        cards = soup.select("div.p-4.border.rounded")

        if is_sunday:
            for card in cards:
                subject = card.find("h6")
                join_btn = card.find("a", string=lambda t: "join meeting" in t.lower())
                mark_btn = card.find("a", string=lambda t: t and "mark" in t.lower())
                if subject and join_btn:
                    data["sunday_classes"].append({
                        "subject": subject.get_text(strip=True),
                        "join_link": join_btn.get('href'),
                        "mark_link": mark_btn.get('href') if mark_btn else None
                    })
        else: # Weekday logic
            for card in cards:
                subject = card.find("h6")
                join_btn = card.find("a", string=lambda t: "join meeting" in t.lower())
                if subject and join_btn:
                    sub_text = subject.get_text(strip=True).lower()
                    if "study circle" in sub_text:
                        data["weekday_links"]["circle"] = {"subject": subject.get_text(strip=True), "link": join_btn.get('href')}
                    else:
                        data["weekday_links"]["normal"] = {"subject": subject.get_text(strip=True), "link": join_btn.get('href')}
            mark_btn = soup.find("a", string=lambda t: t and "mark" in t.lower())
            if mark_btn:
                data["weekday_links"]["mark"] = {"link": mark_btn.get('href')}

        return data
    except Exception as e:
        print(f"ERROR during scraping: {e}")
        send_alert(f"Failed to fetch WinnersEduWorld page.\nError: {traceback.format_exc()}", title="CRITICAL FAILURE ❌")
        return None


def send_to_zoom(message):
    """Sends a message to all configured Zoom Webhooks using the required Authorization header."""
    for url, token in zip(CONFIG["ZOOM_WEBHOOK_URLS"], CONFIG["ZOOM_VERIFICATION_TOKENS"]):
        if not url or not token: 
            print("Skipping Zoom send: Missing URL or Token in configuration.")
            continue
        try:
            full_url = f"{url}?format=message"
            headers = {"Authorization": token, "Content-Type": "application/json"}
            payload = {"text": message}
            requests.post(full_url, json=payload, headers=headers, timeout=15).raise_for_status()
            print(f"  > Message sent successfully to Zoom Webhook.")
        except Exception as e:
            print(f"  > FAILED to send to Zoom Webhook: {e}")
            send_alert(f"Zoom Send Failed! Could not post to webhook. Error: {e}", title="Zoom Webhook Error ❌")
        time.sleep(1)

# ==============================================================================
# --- 3. TASK DISPATCHER ---
# ==============================================================================

def execute_task(task_type):
    """Executes the actual scraping and sending logic."""
    print(f"Executing task: {task_type}")
    data = get_live_class_data()
    if not data: return # Alert was already sent by get_live_class_data

    def format_and_send(link_data, prefix=""):
        if link_data:
            subject = link_data.get('subject', prefix).strip()
            link = link_data.get('link', link_data.get('join_link'))
            message = f"*{subject}*:\n{link}\n{prefix} - (this is automated :>)"
            send_to_zoom(message)
        else:
            send_alert(f"Skipping task: Could not find the '{prefix}' link.", title="Data Missing ⚠️")

    if data['is_sunday']:
        if task_type == 'sunday_classes':
            for info in data['sunday_classes']: format_and_send(info, prefix="JOIN LINK")
        elif task_type == 'sunday_marks':
            for info in data['sunday_classes']:
                if info.get('mark_link'): format_and_send({'subject': f"MARK LINK - {info['subject']}", 'link': info['mark_link']}, prefix="MARK ENTRY")
    else: # Weekday
        if task_type == 'weekday_circle': format_and_send(data['weekday_links'].get('circle'), prefix="STUDY CIRCLE")
        elif task_type == 'weekday_normal': format_and_send(data['weekday_links'].get('normal'), prefix="NORMAL CLASS")
        elif task_type == 'weekday_mark': format_and_send(data['weekday_links'].get('mark'), prefix="MARK ENTRY")


def run_daily_report():
    """Runs a health check and sends a daily status report."""
    print("Executing task: Daily Report")
    data = get_live_class_data()
    status = "All Systems Go! ✅" if data else "Site Access Failed ❌"
    send_alert(f"Daily Status Report:\n{status}", title="Automation Status")

# ==============================================================================
# --- 4. MAIN EXECUTION LOGIC (CORRECTED) ---
# ==============================================================================

def main():
    """Checks current time (in TIMEZONE) and executes the task if scheduled."""
    tz = pytz.timezone(CONFIG["TIMEZONE"])
    now = datetime.now(tz)
    
    current_day = now.weekday()  # Monday=0, Sunday=6
    current_hour = now.hour
    current_minute = now.minute

    print(f"Job Check at: {now.strftime('%Y-%-m-%d %H:%M:%S %Z')}")

    task_to_run = None # Variable to hold which task should be run

    # --- Time-based Dispatcher Logic ---
    if current_hour == 8 and current_minute < 15: # Runs once between 8:00 and 8:15
        task_to_run = 'daily_report'
    
    elif current_day == 6: # Sunday
        if current_hour == 17 and current_minute < 15:
            task_to_run = 'sunday_classes'
        elif current_hour == 18 and current_minute >= 55:
            task_to_run = 'sunday_marks'
            
    else: # Weekday (Mon-Sat)
        if current_hour == 16 and current_minute < 15:
            task_to_run = 'weekday_circle'
        elif current_hour == 17 and current_minute < 15:
            task_to_run = 'weekday_normal'
        elif current_hour == 18 and current_minute >= 55:
            task_to_run = 'weekday_normal'
        elif current_hour == 19 and current_minute >= 15 and current_minute < 30:
            task_to_run = 'weekday_mark'

    # --- Execute the determined task ---
    if task_to_run:
        if task_to_run == 'daily_report':
            run_daily_report()
        else:
            execute_task(task_to_run)
    else:
        print("No task scheduled for the current time window.")

if __name__ == "__main__":
    main()