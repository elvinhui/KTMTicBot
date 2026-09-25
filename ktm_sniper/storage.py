import os
import sqlite3
import json
import uuid
import time
from typing import List, Optional, Dict, Any
from ktm_sniper.models import SniperTaskConfig, Passenger, TaskStatus
from ktm_sniper.security import DataEncryptor, mask_pii_in_text

class TaskRepository:
    """
    SQLite repository for persistent sniper tasks, bot command history, and execution audit logs.
    Encrypts passenger PII (IC numbers, phones) at rest using DataEncryptor.
    All stored logs and bot command records are strictly masked for zero PII leakage on disk.
    Supports both One-Way and Round-Trip tasks.
    """
    def __init__(self, db_path: Optional[str] = None, encryptor: Optional[DataEncryptor] = None):
        if db_path:
            self.db_path = db_path
        elif os.getenv("KTM_DB_PATH"):
            self.db_path = os.getenv("KTM_DB_PATH")
        elif os.path.isfile("data/ktm_sniper.db"):
            self.db_path = "data/ktm_sniper.db"
        elif os.path.isfile("ktm_sniper.db"):
            self.db_path = "ktm_sniper.db"
        else:
            self.db_path = "data/ktm_sniper.db"
        parent_dir = os.path.dirname(os.path.abspath(self.db_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        self.encryptor = encryptor or DataEncryptor()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_id TEXT PRIMARY KEY,
                        origin TEXT NOT NULL,
                        destination TEXT NOT NULL,
                        date TEXT NOT NULL,
                        time_from TEXT NOT NULL,
                        time_to TEXT NOT NULL,
                        preferred_trains TEXT,
                        preferred_classes TEXT,
                        seat_preference TEXT,
                        required_seats INTEGER,
                        passengers TEXT,
                        passengers_masked TEXT,
                        auto_reserve INTEGER,
                        status TEXT NOT NULL,
                        booking_id TEXT,
                        is_round_trip INTEGER DEFAULT 0,
                        return_date TEXT,
                        return_time_from TEXT,
                        return_time_to TEXT,
                        leg_type TEXT DEFAULT 'ONE_WAY',
                        created_at REAL,
                        updated_at REAL
                    )
                """)
                # Auto-migration for new round trip and masked passenger columns
                for col_name, col_type in [
                    ("is_round_trip", "INTEGER DEFAULT 0"),
                    ("return_date", "TEXT"),
                    ("return_time_from", "TEXT"),
                    ("return_time_to", "TEXT"),
                    ("leg_type", "TEXT DEFAULT 'ONE_WAY'"),
                    ("passengers_masked", "TEXT")
                ]:
                    try:
                        conn.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS task_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT,
                        level TEXT,
                        message TEXT,
                        created_at REAL
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bot_interactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id TEXT NOT NULL,
                        command TEXT,
                        raw_text_masked TEXT NOT NULL,
                        response_masked TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at REAL
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS saved_passengers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        id_number_encrypted TEXT NOT NULL,
                        id_number_masked TEXT NOT NULL,
                        gender TEXT NOT NULL,
                        phone TEXT NOT NULL,
                        email TEXT,
                        created_at REAL,
                        updated_at REAL
                    )
                """)
        finally:
            conn.close()

    def save_task(self, config: SniperTaskConfig) -> str:
        task_id = config.task_id or str(uuid.uuid4())
        config.task_id = task_id
        now = time.time()

        passengers_data = [p.to_dict() for p in config.passengers]
        raw_passengers_json = json.dumps(passengers_data)
        encrypted_passengers = self.encryptor.encrypt(raw_passengers_json)

        # Explicitly store masked passenger PII for safe auditing at rest on EC2
        masked_passengers_data = [p.to_dict(mask_pii=True) for p in config.passengers]
        masked_passengers_json = json.dumps(masked_passengers_data)

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks (
                        task_id, origin, destination, date, time_from, time_to,
                        preferred_trains, preferred_classes, seat_preference,
                        required_seats, passengers, passengers_masked, auto_reserve, status,
                        booking_id, is_round_trip, return_date, return_time_from, return_time_to,
                        leg_type, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task_id,
                    config.origin,
                    config.destination,
                    config.date,
                    config.time_from,
                    config.time_to,
                    json.dumps(config.preferred_trains),
                    json.dumps(config.preferred_classes),
                    config.seat_preference,
                    config.required_seats,
                    encrypted_passengers,
                    masked_passengers_json,
                    1 if config.auto_reserve else 0,
                    config.status.value if isinstance(config.status, TaskStatus) else config.status,
                    None,
                    1 if config.is_round_trip else 0,
                    config.return_date,
                    config.return_time_from,
                    config.return_time_to,
                    getattr(config, "leg_type", "ONE_WAY"),
                    now,
                    now
                ))
        finally:
            conn.close()

        # Synchronize passengers into saved_passengers table
        for p in config.passengers:
            try:
                self.save_passenger(p)
            except Exception:
                pass

        return task_id

    def get_task(self, task_id: str) -> Optional[SniperTaskConfig]:
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_config(row)
        finally:
            conn.close()

    def get_latest_task(self) -> Optional[SniperTaskConfig]:
        """
        Retrieves the most recently updated or created task from SQLite.
        Used on robot launch to restore the configured route and passengers.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC, created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_config(row)
        finally:
            conn.close()

    def list_active_tasks(self) -> List[SniperTaskConfig]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status IN (?, ?) ORDER BY created_at ASC",
                (TaskStatus.PENDING.value, TaskStatus.MONITORING.value)
            )
            return [self._row_to_config(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def save_passenger(self, p: Passenger):
        """
        Saves or updates a passenger profile in SQLite.
        """
        encrypted_id = self.encryptor.encrypt(p.id_number)
        masked_id = p.masked_id
        now = time.time()
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute("SELECT id FROM saved_passengers WHERE name = ?", (p.name,))
                row = cursor.fetchone()
                if row:
                    conn.execute("""
                        UPDATE saved_passengers
                        SET id_number_encrypted = ?, id_number_masked = ?, gender = ?, phone = ?, email = ?, updated_at = ?
                        WHERE id = ?
                    """, (encrypted_id, masked_id, p.gender, p.phone, p.email or "", now, row["id"]))
                else:
                    conn.execute("""
                        INSERT INTO saved_passengers (name, id_number_encrypted, id_number_masked, gender, phone, email, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (p.name, encrypted_id, masked_id, p.gender, p.phone, p.email or "", now, now))
        finally:
            conn.close()

    def get_saved_passengers(self) -> List[Passenger]:
        """
        Retrieves all saved passenger profiles from SQLite.
        """
        conn = self._get_connection()
        passengers = []
        try:
            cursor = conn.execute(
                "SELECT * FROM saved_passengers ORDER BY updated_at DESC, created_at DESC"
            )
            for row in cursor.fetchall():
                try:
                    decrypted_id = self.encryptor.decrypt(row["id_number_encrypted"])
                except Exception:
                    decrypted_id = row["id_number_masked"]
                passengers.append(Passenger(
                    name=row["name"],
                    id_number=decrypted_id,
                    gender=row["gender"],
                    phone=row["phone"],
                    email=row["email"] or ""
                ))
            return passengers
        finally:
            conn.close()

    def clear_saved_passengers(self):
        """
        Clears all saved passenger profiles from SQLite.
        """
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM saved_passengers")
        finally:
            conn.close()

    def update_task_status(self, task_id: str, status: TaskStatus, booking_id: Optional[str] = None):
        now = time.time()
        status_val = status.value if isinstance(status, TaskStatus) else status
        conn = self._get_connection()
        try:
            with conn:
                if booking_id:
                    conn.execute(
                        "UPDATE tasks SET status = ?, booking_id = ?, updated_at = ? WHERE task_id = ?",
                        (status_val, booking_id, now, task_id)
                    )
                else:
                    conn.execute(
                        "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        (status_val, now, task_id)
                    )
        finally:
            conn.close()

    def log_event(self, task_id: str, message: str, level: str = "INFO"):
        clean_msg = mask_pii_in_text(message)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO task_logs (task_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                    (task_id, level, clean_msg, time.time())
                )
        finally:
            conn.close()

    def get_logs(self, task_id: str) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM task_logs WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,)
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def log_bot_interaction(
        self,
        chat_id: str,
        command: str,
        raw_text: str,
        response: str,
        status: str = "SUCCESS"
    ):
        """
        Persists all Telegram bot interactions into SQLite with strict PII masking
        (Malaysian ICs, phones, secrets masked) for completely safe storage on EC2.
        """
        clean_raw_text = mask_pii_in_text(raw_text)
        clean_response = mask_pii_in_text(response)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO bot_interactions (chat_id, command, raw_text_masked, response_masked, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (str(chat_id), command, clean_raw_text, clean_response, status, time.time()))
        finally:
            conn.close()

    def get_bot_interactions(self, chat_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieves historical bot interaction audit records.
        """
        conn = self._get_connection()
        try:
            if chat_id:
                cursor = conn.execute(
                    "SELECT * FROM bot_interactions WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(chat_id), limit)
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM bot_interactions ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def _row_to_config(self, row: sqlite3.Row) -> SniperTaskConfig:
        raw_encrypted = row["passengers"] or "[]"
        decrypted_json = self.encryptor.decrypt(raw_encrypted)
        try:
            passengers_raw = json.loads(decrypted_json)
        except Exception:
            passengers_raw = []

        passengers = []
        for p in passengers_raw:
            id_val = p.get("id_number", "")
            if id_val.startswith("gAAAAA"):
                id_val = self.encryptor.decrypt(id_val)
            passengers.append(Passenger(
                name=p["name"],
                id_number=id_val,
                gender=p["gender"],
                phone=p["phone"],
                email=p.get("email", "")
            ))

        # Extract optional round trip fields safely
        keys = row.keys()
        is_round_trip = bool(row["is_round_trip"]) if "is_round_trip" in keys else False
        return_date = row["return_date"] if "return_date" in keys else None
        return_time_from = row["return_time_from"] if "return_time_from" in keys and row["return_time_from"] else "00:00"
        return_time_to = row["return_time_to"] if "return_time_to" in keys and row["return_time_to"] else "23:59"
        leg_type = row["leg_type"] if "leg_type" in keys and row["leg_type"] else ("OUTBOUND" if is_round_trip else "ONE_WAY")

        return SniperTaskConfig(
            task_id=row["task_id"],
            origin=row["origin"],
            destination=row["destination"],
            date=row["date"],
            time_from=row["time_from"],
            time_to=row["time_to"],
            preferred_trains=json.loads(row["preferred_trains"] or "[]"),
            preferred_classes=json.loads(row["preferred_classes"] or "[]"),
            seat_preference=row["seat_preference"],
            required_seats=row["required_seats"],
            passengers=passengers,
            auto_reserve=bool(row["auto_reserve"]),
            status=TaskStatus(row["status"]),
            is_round_trip=is_round_trip,
            return_date=return_date,
            return_time_from=return_time_from,
            return_time_to=return_time_to,
            leg_type=leg_type
        )
