import time
from typing import List, Optional, Dict, Any
from ktm_sniper.models import SniperTaskConfig, TripInfo
from ktm_sniper.stations import KTMStationRegistry

class KTMBrowserDriver:
    """
    Automates interactions with the KTMB KITS web portal (online.ktmb.com.my)
    via Playwright headless Chromium.
    """
    def __init__(self, page, base_url: str = "https://online.ktmb.com.my"):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def dismiss_modals(self):
        """
        Dismisses advertisement modals or notification popups if present.
        """
        for selector in [
            "#CloseButtonAdvertisement",
            "#popupModalOkButton",
            "#popupModalCloseButton",
            "button:has-text('OK')",
            "button:has-text('Close')"
        ]:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=1000):
                    locator.click(timeout=1500)
            except Exception:
                pass

    def navigate_to_booking(self, timeout_ms: int = 30000):
        self.page.goto(self.base_url, timeout=timeout_ms)
        self.dismiss_modals()

    def fill_search_criteria(self, config: SniperTaskConfig):
        """
        Fills the origin, destination, and travel date in the KITS web portal.
        Handles both KITS Select2 controls and standard input controls.
        """
        self.dismiss_modals()
        origin_code, origin_name = KTMStationRegistry.resolve_station(config.origin)
        dest_code, dest_name = KTMStationRegistry.resolve_station(config.destination)

        # 1. Fill Origin Station
        filled_origin = False
        try:
            # Check for live KITS Select2 element
            if self.page.locator("#FromStationId, #FromStationData").first.count() > 0:
                self.page.evaluate(f"""
                    () => {{
                        const inputData = document.getElementById('FromStationData');
                        if (inputData) inputData.value = '{origin_name}';
                        const select = document.getElementById('FromStationId');
                        if (select) {{
                            for (let opt of select.options) {{
                                if (opt.text.toUpperCase().includes('{origin_name}') || opt.value === '{origin_code}') {{
                                    opt.selected = true;
                                    break;
                                }}
                            }}
                            if (window.$ && $('#FromStationId').select2) {{
                                $('#FromStationId').val($('#FromStationId').val()).trigger('change');
                            }}
                        }}
                    }}
                """)
                filled_origin = True
        except Exception:
            pass

        if not filled_origin:
            try:
                self.page.fill(
                    "input#originStation, input[placeholder*='Origin'], input[name*='origin']",
                    origin_name,
                    timeout=2000
                )
            except Exception:
                pass

        # 2. Fill Destination Station
        filled_dest = False
        try:
            if self.page.locator("#ToStationId, #ToStationData").first.count() > 0:
                self.page.evaluate(f"""
                    () => {{
                        const inputData = document.getElementById('ToStationData');
                        if (inputData) inputData.value = '{dest_name}';
                        const select = document.getElementById('ToStationId');
                        if (select) {{
                            for (let opt of select.options) {{
                                if (opt.text.toUpperCase().includes('{dest_name}') || opt.value === '{dest_code}') {{
                                    opt.selected = true;
                                    break;
                                }}
                            }}
                            if (window.$ && $('#ToStationId').select2) {{
                                $('#ToStationId').val($('#ToStationId').val()).trigger('change');
                            }}
                        }}
                    }}
                """)
                filled_dest = True
        except Exception:
            pass

        if not filled_dest:
            try:
                self.page.fill(
                    "input#destStation, input[placeholder*='Destination'], input[name*='destination']",
                    dest_name,
                    timeout=2000
                )
            except Exception:
                pass

        # 3. Fill Travel Date
        try:
            # Format date for KITS if needed (DD/MM/YYYY or YYYY-MM-DD)
            date_parts = config.date.split("-")
            formatted_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else config.date

            if self.page.locator("#OnwardDate").first.count() > 0:
                self.page.fill("#OnwardDate", formatted_date, timeout=2000)
            else:
                self.page.fill("input[type='date'], input#departDate, input[name*='departDate']", config.date, timeout=2000)
        except Exception:
            pass

        # 3b. Fill Return Date if Round Trip
        if config.is_round_trip and config.return_date:
            try:
                ret_parts = config.return_date.split("-")
                formatted_ret = f"{ret_parts[2]}/{ret_parts[1]}/{ret_parts[0]}" if len(ret_parts) == 3 else config.return_date
                if self.page.locator("#ReturnDate").first.count() > 0:
                    self.page.fill("#ReturnDate", formatted_ret, timeout=2000)
                else:
                    self.page.fill("input#returnDate, input[name*='returnDate']", config.return_date, timeout=2000)
            except Exception:
                pass

        # 4. Fill Passenger Count if selector exists
        try:
            if self.page.locator("#PassengerCount").first.count() > 0:
                self.page.select_option("#PassengerCount", value=str(config.required_seats), timeout=2000)
        except Exception:
            pass

    def trigger_search(self):
        self.dismiss_modals()
        try:
            btn = self.page.locator("#btnSubmit, button:has-text('Search'), button:has-text('SEARCH'), button[type='submit']").first
            btn.click(timeout=3000)
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

    def scrape_trips(self) -> List[TripInfo]:
        """
        Scrapes train cards and availability from the current page DOM.
        """
        raw_trips = self.page.evaluate("""
            () => {
                const results = [];
                const cards = document.querySelectorAll('.trip-card, .train-row, [data-train-no], tr[data-trip]');
                cards.forEach(c => {
                    const trainNo = c.getAttribute('data-train-no') || c.querySelector('.train-no, .train-name')?.innerText || 'ETS';
                    const trainClass = c.querySelector('.train-class, .service-type')?.innerText || 'ETS Gold';
                    const depTime = c.querySelector('.dep-time, .departure-time')?.innerText || '00:00';
                    const arrTime = c.querySelector('.arr-time, .arrival-time')?.innerText || '00:00';
                    const seatsText = c.querySelector('.seats-available, .available-seats')?.innerText || '0';
                    const fareText = c.querySelector('.fare, .price')?.innerText || '0';

                    const seats = parseInt(seatsText.replace(/[^0-9]/g, '')) || 0;
                    const fare = parseFloat(fareText.replace(/[^0-9.]/g, '')) || 0.0;

                    results.push({
                        train_no: trainNo.trim(),
                        train_class: trainClass.trim(),
                        origin: '',
                        destination: '',
                        departure_time: depTime.trim(),
                        arrival_time: arrTime.trim(),
                        available_seats: seats,
                        fare: fare,
                        trip_id: trainNo.trim()
                    });
                });
                return results;
            }
        """)

        trips: List[TripInfo] = []
        for item in (raw_trips or []):
            trips.append(TripInfo(
                train_no=item.get("train_no", "ETS"),
                train_class=item.get("train_class", "ETS Gold"),
                origin=item.get("origin", ""),
                destination=item.get("destination", ""),
                departure_time=item.get("departure_time", "00:00"),
                arrival_time=item.get("arrival_time", "00:00"),
                available_seats=int(item.get("available_seats", 0)),
                fare=float(item.get("fare", 0.0)),
                trip_id=item.get("trip_id", "")
            ))
        return trips

    def take_screenshot(self, output_path: str, full_page: bool = True) -> str:
        self.page.screenshot(path=output_path, full_page=full_page)
        return output_path
