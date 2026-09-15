import pytest
from ktm_sniper.models import (
    Passenger,
    SniperTaskConfig,
    TripInfo,
    TaskStatus
)

def test_passenger_creation_and_dict():
    p = Passenger(
        name="Ali bin Ahmad",
        id_number="900101-14-5566",
        gender="Male",
        phone="+60123456789",
        email="ali@example.com"
    )
    assert p.name == "Ali bin Ahmad"
    assert p.clean_id() == "900101145566"
    assert p.to_dict()["id_number"] == "900101-14-5566"

def test_trip_info_matches_filter_criteria():
    trip_morning = TripInfo(
        train_no="EG9022",
        train_class="ETS Gold",
        origin="KL Sentral",
        destination="Butterworth",
        departure_time="08:30",
        arrival_time="12:45",
        available_seats=5,
        fare=59.0
    )
    trip_night = TripInfo(
        train_no="EP9204",
        train_class="ETS Platinum",
        origin="KL Sentral",
        destination="Butterworth",
        departure_time="20:00",
        arrival_time="23:55",
        available_seats=2,
        fare=79.0
    )

    # Filter: Morning only (08:00 - 12:00)
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        time_from="08:00",
        time_to="12:00",
        required_seats=1
    )
    assert config.matches_trip(trip_morning) is True
    assert config.matches_trip(trip_night) is False

    # Filter: Specific train number
    config_train = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        preferred_trains=["EP9204"],
        required_seats=1
    )
    assert config_train.matches_trip(trip_morning) is False
    assert config_train.matches_trip(trip_night) is True

    # Filter: Train class preference
    config_class = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        preferred_classes=["ETS Platinum"],
        required_seats=1
    )
    assert config_class.matches_trip(trip_morning) is False
    assert config_class.matches_trip(trip_night) is True

    # Filter: Seat capacity
    config_seats = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        required_seats=4
    )
    assert config_seats.matches_trip(trip_morning) is True
    assert config_seats.matches_trip(trip_night) is False  # Only 2 seats available

def test_sniper_task_config_validation():
    with pytest.raises(ValueError, match="Origin and destination cannot be identical"):
        SniperTaskConfig(origin="KL Sentral", destination="KL Sentral", date="2026-09-20")

    with pytest.raises(ValueError, match="Invalid date format"):
        SniperTaskConfig(origin="KL Sentral", destination="Butterworth", date="20-09-2026")

def test_date_auto_padding_and_normalization():
    # User types 2027-02-4
    c1 = SniperTaskConfig(origin="KL Sentral", destination="Butterworth", date="2027-02-4")
    assert c1.date == "2027-02-04"

    # User types 2027-2-4
    c2 = SniperTaskConfig(origin="KL Sentral", destination="Butterworth", date="2027-2-4")
    assert c2.date == "2027-02-04"

    # User types with slash 2027/02/04
    c3 = SniperTaskConfig(origin="KL Sentral", destination="Butterworth", date="2027/2/4")
    assert c3.date == "2027-02-04"

def test_round_trip_configuration():
    # Valid round trip
    rt_config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        is_round_trip=True,
        return_date="2026-09-22",
        return_time_from="14:00",
        return_time_to="20:00"
    )
    assert rt_config.is_round_trip is True
    assert rt_config.return_date == "2026-09-22"
    assert rt_config.leg_type == "OUTBOUND"

    # Spawn return leg task
    return_task = rt_config.get_return_task()
    assert return_task.origin == "Butterworth"
    assert return_task.destination == "KL Sentral"
    assert return_task.date == "2026-09-22"
    assert return_task.time_from == "14:00"
    assert return_task.time_to == "20:00"
    assert return_task.is_round_trip is False
    assert return_task.leg_type == "RETURN"

    # Return date earlier than outbound date should raise ValueError
    with pytest.raises(ValueError, match="Return date .* cannot be earlier"):
        SniperTaskConfig(
            origin="KL Sentral",
            destination="Butterworth",
            date="2026-09-20",
            is_round_trip=True,
            return_date="2026-09-18"
        )
