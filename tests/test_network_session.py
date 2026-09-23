import pytest
from unittest.mock import MagicMock, patch
from ktm_sniper.network.session import KITSClient
from ktm_sniper.network.kits_parser import StationIndex, ParsedTrip

def test_kits_client_creation():
    client = KITSClient(timeout=10.0)
    assert client.timeout == 10.0
    assert "User-Agent" in client.session.headers
    assert "online.ktmb.com.my" in client.session.headers["Origin"]
    client.close()

def test_kits_client_initialize_success():
    client = KITSClient(timeout=5.0)
    mock_resp = MagicMock()
    mock_resp.text = """
    <script>
    var groupedStations = [{"State":"KL","Stations":[{"Description":"KL SENTRAL","Id":"19100","TrainServices":["ETS"]}]}];
    var jsStations = [{"Id":"19100","StationData":"DATA123"}];
    </script>
    <input name="__RequestVerificationToken" value="CSRF123" />
    """
    with patch.object(client.session, "get", return_value=mock_resp):
        res = client.initialize()
        assert res is True
        assert client._initialized is True
        assert "KL SENTRAL" in client._station_index.name_to_id
        assert client._homepage_csrf == "CSRF123"
    client.close()

def test_kits_client_initialize_failure():
    client = KITSClient(timeout=5.0)
    with patch.object(client.session, "get", side_effect=Exception("Network error")):
        res = client.initialize()
        assert res is False
        assert client._initialized is False
    client.close()

def test_kits_client_search_trips_flow():
    client = KITSClient(timeout=5.0)
    client._initialized = True
    idx = StationIndex()
    idx.name_to_id = {"KL SENTRAL": "19100", "IPOH": "9000"}
    idx.id_to_data = {"19100": "D1", "9000": "D2"}
    client._station_index = idx
    client._homepage_csrf = "CSRF_ORIG"

    mock_post_fields = {
        "SearchData": "SD_TOKEN",
        "FormValidationCode": "FVC_TOKEN",
        "__RequestVerificationToken": "NEW_CSRF"
    }

    parsed_sample_trips = [
        ParsedTrip(
            train_service="Gold - 9044",
            train_no="9044",
            train_class="Gold",
            depart_time="08:55",
            arrive_time="11:29",
            available_seats=150,
            min_fare=52.0,
            is_available=True
        )
    ]

    with patch.object(client, "_post_trip_form", return_value=mock_post_fields) as mock_post_form:
        with patch.object(client, "_fetch_trip_list", return_value=parsed_sample_trips) as mock_fetch:
            trips = client.search_trips("KL Sentral", "Ipoh", "2026-10-05", passengers=1)
            assert len(trips) == 1
            assert trips[0].train_no == "9044"
            mock_post_form.assert_called_once()
            mock_fetch.assert_called_once_with("SD_TOKEN", "FVC_TOKEN", "2026-10-05", "NEW_CSRF")

            # Second call uses cached SearchData
            trips2 = client.search_trips("KL Sentral", "Ipoh", "2026-10-05", passengers=1)
            assert len(trips2) == 1
            # _post_trip_form should still only have been called once due to cache!
            assert mock_post_form.call_count == 1
            assert mock_fetch.call_count == 2
    client.close()

def test_kits_client_search_trips_invalid_station():
    client = KITSClient(timeout=5.0)
    client._initialized = True
    idx = StationIndex()
    client._station_index = idx
    trips = client.search_trips("UnknownStation", "AnotherUnknown", "2026-10-05")
    assert trips == []
    client.close()

def test_kits_client_search_trips_invalid_date():
    client = KITSClient(timeout=5.0)
    client._initialized = True
    idx = StationIndex()
    idx.name_to_id = {"KL SENTRAL": "19100", "IPOH": "9000"}
    client._station_index = idx
    trips = client.search_trips("KL Sentral", "Ipoh", "invalid-date")
    assert trips == []
    client.close()

