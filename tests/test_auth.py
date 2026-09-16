"""
Tests for ktm_sniper.auth — KTMAuthenticator login and session management.
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from ktm_sniper.auth import KTMAuthenticator, mask_email


# ---------------------------------------------------------------------------
# mask_email
# ---------------------------------------------------------------------------

class TestMaskEmail:
    def test_standard_email(self):
        assert mask_email("elvinhui0217@gmail.com") == "elv****@gmail.com"

    def test_short_local(self):
        assert mask_email("ab@test.com") == "a****@test.com"

    def test_empty_email(self):
        assert mask_email("") == "****"

    def test_no_at_sign(self):
        assert mask_email("notanemail") == "****"

    def test_three_char_local(self):
        assert mask_email("abc@x.co") == "a****@x.co"

    def test_four_char_local(self):
        assert mask_email("abcd@x.co") == "abc****@x.co"


# ---------------------------------------------------------------------------
# KTMAuthenticator — construction and properties
# ---------------------------------------------------------------------------

class TestKTMAuthenticatorInit:
    def test_default_no_credentials(self):
        auth = KTMAuthenticator(email="  ", password="  ")
        assert not auth.has_credentials()
        assert not auth.is_authenticated

    def test_with_explicit_credentials(self):
        auth = KTMAuthenticator(email="test@example.com", password="secret123")
        assert auth.has_credentials()
        assert auth.email == "test@example.com"
        assert auth.password == "secret123"
        assert auth.masked_email == "tes****@example.com"
        assert not auth.is_authenticated

    def test_masked_email_property(self):
        auth = KTMAuthenticator(email="elvinhui0217@gmail.com", password="pwd")
        assert auth.masked_email == "elv****@gmail.com"


# ---------------------------------------------------------------------------
# KTMAuthenticator — login flow
# ---------------------------------------------------------------------------

class TestKTMAuthenticatorLogin:
    def _mock_page(self, login_visible_after=False, has_error=False):
        """
        Creates a mock Playwright page that simulates the KITS login form.
        """
        page = MagicMock()
        page.url = "https://online.ktmb.com.my/" if not login_visible_after else "https://online.ktmb.com.my/Account/Login"

        # Email input
        email_locator = MagicMock()
        email_locator.wait_for = MagicMock()
        email_locator.fill = MagicMock()

        # Password input
        password_locator = MagicMock()
        password_locator.wait_for = MagicMock()
        password_locator.fill = MagicMock()

        # Login button
        login_btn = MagicMock()
        login_btn.wait_for = MagicMock()
        login_btn.click = MagicMock()

        # Login / sign up link (gone after successful login)
        login_link = MagicMock()
        if login_visible_after:
            login_link.count.return_value = 1
            login_link.first.is_visible.return_value = True
        else:
            login_link.count.return_value = 0

        # Validation/error modals
        validation_modal = MagicMock()
        validation_modal.is_visible.return_value = has_error

        popup_modal = MagicMock()
        popup_modal.is_visible.return_value = False

        error_spans = MagicMock()
        error_spans.count.return_value = 0

        # Dismiss modals
        dismiss_locator = MagicMock()
        dismiss_locator.first.is_visible.return_value = False

        def locator_handler(selector):
            sel = selector.strip()
            if sel == "#Email":
                return email_locator
            elif sel == "#Password":
                return password_locator
            elif sel == "#LoginButton":
                return login_btn
            elif "Login / sign up" in sel:
                return login_link
            elif sel == "#validationSummaryModal":
                return validation_modal
            elif sel == "#popupModal":
                return popup_modal
            elif "field-validation-error" in sel:
                return error_spans
            elif sel == "#validationSummaryModalBody":
                body = MagicMock()
                body.count.return_value = 0
                return body
            elif sel == "#popupModalBody":
                body = MagicMock()
                body.count.return_value = 0
                return body
            else:
                return dismiss_locator

        page.locator = locator_handler
        page.goto = MagicMock()
        page.wait_for_load_state = MagicMock()

        return page

    def test_login_success(self):
        auth = KTMAuthenticator(email="test@example.com", password="secret123")
        page = self._mock_page(login_visible_after=False)

        result = auth.login(page)

        assert result is True
        assert auth.is_authenticated

    def test_login_failure_still_on_login_page(self):
        auth = KTMAuthenticator(email="test@example.com", password="wrong")
        page = self._mock_page(login_visible_after=True)

        result = auth.login(page)

        assert result is False
        assert not auth.is_authenticated

    def test_login_no_credentials(self):
        auth = KTMAuthenticator(email="  ", password="  ")
        page = MagicMock()

        result = auth.login(page)

        assert result is False
        assert not auth.is_authenticated
        # Should not attempt to navigate
        page.goto.assert_not_called()

    def test_login_exception_handled(self):
        auth = KTMAuthenticator(email="test@example.com", password="pwd")
        page = MagicMock()
        page.goto.side_effect = Exception("Network error")

        result = auth.login(page)

        assert result is False
        assert not auth.is_authenticated

    def test_login_with_error_modal(self):
        auth = KTMAuthenticator(email="test@example.com", password="wrong")
        page = self._mock_page(login_visible_after=True, has_error=True)

        result = auth.login(page)

        assert result is False
        assert not auth.is_authenticated


# ---------------------------------------------------------------------------
# KTMAuthenticator — verify_login
# ---------------------------------------------------------------------------

class TestVerifyLogin:
    def _create_mock_page(self, url="https://online.ktmb.com.my/", has_login_link=False):
        page = MagicMock()
        page.url = url

        nav = MagicMock()
        nav.count.return_value = 1
        nav.first.is_visible.return_value = True

        login_link = MagicMock()
        if has_login_link:
            login_link.count.return_value = 1
            login_link.first.is_visible.return_value = True
        else:
            login_link.count.return_value = 0

        def locator_fn(selector):
            if "nav.navbar" in selector:
                return nav
            elif "Login / sign up" in selector:
                return login_link
            m = MagicMock()
            m.count.return_value = 0
            return m

        page.locator = locator_fn
        return page

    def test_logged_in_no_login_link(self):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        page = self._create_mock_page(has_login_link=False)

        assert auth.verify_login(page) is True
        assert auth.is_authenticated

    def test_not_logged_in_login_link_visible(self):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        page = self._create_mock_page(has_login_link=True)

        assert auth.verify_login(page) is False
        assert not auth.is_authenticated

    def test_verify_on_about_blank(self):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        page = MagicMock()
        page.url = "about:blank"
        assert auth.verify_login(page) is False

    def test_verify_exception_on_login_page(self):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        page = MagicMock()
        page.url = "https://online.ktmb.com.my/Account/Login"
        assert auth.verify_login(page) is False


# ---------------------------------------------------------------------------
# KTMAuthenticator — ensure_authenticated
# ---------------------------------------------------------------------------

class TestEnsureAuthenticated:
    def test_already_authenticated(self):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        page = MagicMock()
        page.url = "https://online.ktmb.com.my/"
        nav = MagicMock()
        nav.count.return_value = 1
        nav.first.is_visible.return_value = True
        login_link = MagicMock()
        login_link.count.return_value = 0

        def locator_fn(selector):
            if "nav.navbar" in selector:
                return nav
            return login_link

        page.locator = locator_fn

        result = auth.ensure_authenticated(page)
        assert result is True

    @patch.object(KTMAuthenticator, "login", return_value=True)
    @patch.object(KTMAuthenticator, "verify_login", return_value=False)
    def test_login_called_when_not_authenticated(self, mock_verify, mock_login):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        page = MagicMock()

        result = auth.ensure_authenticated(page)
        assert result is True
        mock_login.assert_called_once()

    @patch.object(KTMAuthenticator, "login", return_value=False)
    @patch.object(KTMAuthenticator, "verify_login", return_value=False)
    def test_all_retries_exhausted(self, mock_verify, mock_login):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        auth.MAX_RETRIES = 2
        auth.RETRY_DELAY_SECONDS = 0.01  # Speed up test
        page = MagicMock()

        result = auth.ensure_authenticated(page)
        assert result is False
        assert mock_login.call_count == 2

    @patch.object(KTMAuthenticator, "login", return_value=False)
    @patch.object(KTMAuthenticator, "verify_login", return_value=False)
    def test_notifier_called_on_failure(self, mock_verify, mock_login):
        auth = KTMAuthenticator(email="t@t.com", password="p")
        auth.MAX_RETRIES = 1
        auth.RETRY_DELAY_SECONDS = 0.01
        page = MagicMock()
        notifier = MagicMock()

        result = auth.ensure_authenticated(page, notifier=notifier)
        assert result is False
        notifier.send_raw_message.assert_called_once()
