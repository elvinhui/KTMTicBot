import pytest
from unittest.mock import MagicMock, patch
from ktm_sniper.models import SniperTaskConfig, Passenger
from ktm_sniper.engine import KTMSniperEngine
from ktm_sniper.telegram_bot import TelegramCommandHandler, TelegramCommandListener

@pytest.fixture
def sample_engine():
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2027-02-04",
        time_from="08:00",
        time_to="12:00",
        passengers=[Passenger(name="Tan Ah Kow", id_number="900101-14-5566", gender="Male", phone="0123456789")]
    )
    checker = MagicMock()
    checker.find_matching_trips.return_value = []
    return KTMSniperEngine(task=config, checker=checker)

def test_command_handler_unauthorized(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="999999999", text="/status")
    assert res is None  # Unauthorized messages are dropped

def test_command_handler_status(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/status")
    assert res is not None
    assert "KL Sentral" in res or "KL SENTRAL" in res
    assert "Butterworth" in res or "BUTTERWORTH" in res
    assert "2027-02-04" in res
    assert "08:00 - 12:00" in res

def test_command_handler_set_origin(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    # Change origin using abbreviation BM (Bukit Mertajam)
    res = handler.handle_message(chat_id="1682009086", text="/set origin BM")
    assert res is not None
    assert "BUKIT MERTAJAM" in res
    assert sample_engine.task.origin == "BUKIT MERTAJAM"

def test_command_handler_set_dest(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/set dest Ipoh")
    assert res is not None
    assert "IPOH" in res
    assert sample_engine.task.destination == "IPOH"

def test_command_handler_set_date(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/set date 2027-2-6")
    assert res is not None
    assert "2027-02-06" in res
    assert sample_engine.task.date == "2027-02-06"

def test_command_handler_set_time(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/set time 09:30-15:00")
    assert res is not None
    assert "09:30 - 15:00" in res
    assert sample_engine.task.time_from == "09:30"
    assert sample_engine.task.time_to == "15:00"

def test_command_handler_pause_and_resume(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res_pause = handler.handle_message(chat_id="1682009086", text="/pause")
    assert "暂停" in res_pause
    assert sample_engine.is_paused is True

    res_resume = handler.handle_message(chat_id="1682009086", text="/resume")
    assert "恢复" in res_resume
    assert sample_engine.is_paused is False

def test_command_handler_set_return_date(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/set return_date 2027-02-10")
    assert res is not None
    assert "2027-02-10" in res
    assert sample_engine.task.is_round_trip is True
    assert sample_engine.task.return_date == "2027-02-10"

def test_command_handler_add_passenger(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text='/add_passenger "Siti Nurhaliza" 950202-10-5566 0198765432 Female')
    assert res is not None
    assert "Siti Nurhaliza" in res
    assert "950202-10-****" in res
    assert len(sample_engine.task.passengers) == 2
    assert sample_engine.task.required_seats == 2
    assert sample_engine.task.passengers[1].name == "Siti Nurhaliza"
    assert sample_engine.task.passengers[1].gender == "Female"

def test_command_handler_list_passengers(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/passengers")
    assert res is not None
    assert "Tan Ah Kow" in res
    assert "900101-14-****" in res

def test_command_handler_clear_passengers(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text="/clear_passengers")
    assert res is not None
    assert "清空" in res
    assert len(sample_engine.task.passengers) == 0
    assert sample_engine.task.required_seats == 1


def test_command_listener_single_poll():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 1001,
                "message": {
                    "chat": {"id": 1682009086},
                    "text": "/status"
                }
            }
        ]
    }
    mock_session.get.return_value = mock_response

    engine = MagicMock()
    listener = TelegramCommandListener(
        bot_token="fake_token",
        authorized_chat_id="1682009086",
        engine=engine,
        session=mock_session
    )

    listener.poll_once()
    assert listener.offset == 1002
    mock_session.post.assert_called_once()

def test_command_handler_logs_to_repository(sample_engine, tmp_path):
    from ktm_sniper.storage import TaskRepository
    db_path = tmp_path / "bot_test.db"
    repo = TaskRepository(str(db_path))
    sample_engine.repository = repo

    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text='/add_passenger "Lee" 920101-14-5566 0192233445 Female')
    assert res is not None

    interactions = repo.get_bot_interactions(chat_id="1682009086")
    assert len(interactions) == 1
    record = interactions[0]
    assert record["command"] == "/add_passenger"
    # PII must NOT be in SQLite record in raw format
    assert "920101-14-5566" not in record["raw_text_masked"]
    assert "0192233445" not in record["raw_text_masked"]
    # PII must be masked
    assert "920101-14-****" in record["raw_text_masked"]
    assert "0192****445" in record["raw_text_masked"]
    assert record["status"] == "SUCCESS"


def test_command_handler_book_and_seat_flow(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")

    # 1. /book when no trips are pending
    res = handler.handle_message(chat_id="1682009086", text="/book 1")
    assert "当前没有等待确认的车次" in res

    # 2. Add pending trips and book option 1
    sample_engine.last_found_trips = [
        {"train_no": "9044", "train_class": "Gold", "departure_time": "08:55", "arrival_time": "11:20", "available_seats": 5, "fare": 42.0},
        {"train_no": "9046", "train_class": "Platinum", "departure_time": "10:15", "arrival_time": "12:35", "available_seats": 2, "fare": 56.0}
    ]
    sample_engine.fetch_seats_layout = MagicMock(return_value=[
        {"coach": "Coach B", "seats": [{"seat_no": "3A", "type": "Window", "status": "Available"}]}
    ])
    sample_engine.notifier = MagicMock()
    sample_engine.notifier.format_seat_options_message.return_value = "Seat layout for 9044 Coach B 3A"

    res_book = handler.handle_message(chat_id="1682009086", text="/book 1")
    assert "Seat layout for 9044" in res_book
    assert sample_engine.selected_trip["train_no"] == "9044"

    # 3. /seat before selecting trip shouldn't fail, but since selected_trip is set:
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "KITS-9044-CONFIRMED",
        "payment_url": "https://online.ktmb.com.my/Payment/Checkout?bookingId=KITS-9044-CONFIRMED"
    })
    res_seat = handler.handle_message(chat_id="1682009086", text="/seat 3A")
    assert "KITS-9044-CONFIRMED" in res_seat
    assert "3A" in res_seat
    assert sample_engine.selected_trip is None
    assert len(sample_engine.last_found_trips) == 0


def test_command_handler_cancel_selection(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    sample_engine.last_found_trips = [{"train_no": "9044"}]
    sample_engine.selected_trip = {"train_no": "9044"}
    sample_engine.is_paused = True

    res = handler.handle_message(chat_id="1682009086", text="/cancel")
    assert "已取消本次订座选择" in res
    assert sample_engine.selected_trip is None
    assert len(sample_engine.last_found_trips) == 0
    assert sample_engine.is_paused is False