def test_kits_client_post_trip_form_success():
    client = KITSClient(timeout=5.0)
    mock_resp = MagicMock()
    mock_resp.text = """
    <input type="hidden" id="SearchData" value="SD_VAL" />
    <input type="hidden" id="FormValidationCode" value="FVC_VAL" />
    <input type="hidden" name="__RequestVerificationToken" value="RVT_VAL" />
    """
    with patch.object(client.session, "post", return_value=mock_resp):
        fields = client._post_trip_form("19100", "D1", "9000", "D2", "05 Oct 2026", 1, "CSRF")
        assert fields["SearchData"] == "SD_VAL"
        assert fields["FormValidationCode"] == "FVC_VAL"
    client.close()

def test_kits_client_post_trip_form_missing_searchdata_raises():
    client = KITSClient(timeout=5.0)
    mock_resp = MagicMock()
    mock_resp.text = "<html><body>No search data here</body></html>"
    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(RuntimeError):
            client._post_trip_form("19100", "D1", "9000", "D2", "05 Oct 2026", 1, "CSRF")
    client.close()

def test_kits_client_fetch_trip_list_success():
    client = KITSClient(timeout=5.0)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": True,
        "data": """
        <table><tbody class="depart-trips">
          <tr>
            <td>Gold - 9044</td>
            <td>08:55</td>
            <td>11:29</td>
            <td>2h 34m</td>
            <td>253</td>
            <td>MYR 52.00</td>
            <td>Select</td>
          </tr>
        </tbody></table>
        """
    }
    with patch.object(client.session, "post", return_value=mock_resp):
        trips = client._fetch_trip_list("SD", "FVC", "2026-10-05", "CSRF")
        assert len(trips) == 1
        assert trips[0].train_no == "9044"
    client.close()

def test_kits_client_fetch_trip_list_status_false():
    client = KITSClient(timeout=5.0)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": False,
        "messages": ["No train found"]
    }
    with patch.object(client.session, "post", return_value=mock_resp):
        trips = client._fetch_trip_list("SD", "FVC", "2026-10-05", "CSRF")
        assert trips == []
    client.close()

def test_kits_client_search_trips_post_form_error():
    client = KITSClient(timeout=5.0)
    client._initialized = True
    idx = StationIndex()
    idx.name_to_id = {"KL SENTRAL": "19100", "IPOH": "9000"}
    idx.id_to_data = {"19100": "D1", "9000": "D2"}
    client._station_index = idx
    with patch.object(client, "_post_trip_form", side_effect=Exception("Form post error")):
        trips = client.search_trips("KL Sentral", "Ipoh", "2026-10-05")
        assert trips == []
        assert client._initialized is False
    client.close()

def test_kits_client_search_trips_fetch_list_error():
    client = KITSClient(timeout=5.0)
    client._initialized = True
    idx = StationIndex()
    idx.name_to_id = {"KL SENTRAL": "19100", "IPOH": "9000"}
    idx.id_to_data = {"19100": "D1", "9000": "D2"}
    client._station_index = idx
    mock_fields = {"SearchData": "SD", "FormValidationCode": "FVC", "__RequestVerificationToken": "CSRF"}
    with patch.object(client, "_post_trip_form", return_value=mock_fields):
        with patch.object(client, "_fetch_trip_list", side_effect=Exception("Fetch list error")):
            trips = client.search_trips("KL Sentral", "Ipoh", "2026-10-05")
            assert trips == []
    client.close()

def test_kits_client_get_and_post_proxies():
    client = KITSClient(timeout=5.0)
    with patch.object(client.session, "get", return_value="GET_OK") as mock_get:
        assert client.get("http://example.com") == "GET_OK"
        mock_get.assert_called_once()
    with patch.object(client.session, "post", return_value="POST_OK") as mock_post:
        assert client.post("http://example.com", data={}) == "POST_OK"
        mock_post.assert_called_once()
    client.close()
