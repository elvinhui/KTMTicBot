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

def test_command_handler_add_passenger_unquoted_with_spaces(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    res = handler.handle_message(chat_id="1682009086", text='/add_passengers TAN JIA HUI 960217075045 MALE')
    assert res is not None
    assert "TAN JIA HUI" in res
    p = sample_engine.task.passengers[-1]
    assert p.name == "TAN JIA HUI"
    assert p.id_number == "960217075045"
    assert p.gender == "Male"


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
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_session.post.return_value = mock_post_resp

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


def test_command_handler_seat_avoids_book_405(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    sample_engine.selected_trip = {"train_no": "9124", "train_class": "ETS Gold", "departure_time": "08:05"}
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "KITS-1790307949",
        "payment_url": "https://online.ktmb.com.my/Book"  # Dangerous 405 URL
    })

    res = handler.handle_message(chat_id="1682009086", text="/seat 11A")
    assert "https://online.ktmb.com.my/Book)" not in res
    assert "UpcomingList" in res
    assert "KTMB Mobile App" in res


def test_command_handler_logout(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    mock_auth = MagicMock()
    mock_driver = MagicMock()
    mock_driver.page = MagicMock()
    sample_engine.authenticator = mock_auth
    sample_engine.browser_driver = mock_driver

    res = handler.handle_message(chat_id="1682009086", text="/logout")
    assert "已成功释放 KTMB 官方登录锁" in res
    mock_auth.logout.assert_called_once_with(mock_driver.page)


def test_command_handler_seat_with_qr_and_autologout(sample_engine, tmp_path):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    qr_file = tmp_path / "test_qr.png"
    qr_file.write_text("fake image content")

    mock_auth = MagicMock()
    mock_driver = MagicMock()
    mock_driver.page = MagicMock()
    sample_engine.authenticator = mock_auth
    sample_engine.browser_driver = mock_driver
    sample_engine.notifier = MagicMock()

    sample_engine.selected_trip = {"train_no": "9124", "train_class": "Platinum", "departure_time": "08:05"}
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "KITS-1790309999",
        "payment_url": "https://online.ktmb.com.my/Booking/UpcomingList",
        "payment_qr": str(qr_file)
    })

    res = handler.handle_message(chat_id="1682009086", text="/seat 10B")
    assert "10B" in res
    assert "DuitNow" in res
    sample_engine.notifier.send_photo_alert.assert_called_once()
    mock_auth.logout.assert_called_once_with(mock_driver.page)


def test_command_handler_seat_auto_shows_actual_selected_seat(sample_engine, tmp_path):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    qr_file = tmp_path / "test_auto_qr.png"
    qr_file.write_text("fake qr image")

    sample_engine.notifier = MagicMock()
    sample_engine.selected_trip = {"train_no": "9326", "train_class": "Platinum", "departure_time": "09:55"}
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "KITS-1791641423",
        "selected_seat": "Coach A - 6B",
        "payment_url": "https://online.ktmb.com.my/Booking/UpcomingList",
        "payment_qr": str(qr_file)
    })

    res = handler.handle_message(chat_id="1682009086", text="/seat auto")
    assert "Coach A - 6B (AUTO)" in res
    sample_engine.notifier.send_photo_alert.assert_called_once()
    qr_caption = sample_engine.notifier.send_photo_alert.call_args.kwargs.get("caption", "")
    assert "Coach A - 6B (AUTO)" in qr_caption


