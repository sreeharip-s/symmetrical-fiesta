# robust_linker.py
"""
Automated class link scraper and notifier for WinnersEduWorld.
Fetches live class links and sends them via Zoom webhooks and NTFY alerts.
Designed for scheduled execution via GitHub Actions.
"""

import os
import traceback
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List
from urllib.parse import urljoin

import pytz
import requests
from bs4 import BeautifulSoup


# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================

class TaskType(Enum):
    """Available task types for execution."""
    DAILY_REPORT = "daily_report"
    SUNDAY_CLASSES = "sunday_classes"
    SUNDAY_MARKS = "sunday_marks"
    WEEKDAY_CIRCLE = "weekday_circle"
    WEEKDAY_NORMAL = "weekday_normal"
    WEEKDAY_MARK = "weekday_mark"


@dataclass
class Config:
    """Application configuration loaded from environment variables."""
    ntfy_topic_url: str
    zoom_webhook_urls: List[str]
    zoom_verification_tokens: List[str]
    winners_mobile: str
    winners_password: str
    timezone: str = "Asia/Dubai"
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            ntfy_topic_url=os.getenv("NTFY_TOPIC_URL", ""),
            zoom_webhook_urls=[url.strip() for url in os.getenv("ZOOM_WEBHOOK_URLS", "").split(",") if url.strip()],
            zoom_verification_tokens=[token.strip() for token in os.getenv("ZOOM_VERIFICATION_TOKENS", "").split(",") if token.strip()],
            winners_mobile=os.getenv("WINNERS_MOBILE_NUMBER", ""),
            winners_password=os.getenv("WINNERS_PASSWORD", ""),
        )
    
    def validate(self) -> bool:
        """Validate required configuration."""
        if not self.winners_mobile or not self.winners_password:
            print("FATAL: WINNERS_MOBILE_NUMBER or WINNERS_PASSWORD not set.")
            return False
        if len(self.zoom_webhook_urls) != len(self.zoom_verification_tokens):
            print("FATAL: ZOOM_WEBHOOK_URLS and ZOOM_VERIFICATION_TOKENS count mismatch.")
            return False
        return True


@dataclass
class ClassInfo:
    """Information about a class."""
    subject: str
    join_link: str
    mark_link: Optional[str] = None


@dataclass
class ScrapedData:
    """Container for scraped class data."""
    is_sunday: bool
    sunday_classes: List[ClassInfo]
    weekday_links: Dict[str, ClassInfo]


# ==============================================================================
# NOTIFICATION SERVICE
# ==============================================================================

