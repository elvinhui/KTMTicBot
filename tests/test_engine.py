import pytest
from unittest.mock import MagicMock
from ktm_sniper.engine import KTMSniperEngine
from ktm_sniper.models import SniperTaskConfig, Passenger, TripInfo, TaskStatus

def test_engine_single_step_no_tickets():
    checker = MagicMock()
    checker.find_matching_trips.return_value = []

    reserver = MagicMock()
    notifier = MagicMock()

    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        passengers=[Passenger(name="Ali", id_number="900101145566", gender="Male", phone="0123456789")]
    )

    engine = KTMSniperEngine(task=config, checker=checker, reserver=reserver, notifier=notifier)
    result = engine.step()

    assert result is None
    reserver.reserve_seat.assert_not_called()
    notifier.send_alert.assert_not_called()

def test_engine_single_step_ticket_found_and_reserved():
    matching_trip = TripInfo(
        train_no="EG9022",
        train_class="ETS Gold",
        origin="KL Sentral",
        destination="Butterworth",
        departure_time="08:30",
        arrival_time="12:45",
        available_seats=5,
        fare=59.0,
        trip_id="TRIP-EG9022"
    )

    checker = MagicMock()
    checker.find_matching_trips.return_value = [matching_trip]

    reserver = MagicMock()
    reserver.reserve_seat.return_value = {
        "status": "SUCCESS",
        "booking_id": "KITS-778899",
        "timeout_minutes": 15
    }

    notifier = MagicMock()
    notifier.send_alert.return_value = True

    repo = MagicMock()

    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        passengers=[Passenger(name="Ali", id_number="900101145566", gender="Male", phone="0123456789")]
    )

    engine = KTMSniperEngine(
        task=config,
        checker=checker,
        reserver=reserver,
        notifier=notifier,
        repository=repo
    )

    result = engine.step()

    assert result is not None
    assert result["booking_id"] == "KITS-778899"
    reserver.reserve_seat.assert_called_once()
    notifier.send_alert.assert_called_once()
    repo.update_task_status.assert_called_with(config.task_id, TaskStatus.RESERVED, booking_id="KITS-778899")

def test_engine_run_loop_stops_on_success():
    matching_trip = TripInfo(
        train_no="EG9022",
        train_class="ETS Gold",
        origin="KL Sentral",
        destination="Butterworth",
        departure_time="08:30",
        arrival_time="12:45",
        available_seats=5,
        fare=59.0,
        trip_id="TRIP-EG9022"
    )

    checker = MagicMock()
    checker.find_matching_trips.return_value = [matching_trip]

    reserver = MagicMock()
    reserver.reserve_seat.return_value = {"status": "SUCCESS", "booking_id": "KITS-778899"}

    notifier = MagicMock()
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        passengers=[Passenger(name="Ali", id_number="900101145566", gender="Male", phone="0123456789")]
    )

    engine = KTMSniperEngine(task=config, checker=checker, reserver=reserver, notifier=notifier, max_cycles=3)
    final_res = engine.run()
    assert final_res is not None
    assert final_res["booking_id"] == "KITS-778899"

def test_engine_round_trip_orchestration():
    outbound_trip = TripInfo(
        train_no="EG9022",
        train_class="ETS Gold",
        origin="KL Sentral",
        destination="Butterworth",
        departure_time="08:30",
        arrival_time="12:45",
        available_seats=5,
        fare=59.0,
        trip_id="TRIP-OUTBOUND"
    )
    return_trip = TripInfo(
        train_no="EG9023",
        train_class="ETS Gold",
        origin="Butterworth",
        destination="KL Sentral",
        departure_time="16:00",
        arrival_time="20:15",
        available_seats=4,
        fare=59.0,
        trip_id="TRIP-RETURN"
    )

    checker = MagicMock()
    # First call returns outbound trip, second call returns return trip
    checker.find_matching_trips.side_effect = [[outbound_trip], [return_trip]]

    reserver = MagicMock()
    reserver.reserve_seat.side_effect = [
        {"status": "SUCCESS", "booking_id": "KITS-OUTBOUND-111"},
        {"status": "SUCCESS", "booking_id": "KITS-RETURN-222"}
    ]

    notifier = MagicMock()
    repo = MagicMock()

    rt_config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        is_round_trip=True,
        return_date="2026-09-22",
        passengers=[Passenger(name="Ali", id_number="900101145566", gender="Male", phone="0123456789")]
    )

    engine = KTMSniperEngine(
        task=rt_config,
        checker=checker,
        reserver=reserver,
        notifier=notifier,
        repository=repo,
        max_cycles=5
    )

    final_res = engine.run()
    assert final_res is not None
    assert final_res["is_round_trip"] is True
    assert final_res["outbound"]["booking_id"] == "KITS-OUTBOUND-111"
    assert final_res["return"]["booking_id"] == "KITS-RETURN-222"
    assert reserver.reserve_seat.call_count == 2
    assert notifier.send_alert.call_count == 2