def test_command_handler_seat_auto_multi_passengers_selects_by_count(sample_engine, tmp_path):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    qr_file = tmp_path / "test_multi_auto_qr.png"
    qr_file.write_text("fake qr image")

    # Configure 3 passengers
    handler.handle_message(chat_id="1682009086", text="/clear_passengers")
    handler.handle_message(chat_id="1682009086", text="/add_passenger TAN JIA HUI 960217075045 MALE")
    handler.handle_message(chat_id="1682009086", text="/add_passenger TAN AH GOH 670328085437 MALE")
    handler.handle_message(chat_id="1682009086", text="/add_passenger TAN SIEW CHENG 651219085618 FEMALE")
    assert len(sample_engine.task.passengers) == 3
    assert sample_engine.task.required_seats == 3

    sample_engine.notifier = MagicMock()
    sample_engine.selected_trip = {"train_no": "9326", "train_class": "Platinum", "departure_time": "09:55"}
    sample_engine.available_seats_cache = {
        "B": {
            "window": ["03A", "03D", "04A", "04D"],
            "aisle": ["03B", "03C", "04B", "04C"]
        }
    }
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "KITS-1791649999",
        "payment_url": "https://online.ktmb.com.my/Booking/UpcomingList",
        "payment_qr": str(qr_file)
    })

    res = handler.handle_message(chat_id="1682009086", text="/seat auto")
    # Should automatically pick 3 adjacent seats in Row 03 of Coach B for the 3 passengers
    assert "Coach B - 03A, Coach B - 03B, Coach B - 03C (AUTO)" in res
    assert "TAN JIA HUI, TAN AH GOH, TAN SIEW CHENG (共 3 人)" in res
    sample_engine.notifier.send_photo_alert.assert_called_once()
    qr_caption = sample_engine.notifier.send_photo_alert.call_args.kwargs.get("caption", "")
    assert "Coach B - 03A, Coach B - 03B, Coach B - 03C (AUTO)" in qr_caption
    assert "TAN JIA HUI, TAN AH GOH, TAN SIEW CHENG (共 3 人)" in qr_caption


def test_command_handler_seat_with_return_date(sample_engine, tmp_path):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086")
    qr_file = tmp_path / "test_round_trip_qr.png"
    qr_file.write_text("fake qr image")

    # Configure return_date via /return (must be >= sample_engine.task.date 2027-02-04)
    ret_reply = handler.handle_message(chat_id="1682009086", text="/return 2027-02-08")
    assert "出发日期: *2027-02-04*" in ret_reply
    assert "返程日期: *2027-02-08*" in ret_reply
    assert "返程时段:" in ret_reply
    assert "返程车次:" in ret_reply
    assert "返程座位:" in ret_reply
    assert sample_engine.task.is_round_trip is True
    assert sample_engine.task.return_date == "2027-02-08"

    status_reply = handler.handle_message(chat_id="1682009086", text="/status")
    assert "• *出发日期*: `2027-02-04`" in status_reply
    assert "• *返程日期*: `2027-02-08`" in status_reply
    assert "• *返程时段*:" in status_reply
    assert "• *返程车次*:" in status_reply
    assert "• *返程座位*:" in status_reply

    sample_engine.notifier = MagicMock()
    sample_engine.selected_trip = {"train_no": "9124", "train_class": "Platinum", "departure_time": "08:05"}
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "KITS-RT-8888",
        "selected_seat": "Coach B - 03A (返程: Coach C - 05A)",
        "return_train_no": "9041",
        "return_train_class": "Gold",
        "return_departure_time": "05:00",
        "return_seat": "Coach C - 05A",
        "payment_url": "https://online.ktmb.com.my/Booking/UpcomingList",
        "payment_qr": str(qr_file)
    })

    res = handler.handle_message(chat_id="1682009086", text="/seat auto")
    assert "Coach B - 03A (返程: Coach C - 05A) (AUTO)" in res
    assert "• *出发日期*: `2027-02-04`" in res
    assert "• *返程日期*: `2027-02-08` (往返双程)" in res
    assert "• *返程时间*: `05:00` (AUTO)" in res
    assert "• *返程车次*: *9041* (Gold) (AUTO)" in res
    assert "• *返程座位*: `Coach C - 05A (AUTO)`" in res
    sample_engine.notifier.send_photo_alert.assert_called_once()
    qr_caption = sample_engine.notifier.send_photo_alert.call_args.kwargs.get("caption", "")
    assert "Coach B - 03A (返程: Coach C - 05A) (AUTO)" in qr_caption
    assert "• *出发日期*: `2027-02-04`" in qr_caption
    assert "• *返程日期*: `2027-02-08` (往返双程)" in qr_caption
    assert "• *返程时间*: `05:00` (AUTO)" in qr_caption
    assert "• *返程车次*: *9041* (Gold) (AUTO)" in qr_caption
    assert "• *返程座位*: `Coach C - 05A (AUTO)`" in qr_caption
    handler._cancel_payment_expiry_timer()


