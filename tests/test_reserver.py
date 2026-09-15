import pytest
from unittest.mock import MagicMock
from ktm_sniper.reserver import KTMSeatReserver
from ktm_sniper.models import Passenger

def test_validate_passenger_dict_missing_fields():
    reserver = KTMSeatReserver()
    with pytest.raises(ValueError, match="Missing required passenger field: id_number"):
        reserver.validate_passenger({"name": "Test", "id_number": "   ", "gender": "Male", "phone": "012345"})

    with pytest.raises(ValueError, match="Missing required passenger field: name"):
        reserver.validate_passenger({"id_number": "123456", "gender": "Male", "phone": "012345"})

def test_validate_passenger_invalid_format():
    reserver = KTMSeatReserver()
    # Invalid gender
    with pytest.raises(ValueError, match="Invalid gender"):
        reserver.validate_passenger({"name": "Test", "id_number": "900101145566", "gender": "Alien", "phone": "012345"})

    # Invalid ID (too short)
    with pytest.raises(ValueError, match="Invalid ID number"):
        reserver.validate_passenger({"name": "Test", "id_number": "12", "gender": "Male", "phone": "012345"})

def test_reserve_seat_mock_session_none():
    reserver = KTMSeatReserver()
    passenger = {"name": "John Doe", "id_number": "900101-14-5566", "gender": "Male", "phone": "0123456789"}
    res = reserver.reserve_seat("TRIP-01", "Window", passenger)
    assert res["status"] == "MOCK_SUCCESS"
    assert res["booking_id"] == "MOCK-123456"
    assert res["timeout_minutes"] == 15

def test_reserve_seat_with_passenger_model():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "SUCCESS",
        "booking_id": "KITS-889900",
        "timeout_minutes": 15,
        "payment_url": "https://online.ktmb.com.my/v2/payment/checkout?bookingId=KITS-889900"
    }
    mock_session.post.return_value = mock_response

    reserver = KTMSeatReserver(session=mock_session)
    p = Passenger(name="John Doe", id_number="A1234567", gender="Male", phone="0123456789")
    res = reserver.reserve_seat("TRIP-01", "Aisle", p)
    assert res["status"] == "SUCCESS"
    assert res["booking_id"] == "KITS-889900"

def test_reserve_seat_http_error():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 409
    mock_session.post.return_value = mock_response

    reserver = KTMSeatReserver(session=mock_session)
    passenger = {"name": "John Doe", "id_number": "A1234567", "gender": "Male", "phone": "0123456789"}
    with pytest.raises(RuntimeError, match="Reservation failed with status code: 409"):
        reserver.reserve_seat("TRIP-01", "Window", passenger)

def test_reserve_seat_multi_passenger():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "SUCCESS",
        "booking_id": "KITS-MULTI-77",
        "timeout_minutes": 15,
        "payment_url": "https://online.ktmb.com.my/v2/payment/checkout?bookingId=KITS-MULTI-77"
    }
    mock_session.post.return_value = mock_response

    reserver = KTMSeatReserver(session=mock_session)
    p1 = Passenger(name="John Doe", id_number="A1234567", gender="Male", phone="0123456789")
    p2 = Passenger(name="Jane Doe", id_number="B7654321", gender="Female", phone="0198765432")

    res = reserver.reserve_seat("TRIP-01", "Window", passengers=[p1, p2])
    assert res["status"] == "SUCCESS"
    assert res["booking_id"] == "KITS-MULTI-77"

    # Verify payload sent
    sent_payload = mock_session.post.call_args[1]["json"]
    assert len(sent_payload["passengers"]) == 2
    assert sent_payload["passengers"][0]["name"] == "John Doe"
    assert sent_payload["passengers"][1]["name"] == "Jane Doe"

def test_reserve_seat_no_passenger_raises():
    reserver = KTMSeatReserver()
    with pytest.raises(ValueError, match="At least one passenger must be provided"):
        reserver.reserve_seat("TRIP-01", "Window")
