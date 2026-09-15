import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from ktm_sniper.security import mask_ic, mask_phone

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    MONITORING = "MONITORING"
    RESERVED = "RESERVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

@dataclass
class Passenger:
    name: str
    id_number: str
    gender: str
    phone: str
    email: Optional[str] = ""

    @property
    def masked_id(self) -> str:
        return mask_ic(self.id_number)

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.phone)

    def clean_id(self) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", self.id_number)

    def to_dict(self, mask_pii: bool = False) -> Dict[str, str]:
        return {
            "name": self.name,
            "id_number": self.masked_id if mask_pii else self.id_number,
            "gender": self.gender,
            "phone": self.masked_phone if mask_pii else self.phone,
            "email": self.email or ""
        }

    def __repr__(self) -> str:
        return f"Passenger(name='{self.name}', id_number='{self.masked_id}', gender='{self.gender}', phone='{self.masked_phone}')"

@dataclass
class TripInfo:
    train_no: str
    train_class: str
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    available_seats: int
    fare: float = 0.0
    trip_id: str = ""

def normalize_date(date_str: str) -> str:
    """
    Normalizes and auto-pads date input into canonical YYYY-MM-DD format.
    Accepts YYYY-MM-DD, YYYY-M-D, YYYY/M/D, YYYY.M.D.
    """
    cleaned = date_str.strip()
    m = re.match(r"^(\d{4})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])$", cleaned)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    raise ValueError(f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD.")

@dataclass
class SniperTaskConfig:
    origin: str
    destination: str
    date: str
    time_from: str = "00:00"
    time_to: str = "23:59"
    preferred_trains: List[str] = field(default_factory=list)
    preferred_classes: List[str] = field(default_factory=list)
    seat_preference: str = "Window"
    required_seats: int = 1
    passengers: List[Passenger] = field(default_factory=list)
    auto_reserve: bool = True
    task_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    leg_type: str = "ONE_WAY"  # "ONE_WAY", "OUTBOUND", "RETURN"

    # Round-trip options
    is_round_trip: bool = False
    return_date: Optional[str] = None
    return_time_from: str = "00:00"
    return_time_to: str = "23:59"
    return_preferred_trains: List[str] = field(default_factory=list)
    return_preferred_classes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.origin.strip().upper() == self.destination.strip().upper():
            raise ValueError("Origin and destination cannot be identical.")
        self.date = normalize_date(self.date)

        if self.is_round_trip:
            if not self.return_date:
                raise ValueError("Return date must be specified for round trip tasks.")
            self.return_date = normalize_date(self.return_date)
            if self.return_date < self.date:
                raise ValueError(f"Return date '{self.return_date}' cannot be earlier than outbound date '{self.date}'.")
            if self.leg_type == "ONE_WAY":
                self.leg_type = "OUTBOUND"

    def get_return_task(self) -> 'SniperTaskConfig':
        """
        Generates the paired return leg task configuration.
        """
        if not self.is_round_trip or not self.return_date:
            raise ValueError("Cannot generate return task for a one-way trip.")

        return SniperTaskConfig(
            origin=self.destination,
            destination=self.origin,
            date=self.return_date,
            time_from=self.return_time_from,
            time_to=self.return_time_to,
            preferred_trains=self.return_preferred_trains or self.preferred_trains,
            preferred_classes=self.return_preferred_classes or self.preferred_classes,
            seat_preference=self.seat_preference,
            required_seats=self.required_seats,
            passengers=self.passengers,
            auto_reserve=self.auto_reserve,
            is_round_trip=False,
            leg_type="RETURN"
        )

    def matches_trip(self, trip: TripInfo) -> bool:
        # Check seat capacity
        if trip.available_seats < self.required_seats:
            return False

        # Check departure time window
        dep_time = trip.departure_time.strip()
        if dep_time < self.time_from or dep_time > self.time_to:
            return False

        # Check preferred train numbers
        if self.preferred_trains and trip.train_no not in self.preferred_trains:
            return False

        # Check preferred classes
        if self.preferred_classes:
            matched_class = any(
                p_cls.strip().lower() in trip.train_class.lower()
                for p_cls in self.preferred_classes
            )
            if not matched_class:
                return False

        return True
