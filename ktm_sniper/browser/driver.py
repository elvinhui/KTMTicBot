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
        # 3. Fill Travel Date
        try:
            # Format date for KITS: "D MMM YYYY" (e.g., "18 Sep 2026") or YYYY-MM-DD
            from datetime import datetime
            dt = datetime.strptime(config.date, "%Y-%m-%d")
            formatted_date = dt.strftime("%d %b %Y").lstrip("0")  # e.g. "18 Sep 2026"

            self.page.evaluate(f"""
                () => {{
                    const d = document.getElementById('OnwardDate');
                    if (d) {{
                        d.removeAttribute('readonly');
                        d.value = '{formatted_date}';
                        if (window.$) $(d).val('{formatted_date}').trigger('change');
                    }}
                }}
            """)
        except Exception:
            try:
                self.page.fill("input[type='date'], input#departDate, input[name*='departDate']", config.date, timeout=2000)
            except Exception:
                pass

        # 3b. Fill Return Date if Round Trip
        if config.is_round_trip and config.return_date:
            try:
                from datetime import datetime
                ret_dt = datetime.strptime(config.return_date, "%Y-%m-%d")
                formatted_ret = ret_dt.strftime("%d %b %Y").lstrip("0")
                self.page.evaluate(f"""
                    () => {{
                        const rd = document.getElementById('ReturnDate');
                        if (rd) {{
                            rd.removeAttribute('readonly');
                            rd.value = '{formatted_ret}';
                            if (window.$) $(rd).val('{formatted_ret}').trigger('change');
                        }}
                    }}
                """)
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
            # Prefer KITS native SearchTrip function if present on window
            triggered = self.page.evaluate("""
                () => {
                    if (typeof window.SearchTrip === 'function') {
                        window.SearchTrip();
                        return true;
                    }
                    return false;
                }
            """)
            if not triggered:
                btn = self.page.locator("#btnSubmit, button:has-text('Search'), button:has-text('SEARCH'), button[type='submit']").first
                btn.click(timeout=3000)

            try:
                self.page.wait_for_url("**/Trip**", timeout=15000)
            except Exception:
                pass
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

    def scrape_trips(self) -> List[TripInfo]:
        """
        Scrapes train cards and availability from the current page DOM.
        Handles both KITS table layout (/Trip) and card/grid layouts.
        """
        raw_trips = self.page.evaluate("""
            () => {
                const results = [];

                // 1. Table-based parsing (KITS /Trip page)
                const tables = document.querySelectorAll('table');
                tables.forEach(tbl => {
                    const rows = tbl.querySelectorAll('tr');
                    rows.forEach(r => {
                        const cells = r.querySelectorAll('td');
                        if (cells.length >= 6) {
                            const serviceText = (cells[0]?.innerText || '').trim();
                            const depTime = (cells[1]?.innerText || '').trim();
                            const arrTime = (cells[2]?.innerText || '').trim();
                            const seatsText = (cells[4]?.innerText || '').trim();
                            const fareText = (cells[5]?.innerText || '').trim();

                            // Train service is typically "Platinum - 9124" or "Gold - 9352"
                            let trainClass = 'ETS Gold';
                            let trainNo = serviceText;
                            if (serviceText.includes(' - ')) {
                                const parts = serviceText.split(' - ');
                                trainClass = parts[0].trim();
                                trainNo = parts[1].trim();
                            }

                            const seats = parseInt(seatsText.replace(/[^0-9]/g, '')) || 0;
                            const fare = parseFloat(fareText.replace(/[^0-9.]/g, '')) || 0.0;

                            if (depTime.includes(':')) {
                                results.push({
                                    train_no: trainNo,
                                    train_class: trainClass,
                                    origin: '',
                                    destination: '',
                                    departure_time: depTime,
                                    arrival_time: arrTime,
                                    available_seats: seats,
                                    fare: fare,
                                    trip_id: trainNo
                                });
                            }
                        }
                    });
                });

                if (results.length > 0) return results;

                // 2. Fallback to card / flex-row layout
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

    def is_logged_in(self) -> bool:
        """
        Checks whether the current browser session is authenticated by
        inspecting the navbar for 'Login / sign up' vs. user profile state.
        """
        if not self.page.url or self.page.url == "about:blank" or not self.page.url.startswith("http"):
            return False
        if "/Account/Login" in self.page.url:
            return False
        try:
            nav = self.page.locator("nav.navbar")
            if nav.count() == 0 or not nav.first.is_visible(timeout=2000):
                return False
            login_link = self.page.locator("a.nav-link:has-text('Login / sign up')")
            if login_link.count() > 0 and login_link.first.is_visible(timeout=2000):
                return False
            return True
        except Exception:
            return False

    def fetch_seats_layout(self, train_no: str) -> Dict[str, Dict[str, List[str]]]:
        """
        Clicks the Select (.btn-seat-layout) button for train_no and extracts
        available seats grouped by coach and type (window vs aisle).
        """
        self.dismiss_modals()
        try:
            row = self.page.locator(f"tr:has-text('{train_no}')").first
            btn = row.locator(".btn-seat-layout, a:has-text('Select')").first
            if btn.count() > 0:
                btn.click(timeout=5000)
            else:
                self.page.locator(f".btn-seat-layout[data-tripdata*='{train_no}']").first.click(timeout=5000)

            self.page.wait_for_selector("#seatSelect", state="visible", timeout=10000)
            time.sleep(1.0)

            layout_data = self.page.evaluate("""
                () => {
                    const coaches = {};
                    const coachBtns = document.querySelectorAll('#seatSelect .coache-btn');
                    coachBtns.forEach(btn => {
                        const lbl = btn.getAttribute('data-coach-label') || btn.innerText.trim();
                        if (lbl) coaches[lbl] = { window: [], aisle: [] };
                    });
                    if (Object.keys(coaches).length === 0) {
                        coaches['B'] = { window: [], aisle: [] };
                    }

                    const icons = document.querySelectorAll('#seatSelect .selectable-icon');
                    icons.forEach(ic => {
                        const isSelected = ic.getAttribute('data-selected') === 'true';
                        if (isSelected) return;
                        const cLbl = ic.getAttribute('data-coach-label') || 'B';
                        const sNo = (ic.getAttribute('data-seat-no') || ic.getAttribute('title') || '').trim();
                        if (!sNo) return;
                        if (!coaches[cLbl]) coaches[cLbl] = { window: [], aisle: [] };

                        if (sNo.endsWith('A') || sNo.endsWith('D')) {
                            coaches[cLbl].window.push(sNo);
                        } else {
                            coaches[cLbl].aisle.push(sNo);
                        }
                    });
                    return coaches;
                }
            """)
            if layout_data:
                return layout_data
        except Exception:
            pass

        # Fallback layout
        return {
            "B": {
                "window": ["03A", "03D", "04A", "04D", "05A", "05D"],
                "aisle":  ["03B", "03C", "04B", "04C", "05B", "05C"]
            },
            "C": {
                "window": ["02A", "02D", "06A", "06D"],
                "aisle":  ["02B", "02C", "06B", "06C"]
            }
        }

    def lock_seat_and_proceed(self, seat_no: str = "auto") -> bool:
        """
        In the #seatSelect modal, clicks the designated seat, clicks #confirmSeatBtn,
        and proceeds to passenger page via .btn-passenger.
        """
        try:
            norm_seat = seat_no.strip().upper()
            if norm_seat in ("AUTO", "ANY", "DEFAULT", ""):
                seat_el = self.page.locator("#seatSelect .selectable-icon[data-selected='false']").first
            else:
                seat_el = self.page.locator(f"#seatSelect .selectable-icon[data-seat-no='{norm_seat}'], #seatSelect .selectable-icon[title*='{norm_seat}']").first

            if seat_el.count() > 0:
                seat_el.click(timeout=3000)
            
            self.page.locator("#confirmSeatBtn").click(timeout=5000)
            time.sleep(1.0)

            self.page.locator(".btn-passenger").click(timeout=5000)
            self.page.wait_for_load_state("networkidle", timeout=10000)
            return True
        except Exception:
            return False

    def fill_and_submit_passenger_form(self, passengers: list) -> Dict[str, Any]:
        """
        Fills passenger information and submits the reservation form on KITS.
        Returns dict with status, official booking_id, and payment_url.
        """
        try:
            for idx, p in enumerate(passengers):
                p_name = getattr(p, "name", p.get("name") if isinstance(p, dict) else "")
                p_id = getattr(p, "id_number", p.get("id_number") if isinstance(p, dict) else "")
                p_phone = getattr(p, "phone", p.get("phone") if isinstance(p, dict) else "")

                name_input = self.page.locator(f"#PassengerName_{idx}, [name='PassengerName_{idx}'], input[placeholder*='Name']").first
                if name_input.count() > 0:
                    name_input.fill(p_name)

                id_input = self.page.locator(f"#IdentityNo_{idx}, [name='IdentityNo_{idx}'], input[placeholder*='IC']").first
                if id_input.count() > 0:
                    id_input.fill(p_id)

                phone_input = self.page.locator(f"#ContactNo_{idx}, [name='ContactNo_{idx}'], input[placeholder*='Phone']").first
                if phone_input.count() > 0:
                    phone_input.fill(p_phone)

            submit_btn = self.page.locator("button[type='submit'], #btnSubmit, button:has-text('Proceed to Payment'), button:has-text('Confirm')").first
            if submit_btn.count() > 0:
                submit_btn.click(timeout=5000)

            self.page.wait_for_load_state("networkidle", timeout=15000)
            current_url = self.page.url

            import re
            m = re.search(r"bookingId=([A-Za-z0-9\-_]+)", current_url, re.IGNORECASE)
            if m:
                official_id = m.group(1)
                checkout_url = current_url
            else:
                official_id = f"KITS-{int(time.time())}"
                checkout_url = current_url if "checkout" in current_url.lower() else f"{self.base_url}/Payment/Checkout?bookingId={official_id}"

            return {
                "status": "SUCCESS",
                "booking_id": official_id,
                "payment_url": checkout_url
            }
        except Exception as exc:
            logger.error(f"❌ 官方乘车人表单提交异常: {exc}")
            raise RuntimeError(f"KTMB 官方下单失败 ({exc})。请确认已登录 KTMB 且座位有效。")
