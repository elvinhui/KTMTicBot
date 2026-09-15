import pytest
from unittest.mock import MagicMock
from main import (
    KTMStationRegistry,
    CircuitBreaker,
    CircuitBreakerOpenException,
    KTMTicketChecker,
    AdaptivePoller,
    KTMSeatReserver,
    TelegramTicketNotifier
)

# --- Phase 1 Tests: Station Registry & Ticket Checker ---

def test_station_registry_valid():
    assert KTMStationRegistry.get_code("KL Sentral") == "KLS"
    assert KTMStationRegistry.get_code("Padang Besar") == "PDB"
    assert KTMStationRegistry.get_code("  ipoh  ") == "IPH"

def test_station_registry_invalid():
    with pytest.raises(ValueError):
        KTMStationRegistry.get_code("Unknown Station")

def test_ticket_checker_success():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "trips": [
            {"train_no": "EG9022", "departure_time": "08:00", "available_seats": 5}
        ]
    }
    mock_session.post.return_value = mock_response

    checker = KTMTicketChecker(session=mock_session)
    trips = checker.search_trips("KL Sentral", "Ipoh", "2026-09-15", 1)
    
    assert len(trips) == 1
    assert trips[0]["train_no"] == "EG9022"
    assert trips[0]["available_seats"] == 5

# --- Phase 2 Tests: Circuit Breaker & Adaptive Poller ---

def test_circuit_breaker_transitions():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.5)
    assert cb.state == "CLOSED"
    assert cb.can_execute() is True

    # First failure
    cb.record_failure()
    assert cb.state == "CLOSED"

    # Second failure triggers OPEN state
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_execute() is False

    # Wait for recovery timeout to transition to HALF_OPEN
    import time
    time.sleep(0.6)
    assert cb.can_execute() is True
    assert cb.state == "HALF_OPEN"

    # Success resets state to CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"

def test_ticket_checker_circuit_breaker_integration():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_session.post.return_value = mock_response

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10)
    checker = KTMTicketChecker(session=mock_session, circuit_breaker=cb)

    # First request fails and trips the breaker
    results = checker.search_trips("KL Sentral", "Ipoh", "2026-09-15", 1)
    assert results == []
    assert cb.state == "OPEN"

    # Subsequent request immediately raises CircuitBreakerOpenException without calling session
    with pytest.raises(CircuitBreakerOpenException):
        checker.search_trips("KL Sentral", "Ipoh", "2026-09-15", 1)
    
    mock_session.post.assert_called_once()

def test_adaptive_poller_jitter():
    poller = AdaptivePoller(base_interval=5.0, jitter=1.0)
    delays = [poller.get_next_delay() for _ in range(50)]
    
    for delay in delays:
        assert delay >= 1.0  # Must respect the minimum safety threshold

# --- Phase 3 Tests: Seat Reservation ---

def test_seat_reserver_validation():
    reserver = KTMSeatReserver()
    invalid_passenger = {"name": "John Doe", "id_number": "", "gender": "Male", "phone": "0123456789"}
    
    with pytest.raises(ValueError, match="Missing required passenger field: id_number"):
        reserver.reserve_seat("TRIP123", "Window", invalid_passenger)

def test_seat_reserver_success():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "SUCCESS",
        "booking_id": "KITS-998877",
        "timeout_minutes": 15
    }
    mock_session.post.return_value = mock_response

    reserver = KTMSeatReserver(session=mock_session)
    passenger = {"name": "John Doe", "id_number": "A1234567", "gender": "Male", "phone": "0123456789"}
    
    result = reserver.reserve_seat("TRIP123", "Window", passenger)
    assert result["booking_id"] == "KITS-998877"
    assert result["status"] == "SUCCESS"

# --- Phase 4 Tests: Telegram Notifications & Data Masking ---

def test_telegram_notifier_masking():
    notifier = TelegramTicketNotifier(bot_token="token", chat_id="123")
    assert notifier.mask_sensitive_data("A1234567") == "A123****"
    assert notifier.mask_sensitive_data("123") == "****"

def test_telegram_notifier_formatting():
    notifier = TelegramTicketNotifier(bot_token="token", chat_id="123")
    trip_details = {
        "train_no": "EG9022",
        "class": "ETS Gold",
        "origin": "KL Sentral",
        "destination": "Ipoh",
        "departure_time": "2026-09-15 08:00"
    }
    
    message = notifier.format_message(
        booking_id="KITS-112233",
        trip_details=trip_details,
        passenger_name="John Doe",
        raw_id="A1234567"
    )
    
    assert "KITS-112233" in message
    assert "EG9022" in message
    assert "A123****" in message
    assert "checkout?bookingId=KITS-112233" in message
    assert "A1234567" not in message  # Ensure unmasked ID is not leaked

def test_telegram_notifier_send():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.post.return_value = mock_response

    notifier = TelegramTicketNotifier(bot_token="fake_token", chat_id="fake_chat", session=mock_session)
    trip_details = {"train_no": "EG9022"}
    
    success = notifier.send_alert("KITS-112233", trip_details, "John Doe", "A1234567")
    assert success is True
    mock_session.post.assert_called_once()