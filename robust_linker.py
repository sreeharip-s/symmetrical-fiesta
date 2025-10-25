# robust_linker.py
# Designed to be run by an external scheduler like GitHub Actions.
# It checks the current time and executes the appropriate task.

import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import os
import pytz # A robust library for handling timezones

# ==============================================================================
# --- 1. CONFIGURATION (Loaded from GitHub Secrets) ---
# ==============================================================================

# All credentials will be loaded from environment variables set by the GitHub Action
# This makes the script itself free of any secrets.
CONFIG = {
    "ZOOM_OAUTH_TOKEN": os.environ.get("ZOOM_OAUTH_TOKEN"),
    "ZOOM_CHANNEL_IDS": os.environ.get("ZOOM_CHANNEL_IDS", "").split(','),
    "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
    "TELEGRAM_ADMIN_CHAT_ID": os.environ.get("TELEGRAM_ADMIN_CHAT_ID"),
    "WINNERS_MOBILE_NUMBER": os.environ.get("WINNERS_MOBILE_NUMBER"),
    "WINNERS_PASSWORD": os.environ.get("WINNERS_PASSWORD"),
    "TIMEZONE": "Asia/Dubai" # The timezone for your schedule
}

# --- Validation ---
if not all([CONFIG["ZOOM_OAUTH_TOKEN"], CONFIG["WINNERS_MOBILE_NUMBER"]]):
    print("FATAL: Essential secrets (Zoom Token, Winners Mobile) are not set. Exiting.")
    exit(1)


# ==============================================================================
# --- 2. CORE LOGIC (Scraping and Messaging) ---
# ==============================================================================
# Note: These functions are the same as the previous "longevity" version.

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

        # ... (The rest of this scraping logic is identical to the previous version) ...
        # (It correctly handles Sunday vs Weekday)
        return data

    except Exception as e:
        print(f"ERROR during scraping: {e}")
        return None

def send_to_zoom(message, subject=None):
    """Sends a message to all configured Zoom channels."""
    for channel_id in CONFIG["ZOOM_CHANNEL_IDS"]:
        if not channel_id: continue # Skip if a channel ID is empty
        try:
            headers = {"Authorization": f"Bearer {CONFIG['ZOOM_OAUTH_TOKEN']}", "Content-Type": "application/json"}
            rich_text = [{"start_position": 0, "end_position": len(subject), "format_type": "Bold"}] if subject else []
            payload = {"to_channel": channel_id, "message": message, "rich_text": rich_text}
            requests.post("https://api.zoom.us/v2/chat/users/me/messages", headers=headers, json=payload, timeout=15).raise_for_status()
            print(f"  > Message sent successfully to Zoom channel: ...{channel_id[-6:]}")
        except Exception as e:
            print(f"  > FAILED to send to Zoom channel {channel_id}: {e}")
        time.sleep(1)

# ... (The individual task functions are also the same) ...

# ==============================================================================
# --- 3. MAIN EXECUTION LOGIC ---
# ==============================================================================

def main():
    """
    This is the main entry point. It checks the current time and decides which,
    if any, task needs to be run right now.
    """
    tz = pytz.timezone(CONFIG["TIMEZONE"])
    now = datetime.now(tz)
    
    current_day = now.weekday()  # Monday is 0, Sunday is 6
    current_hour = now.hour
    
    print(f"Running job check at {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Day: {current_day} | Hour: {current_hour}")

    # --- Time-based Dispatcher ---
    
    # SUNDAY LOGIC
    if current_day == 6: 
        if current_hour == 17:
            # task_sunday_classes()
            pass # Placeholder for your function
        elif current_hour == 18:
            # task_sunday_marks()
            pass # Placeholder

    # WEEKDAY LOGIC (Monday to Saturday)
    else:
        if current_hour == 16:
            # task_weekday_circle()
            pass
        elif current_hour == 17:
            # task_weekday_normal()
            pass
        elif current_hour == 18:
            # task_weekday_normal()
            pass
        elif current_hour == 19:
            # task_weekday_mark()
            pass

    print("Job check complete.")

if __name__ == "__main__":
    main()