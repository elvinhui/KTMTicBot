import logging
from typing import List, Dict, Any, Optional

from ktm_sniper.stations import KTMStationRegistry
from ktm_sniper.network.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException
from ktm_sniper.network.kits_parser import ParsedTrip
from ktm_sniper.models import SniperTaskConfig, TripInfo

logger = logging.getLogger(__name__)


class KTMTicketChecker:
    """
    Queries KITS for train availability using the curl_cffi 3-step HTTP flow
    (no Playwright required for polling).

    Wraps KITSClient.search_trips() with circuit breaker protection and
    converts ParsedTrip objects to the canonical TripInfo model.
    Also retains compatibility with raw HTTP session mocking.
    """

    def __init__(
        self,
        session=None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        base_url: str = "https://online.ktmb.com.my"
    ):
        self.session = session
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.base_url = base_url.rstrip("/")

    def search_trips(
        self,
        origin: str,
        destination: str,
        date: str,
        passengers: int = 1
    ) -> List[Any]:
        """
        Executes trip search via KITSClient or legacy session.
        Returns list of ParsedTrip objects or raw dicts.
        """
        if not self.circuit_breaker.can_execute():
            raise CircuitBreakerOpenException("Circuit breaker is OPEN. Request blocked.")

        if self.session is None:
            return []

        try:
            # 1. New KITSClient flow
            from ktm_sniper.network.session import KITSClient
            if isinstance(self.session, KITSClient):
                trips = self.session.search_trips(
                    origin=origin,
                    destination=destination,
                    date=date,
                    passengers=passengers,
                )
                self.circuit_breaker.record_success()
                return trips

            # 2. Legacy / mock session fallback (e.g. mock_session.post(...))
            origin_code = KTMStationRegistry.get_code(origin)
            destination_code = KTMStationRegistry.get_code(destination)
            payload = {
                "origin": origin_code,
                "destination": destination_code,
                "date": date,
                "passengers": passengers
            }
            response = self.session.post(f"{self.base_url}/v2/trips/search", json=payload)
            if response.status_code == 200:
                self.circuit_breaker.record_success()
                data = response.json()
                return data.get("trips", [])
            else:
                self.circuit_breaker.record_failure()
                return []
        except CircuitBreakerOpenException:
            raise
        except Exception as exc:
            logger.error(f"search_trips error: {exc}")
            self.circuit_breaker.record_failure()
            raise

    def find_matching_trips(self, config: SniperTaskConfig) -> List[TripInfo]:
        """
        Executes search and applies multi-dimensional filtering criteria
        (departure time range, train class, train number, seat capacity).
        Converts ParsedTrip or raw dict -> TripInfo for engine consumption.
        """
        raw_trips = self.search_trips(
            origin=config.origin,
            destination=config.destination,
            date=config.date,
            passengers=config.required_seats,
        )

        matching_trips: List[TripInfo] = []
        for item in raw_trips:
            # Handle ParsedTrip dataclass
            if isinstance(item, ParsedTrip):
                if not item.is_available:
                    continue
                trip = TripInfo(
                    train_no=item.train_no,
                    train_class=item.train_class,
                    origin=config.origin,
                    destination=config.destination,
                    departure_time=item.depart_time,
                    arrival_time=item.arrive_time,
                    available_seats=item.available_seats,
                    fare=item.min_fare,
                    trip_id=item.train_no,
                )
            # Handle dict (mock / legacy data)
            elif isinstance(item, dict):
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
            else:
                continue

            if config.matches_trip(trip):
                matching_trips.append(trip)

        return matching_trips
