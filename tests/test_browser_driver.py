import pytest
from unittest.mock import MagicMock, patch
from ktm_sniper.browser.manager import BrowserManager
from ktm_sniper.browser.driver import KTMBrowserDriver
from ktm_sniper.models import SniperTaskConfig, TripInfo

def test_browser_manager_mock_lifecycle():
    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    with patch("ktm_sniper.browser.manager.sync_playwright") as mock_sync:
        mock_sync.return_value.start.return_value = mock_playwright

        bm = BrowserManager(headless=True)
        bm.start()
        assert bm.page is not None

        mock_playwright.chromium.launch.assert_called_once()
        bm.close()
        mock_browser.close.assert_called_once()

def test_browser_driver_fill_and_search():
    mock_page = MagicMock()
    driver = KTMBrowserDriver(page=mock_page)

    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20"
    )

    driver.navigate_to_booking()
    mock_page.goto.assert_called_with("https://online.ktmb.com.my", timeout=30000)

    driver.fill_search_criteria(config)
    # Ensure inputs are targeted
    assert mock_page.fill.call_count >= 2

def test_browser_driver_parse_trips_from_cards():
    mock_page = MagicMock()
    # Mock evaluate returning list of parsed trip dictionaries
    mock_page.evaluate.return_value = [
        {
            "train_no": "EG9022",
            "train_class": "ETS Gold",
            "origin": "KL Sentral",
            "destination": "Butterworth",
            "departure_time": "08:30",
            "arrival_time": "12:45",
            "available_seats": 10,
            "fare": 59.0,
            "trip_id": "TRIP-001"
        }
    ]

    driver = KTMBrowserDriver(page=mock_page)
    trips = driver.scrape_trips()
    assert len(trips) == 1
    assert isinstance(trips[0], TripInfo)
    assert trips[0].train_no == "EG9022"
    assert trips[0].available_seats == 10

def test_browser_driver_screenshot(tmp_path):
    mock_page = MagicMock()
    driver = KTMBrowserDriver(page=mock_page)
    out_path = tmp_path / "shot.png"

    driver.take_screenshot(str(out_path))
    mock_page.screenshot.assert_called_once_with(path=str(out_path), full_page=True)
