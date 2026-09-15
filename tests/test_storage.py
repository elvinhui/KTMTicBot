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

