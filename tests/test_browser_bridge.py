import pytest
from unittest.mock import MagicMock
from ktm_sniper.browser.bridge import BrowserContextBridge
from ktm_sniper.models import SniperTaskConfig

def test_bridge_search_trips_success():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = {
        "trips": [
            {
                "train_no": "EG9022",
                "train_class": "ETS Gold",
                "departure_time": "08:30",
                "arrival_time": "12:45",
                "available_seats": 5,
                "fare": 59.0,
                "trip_id": "T1"
            }
        ]
    }

    bridge = BrowserContextBridge(page=mock_page)
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20"
    )

    trips = bridge.search_trips_via_inpage_fetch(config)
    assert len(trips) == 1
    assert trips[0].train_no == "EG9022"
    assert trips[0].available_seats == 5

def test_bridge_search_trips_error():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = {"error": 403}

    bridge = BrowserContextBridge(page=mock_page)
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20"
    )

    trips = bridge.search_trips_via_inpage_fetch(config)
    assert trips == []
