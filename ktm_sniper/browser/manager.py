import os
import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

try:
    from playwright_stealth.stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

logger = logging.getLogger(__name__)

class BrowserManager:
    """
    Manages Playwright Chromium browser lifecycle and applies stealth evasion
    to bypass Cloudflare Turnstile and anti-bot fingerprints.
    Supports persistent auth state via storage_state.
    """
    def __init__(
        self,
        headless: bool = True,
        slow_mo: int = 0,
        storage_state_path: Optional[str] = "data/auth_state.json"
    ):
        self.headless = headless
        self.slow_mo = slow_mo
        self.storage_state_path = storage_state_path
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def start(self) -> Page:
        if self.page is not None:
            return self.page

        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage"
            ]
        )

        context_kwargs = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "locale": "en-US",
            "timezone_id": "Asia/Kuala_Lumpur"
        }
        if self.storage_state_path and os.path.exists(self.storage_state_path):
            try:
                context_kwargs["storage_state"] = self.storage_state_path
                logger.info(f"🔑 正在载入已有会话凭据 [storage_state: {self.storage_state_path}]...")
            except Exception as e:
                logger.warning(f"无法读取 storage_state ({e})，将以全新上下文启动。")

        self.context = self.browser.new_context(**context_kwargs)

        self.page = self.context.new_page()

        # Apply Stealth evasions
        if HAS_STEALTH:
            try:
                stealth = Stealth()
                stealth.apply_stealth_sync(self.page)
            except Exception:
                pass

        return self.page

    def save_storage_state(self, path: Optional[str] = None) -> Optional[str]:
        """
        Saves current cookies and localStorage state to a JSON file.
        Can be restored on next launch to skip authentication entirely.
        """
        target = path or self.storage_state_path or "data/auth_state.json"
        if self.context:
            try:
                parent_dir = os.path.dirname(target)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                self.context.storage_state(path=target)
                logger.info(f"💾 会话凭据已持久化保存至: {target}")
                return target
            except Exception as e:
                logger.warning(f"保存 storage_state 失败: {e}")
        return None

    def inject_cookies(self, cookies: list) -> bool:
        """
        Injects a list of cookie dicts into the current browser context.
        """
        if self.context:
            try:
                self.context.add_cookies(cookies)
                return True
            except Exception as e:
                logger.warning(f"注入 Cookie 失败: {e}")
        return False

    def close(self):
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None

        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self.page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
