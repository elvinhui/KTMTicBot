import pytest
from unittest.mock import patch, MagicMock
from ktm_sniper.storage import TaskRepository
from ktm_sniper.models import SniperTaskConfig, Passenger, TaskStatus
import main


def test_sqlite_task_loader_prefers_sqlite_over_env_and_json(tmp_path, monkeypatch):
    # Set up custom SQLite db
    db_file = tmp_path / "test_loader.db"
    monkeypatch.setenv("KTM_DB_PATH", str(db_file))
    
    # Pre-seed SQLite with user's specific journey and passenger
    repo = TaskRepository(str(db_file))
    passenger = Passenger(
        name="TAN JIA HUI",
        id_number="960217075045",
        gender="Male",
        phone="0123456789"
    )
    task = SniperTaskConfig(
        origin="KL Sentral",
        destination="Ipoh",
        date="2026-10-05",
        time_from="08:00",
        time_to="20:00",
        passengers=[passenger]
    )
    repo.save_task(task)

    # Ensure no environment variable passenger leaks
    monkeypatch.delenv("KTM_PASSENGER_NAME", raising=False)
    monkeypatch.delenv("KTM_PASSENGER_IC", raising=False)
    monkeypatch.delenv("KTM_PASSENGER_PHONE", raising=False)
    monkeypatch.delenv("KTM_PASSENGER_GENDER", raising=False)

    # Test main() execution with empty CLI args
    monkeypatch.setattr("sys.argv", ["main.py", "--no-browser", "--max-cycles", "1"])

    with patch("main.KTMSniperEngine") as MockEngine:
        mock_instance = MagicMock()
        mock_instance.task = task
        mock_instance.run_sniper_loop.return_value = None
        MockEngine.return_value = mock_instance

        main.main()

        # Verify engine was initialized with the exact config from SQLite
        assert MockEngine.called
        call_kwargs = MockEngine.call_args[1]
        loaded_task = call_kwargs["task"]

        assert loaded_task.origin == "KL Sentral"
        assert loaded_task.destination == "Ipoh"
        assert loaded_task.date == "2026-10-05"
        assert loaded_task.time_from == "08:00"
        assert loaded_task.time_to == "20:00"
        assert len(loaded_task.passengers) == 1
        assert loaded_task.passengers[0].name == "TAN JIA HUI"
        assert loaded_task.passengers[0].id_number == "960217075045"
        assert loaded_task.passengers[0].gender == "Male"
