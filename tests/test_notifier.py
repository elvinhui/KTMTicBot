import pytest
from unittest.mock import MagicMock
from ktm_sniper.notifier import TelegramTicketNotifier

def test_telegram_notifier_masking():
    notifier = TelegramTicketNotifier(bot_token="token", chat_id="123")
    assert notifier.mask_sensitive_data("A1234567") == "A123****"
    assert notifier.mask_sensitive_data("123") == "****"
    assert notifier.mask_sensitive_data("900101-14-5566") == "900101-14-****"

def test_telegram_notifier_formatting():
    notifier = TelegramTicketNotifier(bot_token="token", chat_id="123")
    trip_details = {
        "train_no": "EG9022",
        "class": "ETS Gold",
        "origin": "KL Sentral",
        "destination": "Butterworth",
        "departure_time": "2026-09-20 08:30"
    }

    message = notifier.format_message(
        booking_id="KITS-998811",
        trip_details=trip_details,
        passenger_name="Siti Nurhaliza",
        raw_id="950505105544"
    )

    assert "KITS-998811" in message
    assert "EG9022" in message
    assert "95050510****" in message
    assert "checkout?bookingId=KITS-998811" in message
    assert "950505105544" not in message

def test_telegram_notifier_multi_passenger_formatting():
    from ktm_sniper.models import Passenger
    notifier = TelegramTicketNotifier(bot_token="token", chat_id="123")
    trip_details = {
        "train_no": "EP9120",
        "class": "ETS Platinum",
        "origin": "KL Sentral",
        "destination": "Padang Besar",
        "departure_time": "2026-09-20 07:00"
    }
    p1 = Passenger(name="Alice Smith", id_number="910101-14-1122", gender="Female", phone="0111111111")
    p2 = Passenger(name="Bob Smith", id_number="920202-14-2233", gender="Male", phone="0222222222")

    msg = notifier.format_message(
        booking_id="KITS-MULTI-88",
        trip_details=trip_details,
        passenger_name="Alice Smith",
        raw_id="910101-14-1122",
        passengers=[p1, p2]
    )

    assert "*Passengers* (2):" in msg
    assert "Alice Smith (910101-14-****)" in msg
    assert "Bob Smith (920202-14-****)" in msg
    assert "910101-14-1122" not in msg
    assert "920202-14-2233" not in msg

def test_telegram_notifier_send_text():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.post.return_value = mock_response

    notifier = TelegramTicketNotifier(bot_token="fake_token", chat_id="fake_chat", session=mock_session)
    success = notifier.send_alert("KITS-112233", {"train_no": "EG9022"}, "John Doe", "A1234567")
    assert success is True
    mock_session.post.assert_called_once()
    args, kwargs = mock_session.post.call_args
    assert "sendMessage" in args[0]
    assert kwargs["json"]["chat_id"] == "fake_chat"

def test_telegram_notifier_send_photo(tmp_path):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.post.return_value = mock_response

    notifier = TelegramTicketNotifier(bot_token="fake_token", chat_id="fake_chat", session=mock_session)
    dummy_img = tmp_path / "screenshot.png"
    dummy_img.write_bytes(b"\x89PNG\r\n\x1a\nfake_image_data")

    success = notifier.send_photo_alert(str(dummy_img), caption="Reservation Success!")
    assert success is True
    args, kwargs = mock_session.post.call_args
    assert "sendPhoto" in args[0]
    assert kwargs["data"]["chat_id"] == "fake_chat"
    assert "photo" in kwargs["files"]
