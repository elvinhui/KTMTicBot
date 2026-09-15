import re
from typing import Dict, Any, Union, List, Optional
from ktm_sniper.models import Passenger

class KTMSeatReserver:
    """
    Handles validating passenger details and constructing the reservation payload
    to lock seats on KTMB KITS with a 15-minute countdown.
    """
    def __init__(self, session=None, base_url: str = "https://online.ktmb.com.my"):
        self.session = session
        self.base_url = base_url.rstrip("/")

    def validate_passenger(self, passenger: Union[Dict[str, Any], Passenger]):
        p_data = passenger.to_dict() if isinstance(passenger, Passenger) else passenger

        required_fields = ["name", "id_number", "gender", "phone"]
        for field in required_fields:
            if field not in p_data or not str(p_data[field]).strip():
                raise ValueError(f"Missing required passenger field: {field}")

        # Validate gender
        gender = str(p_data["gender"]).strip().lower()
        if gender not in ["male", "female", "m", "f"]:
            raise ValueError(f"Invalid gender: '{p_data['gender']}'. Must be 'Male' or 'Female'.")

        # Validate ID (MyKad or Passport)
        raw_id = re.sub(r"[^A-Za-z0-9]", "", str(p_data["id_number"]))
        if len(raw_id) < 5 or len(raw_id) > 20:
            raise ValueError(f"Invalid ID number: '{p_data['id_number']}'.")

    def reserve_seat(
        self,
        trip_id: str,
        seat_preference: str,
        passenger: Optional[Union[Dict[str, Any], Passenger]] = None,
        passengers: Optional[List[Union[Dict[str, Any], Passenger]]] = None
    ) -> Dict[str, Any]:
        pass_list: List[Union[Dict[str, Any], Passenger]] = []
        if passengers:
            pass_list = list(passengers)
        elif passenger:
            pass_list = [passenger]
        else:
            raise ValueError("At least one passenger must be provided for seat reservation.")

        passenger_payloads = []
        for p in pass_list:
            self.validate_passenger(p)
            p_data = p.to_dict() if isinstance(p, Passenger) else p
            passenger_payloads.append({
                "name": p_data["name"],
                "id_number": p_data["id_number"],
                "gender": p_data["gender"],
                "phone": p_data["phone"]
            })

        payload = {
            "trip_id": trip_id,
            "seat_preference": seat_preference,
            "passenger": passenger_payloads[0],
            "passengers": passenger_payloads
        }

        if self.session is None:
            return {
                "status": "MOCK_SUCCESS",
                "booking_id": "MOCK-123456",
                "timeout_minutes": 15,
                "payment_url": f"{self.base_url}/v2/payment/checkout?bookingId=MOCK-123456"
            }

        url = f"{self.base_url}/v2/booking/reserve"
        response = self.session.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            if "payment_url" not in data and "booking_id" in data:
                data["payment_url"] = f"{self.base_url}/v2/payment/checkout?bookingId={data['booking_id']}"
            return data
        else:
            raise RuntimeError(f"Reservation failed with status code: {response.status_code}")
