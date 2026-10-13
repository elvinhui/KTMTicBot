import pytest
from unittest.mock import MagicMock, patch, PropertyMock
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

def test_browser_driver_fill_and_submit_passenger_form_with_qr(tmp_path):
    mock_page = MagicMock()
    mock_page.url = "https://online.ktmb.com.my/Payment/Checkout?bookingId=TEST-12345"
    mock_page.content.return_value = "<html><body>Fake Payment Page</body></html>"
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_page.context = mock_context

    mock_frame = MagicMock()
    mock_frame.name = "payment_frame"
    mock_frame.url = "https://gateway.duitnow.my/checkout"
    mock_page.frames = [mock_frame]

    mock_qr_elem = MagicMock()
    mock_qr_elem.is_visible.return_value = True
    mock_qr_elem.bounding_box.return_value = {"width": 150, "height": 150}

    # Simulate locator finding QR element
    mock_matches = MagicMock()
    mock_matches.count.return_value = 1
    mock_matches.nth.return_value = mock_qr_elem
    mock_frame.locator.return_value = mock_matches

    # Simulate clicking DuitNow and Proceed buttons
    mock_page.evaluate.side_effect = [
        {"found": True, "id": "btnGoPaymentDuitNow"},  # click DuitNow
        {"clicked": True, "text": "CLICK HERE TO PROCEED TO PAYMENT GATEWAY"}  # click Proceed
    ]

    # Create dummy screenshot when mock_qr_elem.screenshot is called
    def fake_screenshot(path):
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"PNG_FAKE_IMAGE_DATA_BYTES_OVER_2000" * 100)

    mock_qr_elem.screenshot.side_effect = fake_screenshot

    # Setup mock_page.locator default behavior for form inputs and buttons
    mock_default_loc = MagicMock()
    mock_default_loc.count.return_value = 1
    mock_default_loc.is_visible.return_value = True
    mock_page.locator.return_value = mock_default_loc
    mock_default_loc.first = mock_default_loc
    mock_default_loc.last = mock_default_loc

    driver = KTMBrowserDriver(page=mock_page)
    passengers = [{"name": "TAN JIA HUI", "id_number": "960217075045", "phone": "0123456789", "gender": "Male"}]

    res = driver.fill_and_submit_passenger_form(passengers)
    assert res["status"] == "SUCCESS"
    assert res["booking_id"] == "TEST-12345"
    assert "https://" in res["payment_url"]
    assert res["payment_qr"] is not None


def test_browser_driver_lock_seat_auto_uses_passenger_count():
    from ktm_sniper.models import Passenger

    mock_page = MagicMock()
    mock_page.url = "https://online.ktmb.com.my/Trip"

    def locator_side_effect(selector):
        loc = MagicMock()
        loc.first = loc
        loc.is_visible.return_value = True
        loc.count.return_value = 0 if "return-trips" in selector else 1
        return loc

    mock_page.locator.side_effect = locator_side_effect

    # Configure mock_page.evaluate to return selected seats for 3 passengers
    def eval_side_effect(script, arg=None):
        if isinstance(arg, dict) and "targetCount" in arg:
            assert arg["targetCount"] == 3
            assert arg["pref"] == "AUTO"
            return "Coach B - 03A, Coach B - 03B, Coach B - 03C"
        return ""

    mock_page.evaluate.side_effect = eval_side_effect

    driver = KTMBrowserDriver(page=mock_page)
    driver._last_searched_pax = 3
    task = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        passengers=[
            Passenger(name="PAX 1", id_number="900101015001", phone="0123456789", gender="MALE"),
            Passenger(name="PAX 2", id_number="920202025002", phone="0123456789", gender="FEMALE"),
            Passenger(name="PAX 3", id_number="940303035003", phone="0123456789", gender="MALE"),
        ],
    )

    with patch("ktm_sniper.browser.driver.time.sleep"):
        locked = driver.lock_seat_and_proceed(seat_no="auto", train_no="EG9022", task=task)

    assert locked == "Coach B - 03A, Coach B - 03B, Coach B - 03C"
    assert driver.last_locked_seat == "Coach B - 03A, Coach B - 03B, Coach B - 03C"


def test_browser_driver_scrape_upcoming_bookings():
    mock_page = MagicMock()
    mock_page.url = "https://online.ktmb.com.my/Booking/UpcomingList"
    mock_page.evaluate.return_value = [
        {
            "booking_no": "K261049574760",
            "route": "JB SENTRAL BUKIT MERTAJAM",
            "depart_date": "20 Oct 2026",
            "return_date": "27 Oct 2026",
            "print_url": "https://online.ktmb.com.my/Booking/PrintTicket?p=K261049574760",
            "legs": [
                {
                    "route": "JB SENTRAL BUKIT MERTAJAM",
                    "train": "Platinum - 9326",
                    "datetime": "20 Oct 2026 / 09:55",
                    "tickets": [{"seat": "B / 2C", "ticket_no": "T261013679970", "passenger": "TAN JIA HUI"}],
                }
            ],
        }
    ]
    driver = KTMBrowserDriver(page=mock_page)
    bookings = driver.scrape_upcoming_bookings()
    assert len(bookings) == 1
    assert bookings[0]["booking_no"] == "K261049574760"
    assert bookings[0]["legs"][0]["tickets"][0]["ticket_no"] == "T261013679970"


def test_dismiss_popup_alerts_and_ensure_on_book_page_recovery():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = True
    driver = KTMBrowserDriver(page=mock_page)

    # 1. Test dismiss_popup_alerts
    dismissed = driver.dismiss_popup_alerts()
    assert dismissed is True

    # 2. Test _ensure_on_book_page recovering from /Trip alert modal
    # First check is on /Trip with alert, then after retry lands on /Book
    urls = ["https://online.ktmb.com.my/Trip", "https://online.ktmb.com.my/Book"]
    def _mock_url():
        return urls.pop(0) if urls else "https://online.ktmb.com.my/Book"

    type(mock_page).url = PropertyMock(side_effect=_mock_url)
    driver._click_proceed_to_passenger = MagicMock(return_value=True)

    # Should not raise because retry succeeds into /Book
    driver._ensure_on_book_page(max_retries=1)
    driver._click_proceed_to_passenger.assert_called()
