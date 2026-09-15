import pytest
from unittest.mock import patch
from main import prompt_station, run_wizard

def test_prompt_station_exact():
    with patch("builtins.input", side_effect=["KL Sentral"]):
        station = prompt_station("出发地")
        assert station == "KL SENTRAL"

def test_prompt_station_alias():
    with patch("builtins.input", side_effect=["BM"]):
        station = prompt_station("出发地")
        assert station == "BUKIT MERTAJAM"

def test_prompt_station_popular_hub_menu():
    # User types '?' and selects '2' (Butterworth)
    with patch("builtins.input", side_effect=["?", "2"]):
        station = prompt_station("目的地")
        assert station == "BUTTERWORTH"

def test_prompt_station_multiple_matches_picker():
    # User types 'sentral', gets menu [1] KL SENTRAL, [2] JB SENTRAL, [3] KEPONG SENTRAL, chooses '2'
    with patch("builtins.input", side_effect=["sentral", "2"]):
        station = prompt_station("目的地")
        assert station == "JB SENTRAL"

def test_prompt_station_rejects_duplicate_origin_dest():
    # User chooses Butterworth for dest, but origin was already Butterworth, then changes to Ipoh
    with patch("builtins.input", side_effect=["Butterworth", "Ipoh"]):
        station = prompt_station("目的地", exclude_station="BUTTERWORTH")
        assert station == "IPOH"

def test_run_wizard_round_trip():
    inputs = [
        "KLS",
        "Butterworth",
        "2027-02-04",
        "08:00",
        "12:00",
        "y",
        "2027-02-08",
        "14:00",
        "20:00",
        "",
        "",
        "Window",
        "Tan Ah Kow",
        "Male",
        "0123456789",
        "n"
    ]
    with patch("builtins.input", side_effect=inputs):
        with patch("getpass.getpass", return_value="900101-01-5555"):
            config = run_wizard()
            assert config.origin == "KL SENTRAL"
            assert config.destination == "BUTTERWORTH"
            assert config.date == "2027-02-04"
            assert config.time_from == "08:00"
            assert config.time_to == "12:00"
            assert config.is_round_trip is True
            assert config.return_date == "2027-02-08"
            assert config.return_time_from == "14:00"
            assert config.return_time_to == "20:00"
            assert config.leg_type == "OUTBOUND"
            assert len(config.passengers) == 1
            assert config.passengers[0].name == "Tan Ah Kow"

def test_run_wizard_multi_passenger():
    inputs = [
        "KL Sentral",
        "Butterworth",
        "2027-02-04",
        "08:00",
        "12:00",
        "n",  # no round trip
        "",   # any train
        "",   # any class
        "Window",
        # Passenger 1
        "Tan Ah Kow",
        "Male",
        "0123456789",
        "y",  # add next passenger
        # Passenger 2
        "Lee Mei",
        "Female",
        "0198765432",
        "n"   # stop adding passengers
    ]
    with patch("builtins.input", side_effect=inputs):
        with patch("getpass.getpass", side_effect=["900101-01-5555", "920202-02-6666"]):
            config = run_wizard()
            assert len(config.passengers) == 2
            assert config.passengers[0].name == "Tan Ah Kow"
            assert config.passengers[1].name == "Lee Mei"
            assert config.required_seats == 2