def test_command_handler_verify_and_expiry_timer(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086", expiry_timeout_seconds=0)
    sample_engine.notifier = MagicMock()
    sample_engine.logout = MagicMock(return_value=True)

    # 1. PENDING_PAYMENT booking result shows honest status + payment_amount + payment_ref
    sample_engine.selected_trip = {"train_no": "9326", "train_class": "Platinum", "departure_time": "09:55"}
    sample_engine.execute_real_booking = MagicMock(return_value={
        "status": "SUCCESS",
        "booking_id": "PENDING_PAYMENT",
        "payment_ref": "FIUU-E10B0A00E041",
        "payment_amount": "497.00",
        "selected_seat": "Coach B - 2C, Coach B - 2D",
        "payment_url": "https://pay.fiuu.com/RMS/intermediate_app/viewqr.php",
    })
    res_seat = handler.handle_message(chat_id="1682009086", text="/seat auto")
    assert "待付款后生成" in res_seat
    assert "FIUU-E10B0A00E041" in res_seat
    assert "RM 497.00" in res_seat
    assert "/verify" in res_seat

    # 2. /verify when UpcomingList has no tickets
    sample_engine.verify_upcoming_bookings = MagicMock(return_value=[])
    res_empty = handler.handle_message(chat_id="1682009086", text="/verify")
    assert "暂未查到已出票记录" in res_empty
    assert "没有买到票" in res_empty

    # 3. /verify when UpcomingList has confirmed ticket
    sample_engine.verify_upcoming_bookings = MagicMock(return_value=[
        {
            "booking_no": "K261049574760",
            "route": "JB SENTRAL BUKIT MERTAJAM",
            "depart_date": "20 Oct 2026",
            "return_date": "27 Oct 2026",
            "print_url": "https://online.ktmb.com.my/Booking/PrintTicket?p=K261049574760",
            "legs": [
                {
                    "route": "JB SENTRAL BUKIT MERTAJAM",
                    "train": "Platinum - 9326",
                    "datetime": "20 Oct 2026 / 09:55",
                    "tickets": [
                        {"seat": "B / 2C", "ticket_no": "T261013679970", "passenger": "TAN JIA HUI 960217075045"}
                    ],
                }
            ],
        }
    ])
    res_paid = handler.handle_message(chat_id="1682009086", text="/verify")
    assert "已真实付款出票" in res_paid
    assert "K261049574760" in res_paid
    assert "T261013679970" in res_paid
    assert "B / 2C" in res_paid

    # 4. 15-min expiry check when unpaid sends timeout alert
    sample_engine.verify_upcoming_bookings = MagicMock(return_value=[])
    handler._on_payment_expiry_check({"train_no": "9326", "dep_date": "2026-10-20", "seat_display": "Coach B - 2C"})
    sample_engine.notifier.send_raw_message.assert_called_once()
    expiry_text = sample_engine.notifier.send_raw_message.call_args.args[0]
    assert "15 分钟锁座超时提醒" in expiry_text
    assert "自动释放" in expiry_text


def test_command_handler_return_time_point_selection(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086", expiry_timeout_seconds=0)
    return_trips = [
        {"train_no": "9323", "train_class": "Platinum", "departure_time": "07:11", "arrival_time": "15:35", "available_seats": 270, "fare": 139.0},
        {"train_no": "9329", "train_class": "Platinum", "departure_time": "16:10", "arrival_time": "23:55", "available_seats": 120, "fare": 139.0},
    ]
    sample_engine.fetch_return_trips = MagicMock(return_value=return_trips)

    # 1. /return <date> displays selectable return time points
    res_ret = handler.handle_message(chat_id="1682009086", text="/return 2027-02-08")
    assert "[R1]" in res_ret
    assert "07:11" in res_ret
    assert "[R2]" in res_ret
    assert "16:10" in res_ret

    # 2. /return_time with no args lists available return time points
    res_list = handler.handle_message(chat_id="1682009086", text="/return_time")
    assert "返程时间点与车次选择" in res_list
    assert "07:11" in res_list
    assert "9323" in res_list

    # 3. /ret_time 2 selects [R2] 16:10 (9329)
    res_sel = handler.handle_message(chat_id="1682009086", text="/ret_time 2")
    assert "已选定返程时间点与车次" in res_sel
    assert "16:10" in res_sel
    assert "9329" in res_sel
    assert sample_engine.task.return_time_from == "16:10"
    assert sample_engine.task.return_time_to == "16:10"
    assert sample_engine.task.return_preferred_trains == ["9329"]

    # 4. /return_time 07:11 selects exact time point 07:11 (9323)
    res_exact = handler.handle_message(chat_id="1682009086", text="/return_time 07:11")
    assert "07:11" in res_exact
    assert "9323" in res_exact
    assert sample_engine.task.return_time_from == "07:11"
    assert sample_engine.task.return_time_to == "07:11"
    assert sample_engine.task.return_preferred_trains == ["9323"]

    # 5. /return_time any resets back to full-day auto
    res_any = handler.handle_message(chat_id="1682009086", text="/return_time any")
    assert "00:00 - 23:59" in res_any
    assert sample_engine.task.return_time_from == "00:00"
    assert sample_engine.task.return_time_to == "23:59"
    assert sample_engine.task.return_preferred_trains == []


def test_command_handler_insurance_toggle(sample_engine):
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086", expiry_timeout_seconds=0)
    assert sample_engine.task.include_insurance is False

    # 1. Query default insurance status
    res_info = handler.handle_message(chat_id="1682009086", text="/insurance")
    assert "KTMB Takaful 行程保险设置" in res_info
    assert "默认不购买" in res_info

    # 2. Enable insurance via /insurance yes
    res_yes = handler.handle_message(chat_id="1682009086", text="/insurance yes")
    assert "购买 Takaful 行程保险 (YES)" in res_yes
    assert sample_engine.task.include_insurance is True
    # 3. Disable insurance via /set insurance no
    res_no = handler.handle_message(chat_id="1682009086", text="/set insurance no")
    assert "不购买 Takaful 行程保险 (NO)" in res_no
    assert sample_engine.task.include_insurance is False


def test_command_handler_return_seat_selection(sample_engine):
    import dataclasses
    handler = TelegramCommandHandler(engine=sample_engine, authorized_chat_id="1682009086", expiry_timeout_seconds=0)
    # Enable round trip mode
    sample_engine.task = dataclasses.replace(sample_engine.task, return_date="2027-02-08")

    # 1. /ret_seat query or set
    res_set_ret = handler.handle_message(chat_id="1682009086", text="/ret_seat 5A")
    assert "返程座位已选定" in res_set_ret
    assert "5A" in res_set_ret
    assert sample_engine.task.return_seat_preference == "5A"
    assert sample_engine.selected_return_seat == "5A"

    # 2. /return_seat window
    res_window = handler.handle_message(chat_id="1682009086", text="/return_seat window")
    assert "WINDOW" in res_window
    assert sample_engine.task.return_seat_preference == "WINDOW"
    assert sample_engine.selected_return_seat == "WINDOW"

    # 3. /set return_seat 6B
    res_set_cmd = handler.handle_message(chat_id="1682009086", text="/set return_seat 6B")
    assert "返程座位已选定" in res_set_cmd
    assert "6B" in res_set_cmd
    assert sample_engine.task.return_seat_preference == "6B"

    # 4. Combo /seat 3A / 5B
    trip_mock = {"train_no": "9326", "train_class": "Platinum", "departure_time": "09:55"}
    sample_engine.selected_trip = trip_mock
    sample_engine.lock_real_trip = MagicMock(return_value={"status": "ok"})
    sample_engine.execute_real_booking = MagicMock(return_value={"status": "ok", "booking_id": "TEST1234"})
    res_combo = handler.handle_message(chat_id="1682009086", text="/seat 3A / 5B")
    assert "3A" in res_combo
    assert "5B" in res_combo
    assert sample_engine.selected_return_seat == "5B"
    sample_engine.execute_real_booking.assert_called_once_with(trip=trip_mock, seat_no="3A", return_seat_no="5B")

    # 5. /seat 4A ret 6A syntax
    sample_engine.selected_trip = trip_mock
    sample_engine.execute_real_booking.reset_mock()
    res_combo2 = handler.handle_message(chat_id="1682009086", text="/seat 4A ret 6A")
    assert "4A" in res_combo2
    assert "6A" in res_combo2
    sample_engine.execute_real_booking.assert_called_once_with(trip=trip_mock, seat_no="4A", return_seat_no="6A")






