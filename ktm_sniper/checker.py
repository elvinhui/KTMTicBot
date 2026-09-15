from typing import List, Dict, Any, Optional
from ktm_sniper.stations import KTMStationRegistry
from ktm_sniper.network.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException
from ktm_sniper.models import SniperTaskConfig, TripInfo

class KTMTicketChecker:
    """
    Handles querying the KITS API for train availability with circuit breaker protection.
    """
    def __init__(self, session=None, circuit_breaker: Optional[CircuitBreaker] = None, base_url: str = "https://online.ktmb.com.my"):
        self.session = session
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.base_url = base_url.rstrip("/")

    def search_trips(self, origin: str, destination: str, date: str, passengers: int = 1) -> List[Dict[str, Any]]:
        if not self.circuit_breaker.can_execute():
            raise CircuitBreakerOpenException("Circuit breaker is OPEN. Request blocked.")

        origin_code = KTMStationRegistry.get_code(origin)
        destination_code = KTMStationRegistry.get_code(destination)

        payload = {
            "origin": origin_code,
            "destination": destination_code,
            "date": date,
            "passengers": passengers
        }

        if self.session is None:
            return []

        try:
            response = self.session.post(f"{self.base_url}/v2/trips/search", json=payload)
            
            if response.status_code == 200:
                self.circuit_breaker.record_success()
                data = response.json()
                return data.get("trips", [])
            elif response.status_code in [403, 429]:
                self.circuit_breaker.record_failure()
                return []
            else:
                self.circuit_breaker.record_failure()
                return []
        except Exception:
            self.circuit_breaker.record_failure()
            raise

    def find_matching_trips(self, config: SniperTaskConfig) -> List[TripInfo]:
        """
        Executes search and applies multi-dimensional filtering criteria
        (departure time range, train class, train number, seat capacity).
        """
        raw_trips = self.search_trips(
            origin=config.origin,
            destination=config.destination,
            date=config.date,
            passengers=config.required_seats
        )

        matching_trips: List[TripInfo] = []
        for item in raw_trips:
            trip = TripInfo(
                train_no=item.get("train_no", "ETS"),
                train_class=item.get("train_class", item.get("class", "ETS Gold")),
                origin=config.origin,
                destination=config.destination,
                departure_time=item.get("departure_time", "00:00"),
                arrival_time=item.get("arrival_time", "00:00"),
                available_seats=int(item.get("available_seats", 0)),
                fare=float(item.get("fare", 0.0)),
                trip_id=str(item.get("trip_id", item.get("train_no", "")))
            )
            if config.matches_trip(trip):
                matching_trips.append(trip)

        return matching_trips
