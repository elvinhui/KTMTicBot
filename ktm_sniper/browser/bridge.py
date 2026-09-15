from typing import Dict, Any, List, Optional
from ktm_sniper.models import SniperTaskConfig, TripInfo
from ktm_sniper.stations import KTMStationRegistry

class BrowserContextBridge:
    """
    Executes fast API queries within the authenticated Playwright Page context,
    benefiting from genuine Cloudflare cookies and TLS session bypass.
    """
    def __init__(self, page):
        self.page = page

    def search_trips_via_inpage_fetch(self, config: SniperTaskConfig) -> List[TripInfo]:
        origin_code = KTMStationRegistry.get_code(config.origin)
        dest_code = KTMStationRegistry.get_code(config.destination)

        script = """
        async ({ origin, destination, date, passengers }) => {
            try {
                const response = await fetch('/v2/trips/search', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify({
                        origin: origin,
                        destination: destination,
                        date: date,
                        passengers: passengers
                    })
                });
                if (!response.ok) return { error: response.status };
                return await response.json();
            } catch (e) {
                return { error: e.message };
            }
        }
        """

        raw_result = self.page.evaluate(
            script,
            {
                "origin": origin_code,
                "destination": dest_code,
                "date": config.date,
                "passengers": config.required_seats
            }
        )

        if not raw_result or "error" in raw_result:
            return []

        trips: List[TripInfo] = []
        for item in raw_result.get("trips", []):
            trips.append(TripInfo(
                train_no=item.get("train_no", "ETS"),
                train_class=item.get("train_class", item.get("class", "ETS Gold")),
                origin=config.origin,
                destination=config.destination,
                departure_time=item.get("departure_time", "00:00"),
                arrival_time=item.get("arrival_time", "00:00"),
                available_seats=int(item.get("available_seats", 0)),
                fare=float(item.get("fare", 0.0)),
                trip_id=str(item.get("trip_id", item.get("train_no", "")))
            ))
        return trips
