import pytest
from unittest.mock import MagicMock
from ktm_sniper.checker import KTMTicketChecker
from ktm_sniper.network.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException
from ktm_sniper.network.kits_parser import ParsedTrip
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

from ktm_sniper.network.session import KITSClient

def test_checker_with_kits_client_parsed_trips():
    mock_kits = MagicMock(spec=KITSClient)
    mock_kits.search_trips.return_value = [
        ParsedTrip(
            train_service="Gold - 9044",
            train_no="9044",
            train_class="ETS Gold",
            depart_time="08:55",
            arrive_time="11:29",
            available_seats=10,
            min_fare=52.0,
            is_available=True
        ),
        ParsedTrip(
            train_service="Gold - 9052",
            train_no="9052",
            train_class="ETS Gold",
            depart_time="15:00",
            arrive_time="17:34",
            available_seats=0,
            min_fare=51.0,
            is_available=False
        )
    ]

    checker = KTMTicketChecker(session=mock_kits)
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Ipoh",
        date="2026-10-05",
        time_from="07:00",
        time_to="10:00",
        required_seats=2
    )

    matched = checker.find_matching_trips(config)
    assert len(matched) == 1
    assert matched[0].train_no == "9044"
    assert matched[0].available_seats == 10
    assert matched[0].fare == 52.0

def test_checker_circuit_breaker_open():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
    cb.record_failure()
    checker = KTMTicketChecker(session=MagicMock(), circuit_breaker=cb)
    with pytest.raises(CircuitBreakerOpenException):
        checker.search_trips("KL Sentral", "Ipoh", "2026-10-05")
