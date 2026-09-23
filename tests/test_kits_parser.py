"""
tests/test_kits_parser.py
Unit tests for the KITS HTML parser module.
"""
import pytest
from ktm_sniper.network.kits_parser import (
    ParsedTrip,
    StationIndex,
    parse_stations,
    parse_trip_html,
    extract_hidden_fields,
    extract_csrf,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_HOMEPAGE_HTML = """
<html><body>
<script>
var groupedStations = [
  {"State": "KL", "Stations": [
    {"Description": "KL SENTRAL", "Id": "19100", "TrainServices": ["ETS"]},
    {"Description": "KEPONG SENTRAL", "Id": "18400", "TrainServices": ["ETS"]}
  ]},
  {"State": "PERAK", "Stations": [
    {"Description": "IPOH", "Id": "9000", "TrainServices": ["ETS"]}
  ]}
];
var jsStations = [
  {"Id": "19100", "StationData": "ENCRYPTED_KL_DATA=="},
  {"Id": "18400", "StationData": "ENCRYPTED_KEPONG_DATA=="},
  {"Id": "9000",  "StationData": "ENCRYPTED_IPOH_DATA=="}
];
</script>
<input name="__RequestVerificationToken" value="TEST_CSRF_TOKEN" />
</body></html>
"""

SAMPLE_TRIP_HTML = """
<div>
  <table>
    <tbody class="depart-trips">
      <tr class="text-nowrap" data-HourMinute="0855">
        <td class="f20 blue-left-border">Gold - 9044</td>
        <td class="text-center f22">08:55</td>
        <td class="text-center f22">11:29</td>
        <td class="th-hr">2h <span>34m</span></td>
        <td><i class="fa"></i> 253 </td>
        <td class="text-center f16">MYR 52.00</td>
        <td><a href="#">Select</a></td>
      </tr>
      <tr class="text-nowrap" data-HourMinute="1200">
        <td class="f20 blue-left-border">Express - 9008</td>
        <td class="text-center f22">12:00</td>
        <td class="text-center f22">14:00</td>
        <td class="th-hr">2h <span>0m</span></td>
        <td><i class="fa"></i> 0 </td>
        <td class="text-center f16">MYR 58.00</td>
        <td><a href="#">Select</a></td>
      </tr>
      <tr class="text-nowrap" data-HourMinute="1500">
        <td class="f20 blue-left-border">Gold - 9052</td>
        <td class="text-center f22">15:00</td>
        <td class="text-center f22">17:34</td>
        <td class="th-hr">2h <span>34m</span></td>
        <td><i class="fa"></i> 179 </td>
        <td class="text-center f16">MYR 51.00</td>
        <td><a href="#">Select</a></td>
      </tr>
    </tbody>
  </table>
</div>
"""

SAMPLE_TRIP_RESPONSE_HTML = """
<html><body>
<input type="hidden" id="SearchData" value="SEARCH_DATA_BLOB=" />
<input type="hidden" id="FormValidationCode" value="FORM_VAL_CODE_BLOB=" />
<input type="hidden" name="__RequestVerificationToken" value="NEW_CSRF_TOKEN" />
</body></html>
"""


# ── parse_stations ────────────────────────────────────────────────────────────

class TestParseStations:
    def test_returns_station_index(self):
        index = parse_stations(SAMPLE_HOMEPAGE_HTML)
        assert isinstance(index, StationIndex)

    def test_finds_kl_sentral(self):
        index = parse_stations(SAMPLE_HOMEPAGE_HTML)
        assert "KL SENTRAL" in index.name_to_id

    def test_finds_ipoh(self):
        index = parse_stations(SAMPLE_HOMEPAGE_HTML)
        assert "IPOH" in index.name_to_id

    def test_correct_station_count(self):
        index = parse_stations(SAMPLE_HOMEPAGE_HTML)
        assert len(index.name_to_id) == 3

    def test_id_mapping(self):
        index = parse_stations(SAMPLE_HOMEPAGE_HTML)
        assert index.name_to_id["KL SENTRAL"] == "19100"
        assert index.name_to_id["IPOH"] == "9000"

    def test_station_data_mapping(self):
        index = parse_stations(SAMPLE_HOMEPAGE_HTML)
        assert index.id_to_data["19100"] == "ENCRYPTED_KL_DATA=="
        assert index.id_to_data["9000"] == "ENCRYPTED_IPOH_DATA=="

    def test_empty_html_returns_empty_index(self):
        index = parse_stations("<html></html>")
        assert len(index.name_to_id) == 0


# ── StationIndex.resolve ──────────────────────────────────────────────────────

class TestStationIndexResolve:
    def setup_method(self):
        self.index = parse_stations(SAMPLE_HOMEPAGE_HTML)

    def test_exact_match(self):
        sid, data = self.index.resolve("KL SENTRAL")
        assert sid == "19100"
        assert data == "ENCRYPTED_KL_DATA=="

    def test_case_insensitive(self):
        sid, data = self.index.resolve("kl sentral")
        assert sid == "19100"

    def test_partial_match(self):
        sid, data = self.index.resolve("IPOH")
        assert sid == "9000"

    def test_unknown_station_raises(self):
        with pytest.raises(KeyError):
            self.index.resolve("NONEXISTENT STATION")


# ── parse_trip_html ───────────────────────────────────────────────────────────

class TestParseTripHtml:
    def test_returns_correct_count(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        assert len(trips) == 3

    def test_first_trip_service(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        assert trips[0].train_service == "Gold - 9044"
        assert trips[0].train_no == "9044"
        assert trips[0].train_class == "Gold"

    def test_first_trip_times(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        assert trips[0].depart_time == "08:55"
        assert trips[0].arrive_time == "11:29"

    def test_first_trip_seats(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        assert trips[0].available_seats == 253
        assert trips[0].is_available is True

    def test_first_trip_fare(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        assert trips[0].min_fare == 52.0

    def test_sold_out_trip_is_not_available(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        sold_out = [t for t in trips if t.available_seats == 0]
        assert len(sold_out) == 1
        assert sold_out[0].is_available is False

    def test_available_trips_only(self):
        trips = parse_trip_html(SAMPLE_TRIP_HTML)
        available = [t for t in trips if t.is_available]
        assert len(available) == 2

    def test_empty_html_returns_empty_list(self):
        trips = parse_trip_html("<html></html>")
        assert trips == []

    def test_no_tbody_returns_empty_list(self):
        html = "<table><tbody class='other-class'><tr><td>X</td></tr></tbody></table>"
        trips = parse_trip_html(html)
        assert trips == []


# ── extract_hidden_fields ─────────────────────────────────────────────────────

class TestExtractHiddenFields:
    def test_extracts_search_data(self):
        fields = extract_hidden_fields(SAMPLE_TRIP_RESPONSE_HTML)
        assert fields["SearchData"] == "SEARCH_DATA_BLOB="

    def test_extracts_form_validation_code(self):
        fields = extract_hidden_fields(SAMPLE_TRIP_RESPONSE_HTML)
        assert fields["FormValidationCode"] == "FORM_VAL_CODE_BLOB="

    def test_extracts_csrf_token(self):
        fields = extract_hidden_fields(SAMPLE_TRIP_RESPONSE_HTML)
        assert fields["__RequestVerificationToken"] == "NEW_CSRF_TOKEN"

    def test_missing_fields_return_empty_string(self):
        fields = extract_hidden_fields("<html></html>")
        assert fields["SearchData"] == ""
        assert fields["FormValidationCode"] == ""
        assert fields["__RequestVerificationToken"] == ""


# ── extract_csrf ──────────────────────────────────────────────────────────────

class TestExtractCsrf:
    def test_extracts_token(self):
        assert extract_csrf(SAMPLE_HOMEPAGE_HTML) == "TEST_CSRF_TOKEN"

    def test_missing_token_returns_empty(self):
        assert extract_csrf("<html></html>") == ""


# ── ParsedTrip.from_row ───────────────────────────────────────────────────────

class TestParsedTripFromRow:
    def test_returns_none_for_short_row(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<tr><td>A</td><td>B</td></tr>", "html.parser")
        cells = soup.find_all("td")
        assert ParsedTrip.from_row(cells) is None
