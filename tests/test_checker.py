import pytest
from unittest.mock import MagicMock
from ktm_sniper.checker import KTMTicketChecker
from ktm_sniper.network.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException
from ktm_sniper.models import SniperTaskConfig

def test_checker_search_trips_success():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "trips": [
            {
                "train_no": "EG9022",
                "class": "ETS Gold",
                "departure_time": "08:30",
                "arrival_time": "12:45",
                "available_seats": 5,
                "fare": 59.0
            }
        ]
    }
    mock_session.post.return_value = mock_response

    checker = KTMTicketChecker(session=mock_session)
    trips = checker.search_trips("KL Sentral", "Butterworth", "2026-09-20", passengers=1)
    assert len(trips) == 1
    assert trips[0]["train_no"] == "EG9022"

def test_checker_find_matching_trips():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "trips": [
            {"train_no": "EG9022", "train_class": "ETS Gold", "departure_time": "08:30", "arrival_time": "12:45", "available_seats": 5},
            {"train_no": "EP9204", "train_class": "ETS Platinum", "departure_time": "20:00", "arrival_time": "23:55", "available_seats": 0}
        ]
    }
    mock_session.post.return_value = mock_response

    checker = KTMTicketChecker(session=mock_session)
    # Looking for morning train with at least 1 seat
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        time_from="08:00",
        time_to="12:00",
        required_seats=1
    )
    matched = checker.find_matching_trips(config)
    assert len(matched) == 1
    assert matched[0].train_no == "EG9022"
