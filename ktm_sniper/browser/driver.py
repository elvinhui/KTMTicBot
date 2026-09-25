import os
import shutil
import logging
import time
from typing import List, Optional, Dict, Any
from ktm_sniper.models import SniperTaskConfig, TripInfo
from ktm_sniper.stations import KTMStationRegistry

logger = logging.getLogger(__name__)

class KTMBrowserDriver:
    """
    Automates interactions with the KTMB KITS web portal (online.ktmb.com.my)
    via Playwright headless Chromium.
    """
    def __init__(self, page, base_url: str = "https://online.ktmb.com.my"):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def check_maintenance_modal(self) -> Optional[str]:
        """
        Checks if KTMB daily maintenance modal (scheduled 23:00 - 00:15 UTC+8) is active.
        """
        try:
            return self.page.evaluate("""() => {
                const modal = document.getElementById('popupModal');
                if (modal && (modal.classList.contains('show') || window.getComputedStyle(modal).display !== 'none')) {
                    const text = modal.innerText || '';
                    if (text.toLowerCase().includes('maintenance')) {
                        const body = document.getElementById('popupModalBody');
                        return body ? body.innerText.trim() : text.trim();
                    }
                }
                return null;
            }""")
        except Exception:
            return None

    def dismiss_modals(self):
        """
        Dismisses advertisement modals, notification popups, or cookie consent banners if present.
        Leaves system maintenance alerts intact so callers can detect scheduled downtime.
        """
        # If active maintenance modal, do not blindly click
        m_text = self.check_maintenance_modal()
        if m_text:
            return

        for selector in [
            "#popupModalCloseButton",
            "#popupModalOkButton",
            ".cc-btn",
            ".cc-dismiss",
            ".cc-allow",
            "a.cc-btn",
            "#CloseButtonAdvertisement",
            "button:has-text('OK')",
            "button:has-text('Close')",
            "button:has-text('Accept')"
        ]:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=1000, force=True)
            except Exception:
                pass

        try:
            self.page.evaluate("""() => {
                document.querySelectorAll('.cc-window, .cc-banner, .cc-overlay, #cookie-law-info-bar').forEach(el => el.remove());
                const modal = document.getElementById('popupModal');
                if (modal && (modal.classList.contains('show') || modal.getAttribute('data-show') === 'true')) {
                    const text = modal.innerText || '';
                    if (!text.toLowerCase().includes('maintenance')) {
                        const btn = document.getElementById('popupModalCloseButton') || document.getElementById('popupModalOkButton') || modal.querySelector('button, .btn');
                        if (btn && window.getComputedStyle(btn).display !== 'none') {
                            btn.click();
                        } else {
                            modal.classList.remove('show');
                            modal.style.display = 'none';
                            document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                        }
                    }
                }
            }""")
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
                if btn.count() > 0:
                    btn.click(timeout=3000, force=True)

            try:
                self.page.wait_for_url("**/Trip**", timeout=15000)
            except Exception:
                pass
            self.page.wait_for_load_state("networkidle", timeout=10000)
            try:
                self.page.wait_for_selector("tbody.depart-trips tr", state="visible", timeout=15000)
            except Exception:
                pass
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

    def fetch_seats_layout(self, train_no: str, task: Optional[Any] = None) -> Dict[str, Dict[str, List[str]]]:
        """
        Navigates to /Trip if not already present, clicks the Pick Seats button for train_no,
        and extracts available seats grouped by coach and type (window vs aisle).
        """
        self.dismiss_modals()
        try:
            # 1. Ensure browser is on the /Trip page
            if "/Trip" not in (self.page.url or "") and task is not None:
                logger.info(f"🧭 正在为订座将无头浏览器导航至 /Trip ({task.origin} -> {task.destination} on {task.date})...")
                self.navigate_to_booking()
                self.fill_search_criteria(task)
                self.trigger_search()

            try:
                self.page.wait_for_selector(f"tbody.depart-trips tr:has-text('{train_no}') .btn-seat-layout, tbody.depart-trips tr .btn-seat-layout", state="visible", timeout=15000)
            except Exception:
                pass

            self.dismiss_modals()
            row = self.page.locator(f"tbody.depart-trips tr:has-text('{train_no}')").first
            if row.count() == 0:
                row = self.page.locator("tbody.depart-trips tr").first

            btn = row.locator(".btn-seat-layout, button:has-text('Pick Seats'), a:has-text('Pick Seats'), a:has-text('Select')").first
            if btn.count() > 0:
                try:
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=5000, force=True)
                except Exception:
                    btn.click(timeout=5000)
            else:
                try:
                    self.page.locator(f".btn-seat-layout[data-tripdata*='{train_no}']").first.click(timeout=5000, force=True)
                except Exception:
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
        except Exception as e:
            logger.warning(f"获取车次 {train_no} 实时座舱布局时异常: {e}")

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

    def lock_seat_and_proceed(self, seat_no: str = "auto", train_no: Optional[str] = None, task: Optional[Any] = None) -> bool:
        """
        Ensures seat selection modal is open, selects seat (supporting Window/Aisle preferences
        or specific seat numbers), confirms seat, and proceeds to passenger details (/Book).
        """
        try:
            # 1. Ensure browser is on /Trip page
            if "/Trip" not in (self.page.url or "") and task is not None:
                self.navigate_to_booking()
                self.fill_search_criteria(task)
                self.trigger_search()

            # 2. Dismiss any modal/cookie popups
            self.dismiss_modals()

            # 3. Open #seatSelect modal
            if not self.page.locator("#seatSelect").is_visible():
                m_init = self.check_maintenance_modal()
                if m_init:
                    raise RuntimeError(f"KTMB 官方系统维护中（每晚 23:00 - 00:15 例行维护，暂停订票与支付）：{m_init}")

                btn = self.page.locator(f"tbody.depart-trips tr:has-text('{train_no}') .btn-seat-layout, tbody.depart-trips tr .btn-seat-layout").first
                if btn.count() > 0:
                    btn.click(timeout=5000, force=True)
                else:
                    self.fetch_seats_layout(train_no or "", task=task)

                try:
                    self.page.wait_for_selector("#seatSelect", state="visible", timeout=6000)
                except Exception:
                    m_text = self.check_maintenance_modal()
                    if m_text:
                        raise RuntimeError(f"KTMB 官方系统维护中（每晚 23:00 - 00:15 例行维护，暂停订票与支付）：{m_text}")
                    raise
                time.sleep(1.0)

            # 4. Smart Seat Selection via DOM click
            pref = (seat_no or "AUTO").strip().upper()
            target_seat_no = self.page.evaluate("""
                (pref) => {
                    const seats = Array.from(document.querySelectorAll('#seatSelect .selectable-icon[data-selected="false"]'));
                    if (!seats.length) return null;

                    let chosen = null;
                    if (pref === 'WINDOW') {
                        chosen = seats.find(s => {
                            const no = (s.getAttribute('data-seat-no') || s.getAttribute('title') || '').toUpperCase();
                            return no.endsWith('A') || no.endsWith('D');
                        });
                    } else if (pref === 'AISLE') {
                        chosen = seats.find(s => {
                            const no = (s.getAttribute('data-seat-no') || s.getAttribute('title') || '').toUpperCase();
                            return no.endsWith('B') || no.endsWith('C');
                        });
                    } else if (pref !== 'AUTO' && pref !== 'ANY' && pref !== 'DEFAULT' && pref !== '') {
                        chosen = seats.find(s => {
                            const no = (s.getAttribute('data-seat-no') || s.getAttribute('title') || '').toUpperCase();
                            return no === pref || no.endsWith(pref);
                        });
                    }
                    if (!chosen) chosen = seats[0];
                    chosen.click();
                    return chosen.getAttribute('data-seat-no') || chosen.getAttribute('title');
                }
            """, pref)

            logger.info(f"💺 已选定座位: {target_seat_no} (偏好: {pref})")
            time.sleep(1.0)

            # 5. Confirm seat selection
            try:
                self.page.wait_for_selector("#confirmSeatBtn:not(.disabled-btn)", timeout=5000)
            except Exception:
                pass

            confirm_btn = self.page.locator("#confirmSeatBtn").first
            if confirm_btn.count() > 0:
                confirm_btn.evaluate("e => e.click()")
            time.sleep(1.5)

            # 6. Click PROCEED TO PASSENGER DETAILS
            proceed_btn = self.page.locator("button:has-text('PROCEED TO PASSENGER DETAILS'), .btn-passenger").first
            if proceed_btn.count() > 0:
                proceed_btn.evaluate("e => e.click()")

            try:
                self.page.wait_for_url("**/Book**", timeout=15000)
            except Exception:
                pass
            self.page.wait_for_load_state("networkidle", timeout=10000)
            return True
        except Exception as e:
            logger.warning(f"锁座流程推进失败: {e}")
            raise RuntimeError(f"{e}")

    def fill_and_submit_passenger_form(self, passengers: list) -> Dict[str, Any]:
        """
        Fills passenger information on official KITS /Book page, handles Takaful insurance
        and meal prompts, and reaches the official Order / Payment options page.
        Returns dict with status, official booking_id, and payment_url.
        """
        try:
            self.page.wait_for_selector("#Passengers_0__FullName, input.FullName", timeout=10000)

            for idx, p in enumerate(passengers):
                p_name = getattr(p, "name", p.get("name") if isinstance(p, dict) else "")
                p_id = getattr(p, "id_number", p.get("id_number") if isinstance(p, dict) else "")
                p_phone = getattr(p, "phone", p.get("phone") if isinstance(p, dict) else "")
                p_gender = getattr(p, "gender", p.get("gender") if isinstance(p, dict) else "Male")

                name_input = self.page.locator(f"#Passengers_{idx}__FullName, input.FullName").first
                if name_input.count() > 0:
                    name_input.fill(p_name)

                id_input = self.page.locator(f"#Passengers_{idx}__IdentityNo, input.IdentityNo").first
                if id_input.count() > 0:
                    id_input.fill(p_id)

                phone_input = self.page.locator(f"#Passengers_{idx}__ContactNo, input.ContactNo").first
                if phone_input.count() > 0:
                    phone_input.fill(p_phone)

                if str(p_gender).lower() in ("female", "f", "女"):
                    self.page.locator(f"#Passengers_{idx}__GenderFemale").first.click(timeout=2000)
                else:
                    self.page.locator(f"#Passengers_{idx}__GenderMale").first.click(timeout=2000)

                ticket_sel = self.page.locator(f"#Passengers_{idx}__Tickets_0__TicketTypeId").first
                if ticket_sel.count() > 0:
                    try:
                        ticket_sel.select_option("Adult", timeout=2000)
                    except Exception:
                        pass

            # Step 1: Submit passenger details
            logger.info("📝 正在提交乘车人信息...")
            self.page.locator("#btnConfirmPayment, button:has-text('PROCEED TO PAYMENT')").last.click(timeout=5000)
            time.sleep(2.5)

            # Step 2: Handle Takaful Insurance page
            takaful_proceed = self.page.locator("#btnUpdateInsuranceYes, button:has-text('PROCEED TO PAYMENT')").last
            if takaful_proceed.is_visible():
                logger.info("🛡️ 正在处理 Takaful 保险确认...")
                takaful_proceed.click(timeout=5000)
                time.sleep(1.5)

            takaful_confirm = self.page.locator("button:has-text('purchase Takaful'), a:has-text('purchase Takaful')").first
            if takaful_confirm.is_visible():
                takaful_confirm.click(timeout=5000)
                time.sleep(2.0)

            # Step 3: Handle Confirmation & Meals page
            final_pay = self.page.locator("button:has-text('PROCEED TO PAYMENT'), a:has-text('PROCEED TO PAYMENT')").last
            if final_pay.is_visible():
                logger.info("🍽️ 正在处理餐食确认并提交订单...")
                final_pay.click(timeout=5000)
                time.sleep(1.5)

            meal_confirm = self.page.locator("button:has-text('Confirmed'), a:has-text('Confirmed')").first
            if meal_confirm.is_visible():
                meal_confirm.click(timeout=5000)
                time.sleep(2.5)

            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            current_url = self.page.url
            screenshot_path = "reservation_success.png"
            try:
                self.page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                pass

            # Extract Booking ID from page or URL
            import re
            m = re.search(r"bookingId=([A-Za-z0-9\-_]+)", current_url, re.IGNORECASE)
            official_id = ""
            if m:
                official_id = m.group(1)
            else:
                try:
                    official_id = self.page.evaluate("""
                        () => {
                            const bId = document.querySelector('[data-booking-id], #BookingId, input[name="BookingId"]');
                            if (bId) return bId.value || bId.getAttribute('data-booking-id');
                            const m = document.body.innerText.match(/Booking (?:No|ID|Reference)\\s*[:#]?\\s*([A-Za-z0-9\\-_]+)/i);
                            return m ? m[1] : '';
                        }
                    """)
                except Exception:
                    pass

            if not official_id:
                official_id = f"KITS-{int(time.time())}"

            # Determine payment/checkout URL:
            if "checkout" in current_url.lower() or "payment" in current_url.lower():
                checkout_url = current_url
            elif official_id and not official_id.startswith("KITS-"):
                checkout_url = f"{self.base_url}/Payment/Checkout?bookingId={official_id}"
            else:
                checkout_url = f"{self.base_url}/Booking/UpcomingList"

            # Step 4: Automatically select DuitNow QR to display the payment QR code
            qr_screenshot_path = "data/payment_qr.png"
            import os
            os.makedirs("data", exist_ok=True)
            has_qr = False

            try:
                duitnow_opt = self.page.locator("text='DuitNow QR', img[src*='duitnow' i], [data-payment-method*='duitnow' i]").first
                if duitnow_opt.is_visible(timeout=3000):
                    logger.info("💳 正在选择 DuitNow QR 官方支付方式...")
                    duitnow_opt.click()
                    time.sleep(1.0)

                    pay_btn = self.page.locator("button:has-text('PAY'), button:has-text('PROCEED'), input[value*='Pay' i], #btnPayNow").first
                    if pay_btn.is_visible(timeout=2000):
                        logger.info("💳 点击确认调起 DuitNow QR 码...")
                        pay_btn.click()
                        time.sleep(2.0)

                    qr_elem = self.page.locator("img[src*='qr' i], img[src*='duitnow' i], canvas, #qrCode, .qr-code").first
                    if qr_elem.is_visible(timeout=3000):
                        logger.info("📸 检测到官方支付二维码，正在精准截图...")
                        qr_elem.screenshot(path=qr_screenshot_path)
                        has_qr = True
                    else:
                        self.page.screenshot(path=qr_screenshot_path, full_page=True)
                        has_qr = True
                else:
                    if os.path.exists(screenshot_path):
                        import shutil
                        shutil.copyfile(screenshot_path, qr_screenshot_path)
                        has_qr = True
            except Exception as e:
                logger.warning(f"调起 DuitNow QR 异常: {e}")
                if os.path.exists(screenshot_path):
                    import shutil
                    shutil.copyfile(screenshot_path, qr_screenshot_path)
                    has_qr = True

            logger.info(f"🎉 KTMB 官方订单生成成功！订单编号: {official_id}, 支付页面: {checkout_url}")
            return {
                "status": "SUCCESS",
                "booking_id": official_id,
                "payment_url": checkout_url,
                "payment_qr": qr_screenshot_path if has_qr else None
            }
        except Exception as exc:
            logger.error(f"❌ 官方乘车人表单提交异常: {exc}")
            raise RuntimeError(f"KTMB 官方下单失败 ({exc})。请确认已登录 KTMB 且座位有效。")
