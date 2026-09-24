import pytest
from ktm_sniper.storage import TaskRepository
from ktm_sniper.models import SniperTaskConfig, Passenger, TaskStatus

def test_sqlite_task_lifecycle(tmp_path):
    db_path = tmp_path / "test_sniper.db"
    repo = TaskRepository(str(db_path))

    passenger = Passenger(
        name="Ahmad Faiz",
        id_number="920101105555",
        gender="Male",
        phone="0192233445"
    )
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        time_from="08:00",
        time_to="12:00",
        preferred_trains=["EG9022"],
        preferred_classes=["ETS Gold"],
        passengers=[passenger]
    )

    # Save task
    task_id = repo.save_task(config)
    assert task_id is not None

    # Retrieve task
    retrieved = repo.get_task(task_id)
    assert retrieved is not None
    assert retrieved.origin == "KL Sentral"
    assert retrieved.destination == "Butterworth"
    assert retrieved.date == "2026-09-20"
    assert retrieved.preferred_trains == ["EG9022"]
    assert retrieved.passengers[0].name == "Ahmad Faiz"
    assert retrieved.status == TaskStatus.PENDING

    # List active tasks
    active = repo.list_active_tasks()
    assert len(active) == 1
    assert active[0].task_id == task_id

    # Update status to RESERVED
    repo.update_task_status(task_id, TaskStatus.RESERVED, booking_id="KITS-123456")
    updated = repo.get_task(task_id)
    assert updated.status == TaskStatus.RESERVED

    # Active tasks should now be empty
    assert len(repo.list_active_tasks()) == 0

def test_task_logging(tmp_path):
    db_path = tmp_path / "test_sniper.db"
    repo = TaskRepository(str(db_path))
    repo.log_event("task-001", "Checking train availability", level="INFO")
    logs = repo.get_logs("task-001")
    assert len(logs) == 1
    assert logs[0]["message"] == "Checking train availability"

def test_sqlite_round_trip_task(tmp_path):
    db_path = tmp_path / "test_sniper_rt.db"
    repo = TaskRepository(str(db_path))

    rt_config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2026-09-20",
        is_round_trip=True,
        return_date="2026-09-22",
        return_time_from="14:00",
        return_time_to="21:00",
        passengers=[Passenger(name="Siti", id_number="950202105566", gender="Female", phone="0123456789")]
    )

    task_id = repo.save_task(rt_config)
    retrieved = repo.get_task(task_id)

    assert retrieved is not None
    assert retrieved.is_round_trip is True
    assert retrieved.return_date == "2026-09-22"
    assert retrieved.return_time_from == "14:00"
    assert retrieved.return_time_to == "21:00"
    assert retrieved.leg_type == "OUTBOUND"

    return_task = retrieved.get_return_task()
    assert return_task.origin == "Butterworth"
    assert return_task.destination == "KL Sentral"
    assert return_task.date == "2026-09-22"
    assert return_task.leg_type == "RETURN"

def test_sqlite_masked_passengers_column_and_no_raw_pii_leak(tmp_path):
    import sqlite3
    db_path = tmp_path / "test_secure.db"
    repo = TaskRepository(str(db_path))

    p = Passenger(name="Tan Ah Kow", id_number="900101-14-5566", gender="Male", phone="0123456789")
    config = SniperTaskConfig(
        origin="KL Sentral",
        destination="Butterworth",
        date="2027-02-04",
        passengers=[p]
    )
    task_id = repo.save_task(config)

    # Inspect raw SQLite database directly
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT passengers, passengers_masked FROM tasks WHERE task_id = ?", (task_id,))
    row = cursor.fetchone()
    conn.close()

    raw_encrypted_col = row[0]
    raw_masked_col = row[1]

    # Encrypted column must NOT contain plaintext IC or phone
    assert "900101-14-5566" not in raw_encrypted_col
    assert "0123456789" not in raw_encrypted_col

    # Masked column must contain masked values, NOT raw
    assert "900101-14-****" in raw_masked_col
    assert "0123****789" in raw_masked_col
    assert "900101-14-5566" not in raw_masked_col
    assert "0123456789" not in raw_masked_col

