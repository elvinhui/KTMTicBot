import os
import json
import pytest
from unittest.mock import MagicMock, patch
from ktm_sniper.browser.manager import BrowserManager
from ktm_sniper.auth import KTMAuthenticator
from ktm_sniper.telegram_bot import TelegramCommandHandler


def test_browser_manager_save_storage_state(tmp_path):
    mgr = BrowserManager(storage_state_path=str(tmp_path / "auth_state.json"))
    mgr.context = MagicMock()
    
    target_path = str(tmp_path / "sub" / "auth.json")
    saved = mgr.save_storage_state(target_path)
    assert saved == target_path
    mgr.context.storage_state.assert_called_once_with(path=target_path)


def test_browser_manager_inject_cookies():
    mgr = BrowserManager()
    mgr.context = MagicMock()
    cookies = [{"name": "ASP.NET_SessionId", "value": "12345"}]
    res = mgr.inject_cookies(cookies)
    assert res is True
    mgr.context.add_cookies.assert_called_once_with(cookies)


def test_authenticator_skips_form_if_verified():
    auth = KTMAuthenticator(email="test@example.com", password="password123")
    mock_page = MagicMock()
    mock_page.url = "https://online.ktmb.com.my/Home"
    
    with patch.object(auth, "verify_login", return_value=True), \
         patch.object(auth, "login") as mock_login:
        success = auth.ensure_authenticated(mock_page)
        assert success is True
        mock_login.assert_not_called()


def test_authenticator_saves_session_state(tmp_path):
    auth = KTMAuthenticator(email="test@example.com", password="password123")
    mock_page = MagicMock()
    target_file = str(tmp_path / "state.json")
    
    auth._save_session_state(mock_page, path=target_file)
    mock_page.context.storage_state.assert_called_once_with(path=target_file)


def test_telegram_set_cookie_raw_string(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = TelegramCommandHandler(engine=MagicMock(), authorized_chat_id=12345)
    
    cmd_text = "/set_cookie .AspNetCore.Cookies=secret_cookie_val; ASP.NET_SessionId=session123"
    result = handler._handle_set_cookie(cmd_text)
    
    assert "✓ 已成功更新并固化 2 个会话 Cookie" in result
    assert os.path.exists("data/auth_state.json")
    with open("data/auth_state.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data["cookies"]) == 2
        names = [c["name"] for c in data["cookies"]]
        assert ".AspNetCore.Cookies" in names
        assert "ASP.NET_SessionId" in names


def test_telegram_set_cookie_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = TelegramCommandHandler(engine=MagicMock(), authorized_chat_id=12345)
    
    raw_json = json.dumps([{"name": "test", "value": "val123", "domain": ".ktmb.com.my"}])
    cmd_text = f"/set_cookie {raw_json}"
    result = handler._handle_set_cookie(cmd_text)
    
    assert "✓ 已成功更新并固化 1 个会话 Cookie" in result
    with open("data/auth_state.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["cookies"][0]["name"] == "test"
