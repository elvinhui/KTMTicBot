import time
import logging
from typing import Optional

from ktm_sniper.config import settings
from ktm_sniper.security import mask_pii_in_text

logger = logging.getLogger(__name__)

# Sensitive fields that must never appear in logs
_MASK_EMAIL_KEYS = {"email", "mail", "username", "login"}
_MASK_PASSWORD_KEYS = {"password", "passwd", "pwd", "secret"}


def mask_email(email: str) -> str:
    """Masks an email address for privacy-safe logging. e.g. elv****@gmail.com"""
    if not email or "@" not in email:
        return "****"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 3:
        return f"{local[0]}****@{domain}"
    return f"{local[:3]}****@{domain}"


class KTMAuthenticator:
    """
    Manages KTMB KITS web portal authentication via Playwright browser.

    Handles:
    - Navigating to the login page (/Account/Login)
    - Filling Email + Password and submitting the ASP.NET form
    - Verifying login success via navbar state change
    - Automatic retry with backoff on failure
    - Telegram notification on login failure
    """

    LOGIN_URL_PATH = "/Account/Login"
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 5.0

    def __init__(
        self,
        email: str = "",
        password: str = "",
    ):
        self.email = email or settings.KTM_EMAIL
        self.password = password or settings.KTM_PASSWORD
        self._is_authenticated = False

    @property
    def is_authenticated(self) -> bool:
        return self._is_authenticated

    @property
    def masked_email(self) -> str:
        return mask_email(self.email)

    def has_credentials(self) -> bool:
        """Returns True if both email and password are configured."""
        return bool(self.email.strip()) and bool(self.password.strip())

    def login(self, page, timeout_ms: int = 30000) -> bool:
        """
        Executes the full KITS login flow via the Playwright browser page.

        1. Navigates to /Account/Login
        2. Dismisses any modals/popups
        3. Fills #Email and #Password
        4. Clicks #LoginButton
        5. Waits for navigation and verifies authenticated state

        Returns True on success, False on failure.
        """
        if not self.has_credentials():
            logger.error("登录失败: KTM_EMAIL 或 KTM_PASSWORD 未配置。")
            return False

        masked = self.masked_email
        logger.info(f"🔐 正在登录 KITS 账号 [{masked}]...")

        try:
            # 1. Navigate to login page
            login_url = settings.BASE_URL.rstrip("/") + self.LOGIN_URL_PATH
            page.goto(login_url, timeout=timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

            # 2. Dismiss any popups/modals
            self._dismiss_modals(page)

            # 3. Fill email
            email_input = page.locator("#Email")
            email_input.wait_for(state="visible", timeout=10000)
            email_input.fill(self.email)

            # 4. Fill password
            password_input = page.locator("#Password")
            password_input.wait_for(state="visible", timeout=5000)
            password_input.fill(self.password)

            # 5. Click login button
            login_btn = page.locator("#LoginButton")
            login_btn.wait_for(state="visible", timeout=5000)
            login_btn.click()

            # 6. Wait for navigation after form submission
            page.wait_for_load_state("networkidle", timeout=timeout_ms)

            # 7. Check for login errors (modal popups with error messages)
            if self._has_login_error(page):
                error_msg = self._extract_login_error(page)
                if "multiple login" in error_msg.lower():
                    logger.error(f"❌ KITS 登录失败 [{masked}]: KTMB 限制单设备在线！检测到该账号当前已在其他浏览器或手机端登录。请先在您的个人浏览器或 App 中点击【Log Out 退出登录】，然后再运行机器人！")
                else:
                    logger.error(f"❌ KITS 登录失败 [{masked}]: {error_msg}")
                self._is_authenticated = False
                return False

            # 8. Verify authenticated state
            if self.verify_login(page):
                logger.info(f"✅ KITS 登录成功 [{masked}]！")
                self._is_authenticated = True
                return True

            # If still on login page, login failed
            if "/Account/Login" in page.url:
                logger.error(f"❌ KITS 登录失败 [{masked}]: 仍停留在登录页面")
                self._is_authenticated = False
                return False

            # Navigated away from login => likely success
            logger.info(f"✅ KITS 登录成功 [{masked}] (已重定向到 {page.url})")
            self._is_authenticated = True
            return True

        except Exception as e:
            logger.error(f"❌ KITS 登录异常 [{masked}]: {e}")
            self._is_authenticated = False
            return False

    def verify_login(self, page) -> bool:
        """
        Checks whether the current Playwright page has an authenticated session.

        Detection strategy:
        - If page is at about:blank or not on KITS domain => NOT logged in
        - If on /Account/Login page => NOT logged in
        - If navbar contains "Login / sign up" link => NOT logged in
        - If navbar exists and "Login / sign up" is absent => logged in
        """
        if not page.url or page.url == "about:blank" or not page.url.startswith("http"):
            self._is_authenticated = False
            return False

        if "/Account/Login" in page.url:
            self._is_authenticated = False
            return False

        try:
            # Check if navbar exists
            nav = page.locator("nav.navbar")
            if nav.count() == 0 or not nav.first.is_visible(timeout=2000):
                self._is_authenticated = False
                return False

            # Check if "Login / sign up" link is still visible in navbar
            login_link = page.locator("a.nav-link:has-text('Login / sign up')")
            if login_link.count() > 0 and login_link.first.is_visible(timeout=2000):
                self._is_authenticated = False
                return False

            # If the login link is gone and navbar is present, we are authenticated
            self._is_authenticated = True
            return True
        except Exception:
            self._is_authenticated = False
            return False

    def ensure_authenticated(
        self,
        page,
        notifier=None,
    ) -> bool:
        """
        High-level method: verify existing session or perform login with retries.

        Args:
            page: Playwright page instance
            notifier: Optional TelegramTicketNotifier for failure alerts

        Returns True if authenticated, False if all retries exhausted.
        """
        # Quick check: already authenticated?
        if self.verify_login(page):
            logger.info(f"✅ KITS Session 仍然有效 [{self.masked_email}]")
            return True

        # Attempt login with retries
        for attempt in range(1, self.MAX_RETRIES + 1):
            logger.info(f"🔐 登录尝试 {attempt}/{self.MAX_RETRIES}...")

            if self.login(page):
                return True

            if attempt < self.MAX_RETRIES:
                delay = self.RETRY_DELAY_SECONDS * attempt
                logger.warning(f"⏳ 登录失败，将在 {delay:.0f} 秒后重试...")
                time.sleep(delay)

        # All retries exhausted
        error_msg = f"🚨 KITS 登录失败！已耗尽所有 {self.MAX_RETRIES} 次重试。请检查 KTM_EMAIL/KTM_PASSWORD 配置。"
        logger.error(error_msg)

        if notifier:
            try:
                notifier.send_raw_message(
                    f"🚨 *KITS 登录失败*\n\n"
                    f"账号: `{self.masked_email}`\n"
                    f"已重试 {self.MAX_RETRIES} 次均失败。\n\n"
                    f"请检查 `.env` 中的 `KTM_EMAIL` 和 `KTM_PASSWORD` 是否正确。"
                )
            except Exception as e:
                logger.warning(f"发送 Telegram 登录失败告警失败: {e}")

        return False

    def logout(self, page, timeout_ms: int = 15000) -> bool:
        """
        Cleanly logs out the KITS session via /Account/Logout to release
        the server-side session lock and avoid 'Not allow multiple login' errors.
        """
        try:
            logout_url = settings.BASE_URL.rstrip("/") + "/Account/Logout"
            page.goto(logout_url, timeout=timeout_ms)
            self._is_authenticated = False
            logger.info("Session logged out cleanly.")
            return True
        except Exception as e:
            logger.warning(f"Clean logout failed: {e}")
            return False

    def _dismiss_modals(self, page):
        """Dismisses KITS advertisement and notification popups."""
        for selector in [
            "#CloseButtonAdvertisement",
            "#popupModalOkButton",
            "#popupModalCloseButton",
            "button:has-text('OK')",
            "button:has-text('Close')",
        ]:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=1000):
                    locator.click(timeout=1500)
            except Exception:
                pass

    def _has_login_error(self, page) -> bool:
        """Checks if a validation error modal or message is displayed."""
        try:
            # Check for validation summary modal
            modal = page.locator("#validationSummaryModal")
            if modal.is_visible(timeout=2000):
                return True

            # Check for popup modal with error
            popup = page.locator("#popupModal")
            if popup.is_visible(timeout=1000):
                return True

            # Check for inline validation errors
            error_spans = page.locator("span.field-validation-error")
            if error_spans.count() > 0:
                return True

            return False
        except Exception:
            return False

    def _extract_login_error(self, page) -> str:
        """Extracts the error message text from the login page."""
        try:
            # Try validation summary modal body
            body = page.locator("#validationSummaryModalBody")
            if body.count() > 0:
                text = body.inner_text(timeout=2000).strip()
                if text:
                    return text

            # Try popup modal body
            popup_body = page.locator("#popupModalBody")
            if popup_body.count() > 0:
                text = popup_body.inner_text(timeout=2000).strip()
                if text:
                    return text

            # Try inline field errors
            errors = page.locator("span.field-validation-error")
            if errors.count() > 0:
                return errors.first.inner_text(timeout=2000).strip()

            return "Unknown login error"
        except Exception:
            return "Unable to extract error message"