def test_sqlite_log_event_masks_pii(tmp_path):
    db_path = tmp_path / "test_logs.db"
    repo = TaskRepository(str(db_path))

    repo.log_event("task-1", "Processed passenger 920101-14-5566 with contact 0192233445", level="INFO")
    logs = repo.get_logs("task-1")
    assert len(logs) == 1
    assert "920101-14-5566" not in logs[0]["message"]
    assert "0192233445" not in logs[0]["message"]
    assert "920101-14-****" in logs[0]["message"]
    assert "0192****445" in logs[0]["message"]

def test_sqlite_bot_interactions_logging(tmp_path):
    db_path = tmp_path / "test_bot.db"
    repo = TaskRepository(str(db_path))

    repo.log_bot_interaction(
        chat_id="1682009086",
        command="/add_passenger",
        raw_text='/add_passenger "Siti" 950202-10-5566 0198765432 Female',
        response='✓ 已成功添加乘车人: Siti (950202-10-****)',
        status="SUCCESS"
    )

    records = repo.get_bot_interactions(chat_id="1682009086")
    assert len(records) == 1
    assert records[0]["command"] == "/add_passenger"
    assert "950202-10-5566" not in records[0]["raw_text_masked"]
    assert "0198765432" not in records[0]["raw_text_masked"]
    assert "950202-10-****" in records[0]["raw_text_masked"]
    assert "0198****432" in records[0]["raw_text_masked"]

def test_sqlite_env_db_path(tmp_path, monkeypatch):
    nested_db = tmp_path / "sub" / "folder" / "custom.db"
    monkeypatch.setenv("KTM_DB_PATH", str(nested_db))

    repo = TaskRepository()
    assert repo.db_path == str(nested_db)
    assert nested_db.parent.exists()


def test_sqlite_get_latest_task(tmp_path):
    db_path = tmp_path / "test_latest.db"
    repo = TaskRepository(str(db_path))

    # Empty initially
    assert repo.get_latest_task() is None

    p1 = Passenger(name="First User", id_number="900101015555", gender="Male", phone="0111111111")
    t1 = SniperTaskConfig(
        origin="KL Sentral", destination="Ipoh", date="2026-10-01",
        time_from="08:00", time_to="12:00", passengers=[p1]
    )
    repo.save_task(t1)

    import time
    time.sleep(0.01)

    p2 = Passenger(name="Second User", id_number="920202026666", gender="Female", phone="0122222222")
    t2 = SniperTaskConfig(
        origin="Butterworth", destination="KL Sentral", date="2026-10-05",
        time_from="14:00", time_to="18:00", passengers=[p2]
    )
    repo.save_task(t2)

    latest = repo.get_latest_task()
    assert latest is not None
    assert latest.origin == "Butterworth"
    assert latest.destination == "KL Sentral"
    assert latest.date == "2026-10-05"
    assert len(latest.passengers) == 1
    assert latest.passengers[0].name == "Second User"
    assert latest.passengers[0].id_number == "920202026666"


def test_sqlite_saved_passengers_crud(tmp_path):
    db_path = tmp_path / "test_passengers.db"
    repo = TaskRepository(str(db_path))

    assert len(repo.get_saved_passengers()) == 0

    p = Passenger(name="TAN JIA HUI", id_number="960217075045", gender="Male", phone="0123456789")
    repo.save_passenger(p)

    saved = repo.get_saved_passengers()
    assert len(saved) == 1
    assert saved[0].name == "TAN JIA HUI"
    assert saved[0].id_number == "960217075045"
    assert saved[0].gender == "Male"
    assert saved[0].phone == "0123456789"

    # Update same passenger
    p_updated = Passenger(name="TAN JIA HUI", id_number="960217075045", gender="Male", phone="0199999999")
    repo.save_passenger(p_updated)
    saved_after = repo.get_saved_passengers()
    assert len(saved_after) == 1
    assert saved_after[0].phone == "0199999999"

    # Clear passengers
    repo.clear_saved_passengers()
    assert len(repo.get_saved_passengers()) == 0