class NotificationService:
    """Handles sending notifications via NTFY and Zoom webhooks."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def send_ntfy_alert(self, message: str, title: str = "Automation Alert ⚠️", priority: str = "high") -> bool:
        """Send alert to NTFY topic."""
        if not self.config.ntfy_topic_url:
            print(f"ALERT SKIPPED: NTFY URL not configured. Message: {message}")
            return False
        
        try:
            response = requests.post(
                self.config.ntfy_topic_url,
                data=message.encode("utf-8"),
                headers={"Title": title, "Priority": priority},
                timeout=15
            )
            response.raise_for_status()
            print(f"✓ NTFY alert sent: {title}")
            return True
        except Exception as e:
            print(f"✗ CRITICAL: Failed to send NTFY alert: {e}")
            return False
    
    def send_to_zoom(self, message: str) -> bool:
        """Send message to all configured Zoom webhooks."""
        if not self.config.zoom_webhook_urls:
            print("No Zoom webhooks configured.")
            return False
        
        success_count = 0
        for idx, (url, token) in enumerate(zip(self.config.zoom_webhook_urls, self.config.zoom_verification_tokens), 1):
            if not url or not token:
                print(f"✗ Webhook {idx}: Missing URL or token")
                continue
            
            try:
                full_url = f"{url}?format=message"
                response = requests.post(
                    full_url,
                    json={"text": message},
                    headers={"Authorization": token, "Content-Type": "application/json"},
                    timeout=15
                )
                response.raise_for_status()
                print(f"✓ Webhook {idx}: Message sent successfully")
                success_count += 1
            except Exception as e:
                print(f"✗ Webhook {idx}: Failed - {e}")
                self.send_ntfy_alert(
                    f"Zoom webhook {idx} failed!\nError: {str(e)}",
                    title="Zoom Webhook Error ❌"
                )
        
        return success_count > 0


# ==============================================================================
# WEB SCRAPER
# ==============================================================================

class WinnersEduScraper:
    """Scrapes class information from WinnersEduWorld portal."""
    
    BASE_URL = "https://www.winnerseduworld.com"
    LOGIN_URL = urljoin(BASE_URL, "/parent-portal")
    LIVE_CLASS_URL = urljoin(BASE_URL, "/parent-portal/live-class")
    
    def __init__(self, config: Config, notifier: NotificationService):
        self.config = config
        self.notifier = notifier
        self.session = requests.Session()
    
    def login(self) -> bool:
        """Authenticate with the portal."""
        try:
            login_data = {
                "check": "post",
                "mob": self.config.winners_mobile,
                "password": self.config.winners_password
            }
            
            response = self.session.post(self.LOGIN_URL, data=login_data, timeout=30)
            response.raise_for_status()
            
            if "dashboard" not in response.url:
                raise ConnectionError("Login failed - dashboard not reached. Check credentials.")
            
            print("✓ Login successful")
            return True
        except Exception as e:
            print(f"✗ Login failed: {e}")
            self.notifier.send_ntfy_alert(
                f"Login to WinnersEduWorld failed!\nError: {str(e)}",
                title="Login Error ❌"
            )
            return False
    
    def scrape_live_classes(self) -> Optional[ScrapedData]:
        """Scrape live class information from the portal."""
        try:
            response = self.session.get(self.LIVE_CLASS_URL, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            tz = pytz.timezone(self.config.timezone)
            is_sunday = datetime.now(tz).weekday() == 6
            
            data = ScrapedData(
                is_sunday=is_sunday,
                sunday_classes=[],
                weekday_links={}
            )
            
            cards = soup.select("div.p-4.border.rounded")
            
            if is_sunday:
                self._parse_sunday_classes(cards, data)
            else:
                self._parse_weekday_classes(cards, soup, data)
            
            print(f"✓ Scraping complete. Found: {len(data.sunday_classes)} Sunday classes, {len(data.weekday_links)} weekday links")
            return data
        except Exception as e:
            print(f"✗ Scraping failed: {e}")
            self.notifier.send_ntfy_alert(
                f"Failed to scrape WinnersEduWorld!\n\n{traceback.format_exc()}",
                title="Scraping Error ❌"
            )
            return None
    
    def _parse_sunday_classes(self, cards, data: ScrapedData):
        """Parse Sunday class information."""
        for card in cards:
            subject_elem = card.find("h6")
            join_btn = card.find("a", string=lambda t: t and "join meeting" in t.lower())
            mark_btn = card.find("a", string=lambda t: t and "mark" in t.lower())
            
            if subject_elem and join_btn:
                data.sunday_classes.append(ClassInfo(
                    subject=subject_elem.get_text(strip=True),
                    join_link=join_btn.get("href", ""),
                    mark_link=mark_btn.get("href") if mark_btn else None
                ))
    
    def _parse_weekday_classes(self, cards, soup, data: ScrapedData):
        """Parse weekday class information."""
        for card in cards:
            subject_elem = card.find("h6")
            join_btn = card.find("a", string=lambda t: t and "join meeting" in t.lower())
            
            if subject_elem and join_btn:
                subject_text = subject_elem.get_text(strip=True)
                link = join_btn.get("href", "")
                
                if "study circle" in subject_text.lower():
                    data.weekday_links["circle"] = ClassInfo(subject=subject_text, join_link=link)
                else:
                    data.weekday_links["normal"] = ClassInfo(subject=subject_text, join_link=link)
        
        # Find mark attendance link
        mark_btn = soup.find("a", string=lambda t: t and "mark" in t.lower())
        if mark_btn:
            data.weekday_links["mark"] = ClassInfo(
                subject="Mark Attendance",
                join_link=mark_btn.get("href", "")
            )
    
    def get_data(self) -> Optional[ScrapedData]:
        """Main entry point: login and scrape data."""
        if not self.login():
            return None
        return self.scrape_live_classes()


# ==============================================================================
# TASK EXECUTOR
# ==============================================================================

class TaskExecutor:
    """Executes scheduled tasks based on scraped data."""
    
    def __init__(self, config: Config, notifier: NotificationService, scraper: WinnersEduScraper):
        self.config = config
        self.notifier = notifier
        self.scraper = scraper
    
    def execute(self, task_type: TaskType):
        """Execute the specified task."""
        print(f"\n{'='*60}")
        print(f"Executing: {task_type.value}")
        print(f"{'='*60}")
        
        if task_type == TaskType.DAILY_REPORT:
            self._run_daily_report()
        else:
            data = self.scraper.get_data()
            if not data:
                return
            
            self._dispatch_task(task_type, data)
    
    def _run_daily_report(self):
        """Run health check and send daily status."""
        data = self.scraper.get_data()
        status = "✅ All systems operational!" if data else "❌ Site access failed"
        
        self.notifier.send_ntfy_alert(
            f"Daily Status Report\n\n{status}\nTime: {datetime.now(pytz.timezone(self.config.timezone)).strftime('%Y-%m-%d %H:%M:%S %Z')}",
            title="Daily Health Check"
        )
    
    def _dispatch_task(self, task_type: TaskType, data: ScrapedData):
        """Dispatch task based on type and available data."""
        handlers = {
            TaskType.SUNDAY_CLASSES: lambda: self._send_sunday_classes(data),
            TaskType.SUNDAY_MARKS: lambda: self._send_sunday_marks(data),
            TaskType.WEEKDAY_CIRCLE: lambda: self._send_weekday_link(data, "circle", "STUDY CIRCLE"),
            TaskType.WEEKDAY_NORMAL: lambda: self._send_weekday_link(data, "normal", "NORMAL CLASS"),
            TaskType.WEEKDAY_MARK: lambda: self._send_weekday_link(data, "mark", "MARK ATTENDANCE"),
        }
        
        handler = handlers.get(task_type)
        if handler:
            handler()
    
    def _format_message(self, subject: str, link: str, prefix: str = "") -> str:
        """Format class link message."""
        message_parts = [f"*{subject}*", link]
        if prefix:
            message_parts.append(f"\n[{prefix}]")
        message_parts.append("\n_(automated)_")
        return "\n".join(message_parts)
    
    def _send_sunday_classes(self, data: ScrapedData):
        """Send Sunday class join links."""
        if not data.sunday_classes:
            self.notifier.send_ntfy_alert("No Sunday classes found!", title="Data Missing ⚠️")
            return
        
        for class_info in data.sunday_classes:
            message = self._format_message(class_info.subject, class_info.join_link, "Sunday Class")
            self.notifier.send_to_zoom(message)
    
    def _send_sunday_marks(self, data: ScrapedData):
        """Send Sunday mark attendance links."""
        mark_classes = [c for c in data.sunday_classes if c.mark_link]
        
        if not mark_classes:
            self.notifier.send_ntfy_alert("No Sunday mark links found!", title="Data Missing ⚠️")
            return
        
        for class_info in mark_classes:
            message = self._format_message(
                f"Mark Attendance - {class_info.subject}",
                class_info.mark_link,
                "Sunday Marks"
            )
            self.notifier.send_to_zoom(message)
    
    def _send_weekday_link(self, data: ScrapedData, link_key: str, prefix: str):
        """Send weekday class link."""
        class_info = data.weekday_links.get(link_key)
        
        if not class_info:
            self.notifier.send_ntfy_alert(
                f"Could not find '{prefix}' link for weekday!",
                title="Data Missing ⚠️"
            )
            return
        
        message = self._format_message(class_info.subject, class_info.join_link, prefix)
        self.notifier.send_to_zoom(message)


# ==============================================================================
# SCHEDULER
# ==============================================================================

class TaskScheduler:
    """Determines which task should run based on current time."""
    
    def __init__(self, timezone: str):
        self.tz = pytz.timezone(timezone)
    
    def get_scheduled_task(self) -> Optional[TaskType]:
        """Check current time and return the task to execute, if any."""
        now = datetime.now(self.tz)
        day = now.weekday()  # Monday=0, Sunday=6
        hour = now.hour
        minute = now.minute
        
        print(f"Time check: {now.strftime('%A, %Y-%m-%d %H:%M:%S %Z')}")
        
        # Daily report at 8:00 AM
        if hour == 8 and minute < 15:
            return TaskType.DAILY_REPORT
        
        # Sunday schedule
        if day == 6:
            if hour == 17 and minute < 15:
                return TaskType.SUNDAY_CLASSES
            elif hour == 18 and minute >= 55:
                return TaskType.SUNDAY_MARKS
        
        # Weekday schedule (Mon-Sat)
        else:
            if hour == 16 and minute < 15:
                return TaskType.WEEKDAY_CIRCLE
            elif hour == 17 and minute < 15:
                return TaskType.WEEKDAY_NORMAL
            elif hour == 18 and minute >= 55:
                return TaskType.WEEKDAY_NORMAL
            elif hour == 19 and 15 <= minute < 30:
                return TaskType.WEEKDAY_MARK
        
        return None


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    """Main application entry point."""
    print("\n" + "="*60)
    print("WinnersEduWorld Class Link Automation")
    print("="*60 + "\n")
    
    # Load and validate configuration
    config = Config.from_env()
    if not config.validate():
        print("\n❌ Configuration validation failed. Exiting.")
        return 1
    
    # Initialize services
    notifier = NotificationService(config)
    scraper = WinnersEduScraper(config, notifier)
    executor = TaskExecutor(config, notifier, scraper)
    scheduler = TaskScheduler(config.timezone)
    
    # Check for scheduled task
    task = scheduler.get_scheduled_task()
    
    if task:
        print(f"✓ Task scheduled: {task.value}\n")
        executor.execute(task)
        print(f"\n✓ Execution complete!")
    else:
        print("⊘ No task scheduled for current time window.")
    
    print("\n" + "="*60 + "\n")
    return 0


if __name__ == "__main__":
    exit(main())
