import os
import re
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
        Checks if KTMB daily maintenance modal is blocking and active without dismiss button.
        If it is an advance informational notice (e.g. maintenance scheduled tonight) with a Close button,
        it should be dismissed so booking can continue.
        """
        try:
            return self.page.evaluate("""() => {
                const modal = document.getElementById('popupModal');
                if (modal && (modal.classList.contains('show') || window.getComputedStyle(modal).display !== 'none')) {
                    const text = (modal.innerText || '').toLowerCase();
                    const closeBtn = document.getElementById('popupModalCloseButton') || 
                                     document.getElementById('popupModalOkButton') || 
                                     modal.querySelector('button, .btn, .close');
                    // If there is an active dismiss/close button, it is an advance warning, not a hard blocking error
                    if (closeBtn && window.getComputedStyle(closeBtn).display !== 'none') {
                        return null;
                    }
                    if (text.includes('maintenance') || text.includes('under maintenance')) {
                        const body = document.getElementById('popupModalBody');
                        return body ? body.innerText.trim() : modal.innerText.trim();
                    }
                }
                return null;
            }""")
        except Exception:
            return None

    def dismiss_modals(self):
        """
        Dismisses advertisement modals, notification popups, advance notice modals, or cookie consent banners.
        Removes blocking backdrops so subsequent clicks are not intercepted.
        """
        try:
            self.page.evaluate("""() => {
                // 1. Remove cookie banners and overlays
                document.querySelectorAll('.cc-window, .cc-banner, .cc-overlay, #cookie-law-info-bar').forEach(el => el.remove());

                // 2. Handle #popupModal (advance notice, promo, alerts)
                const modal = document.getElementById('popupModal');
                if (modal) {
                    const btn = document.getElementById('popupModalCloseButton') || 
                                document.getElementById('popupModalOkButton') || 
                                modal.querySelector('button, .btn, .close');
                    if (btn && window.getComputedStyle(btn).display !== 'none') {
                        btn.click();
                    }
                    // Force close and remove backdrop if still present
                    modal.classList.remove('show');
                    modal.style.display = 'none';
                    modal.setAttribute('aria-hidden', 'true');
                }

                // 3. Remove any remaining modal backdrops that block pointer events
                document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                document.body.classList.remove('modal-open');
                document.body.style.overflow = '';
            }""")
        except Exception:
            pass

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
                if locator.is_visible(timeout=300):
                    locator.click(timeout=800, force=True)
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

        # 3b. Fill Return Date if Round Trip (or clear if One-Way)
        expected_ret = config.return_date if (getattr(config, "is_round_trip", False) and getattr(config, "return_date", None)) else None
        self._last_searched_return_date = expected_ret
        if expected_ret:
            try:
                from datetime import datetime
                ret_dt = datetime.strptime(expected_ret, "%Y-%m-%d")
                formatted_ret = ret_dt.strftime("%d %b %Y").lstrip("0")
                self.page.evaluate(f"""
                    () => {{
                        const rd = document.getElementById('ReturnDate');
                        if (rd) {{
                            rd.removeAttribute('readonly');
                            rd.value = '{formatted_ret}';
                            if (window.$) $(rd).val('{formatted_ret}').trigger('change');
                        }}
                        document.querySelectorAll('input.ReturnDate').forEach(el => {{ el.value = '{formatted_ret}'; }});
                    }}
                """)
            except Exception:
                pass
        else:
            try:
                self.page.evaluate("""
                    () => {
                        const rd = document.getElementById('ReturnDate');
                        if (rd) {
                            rd.value = '';
                            if (window.$) $(rd).val('').trigger('change');
                        }
                        document.querySelectorAll('input.ReturnDate').forEach(el => { el.value = ''; });
                    }
                """)
            except Exception:
                pass

        # 4. Fill Passenger Count (supports hidden Select2 and standard <select>)
        pax_count = len(config.passengers) if getattr(config, "passengers", None) else max(1, getattr(config, "required_seats", 1))
        self._last_searched_pax = pax_count
        try:
            if self.page.locator("#PassengerCount, select[name='PassengerCount']").first.count() > 0:
                self.page.evaluate(f"""
                    () => {{
                        const paxStr = '{pax_count}';
                        const sel = document.getElementById('PassengerCount') || document.querySelector("select[name='PassengerCount']");
                        if (sel) {{
                            sel.value = paxStr;
                            for (const opt of sel.options) {{
                                if (opt.value === paxStr || opt.text.trim().startsWith(paxStr + ' ')) {{
                                    opt.selected = true;
                                    break;
                                }}
                            }}
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            if (window.$ && $('#PassengerCount').length) {{
                                $('#PassengerCount').val(paxStr).trigger('change');
                            }}
                        }}
                        const fixedPax = document.getElementById('fixedPax');
                        if (fixedPax) fixedPax.value = paxStr;
                        document.querySelectorAll('input.PassengerCount').forEach(el => {{ el.value = paxStr; }});
                    }}
                """)
        except Exception:
            try:
                self.page.select_option("#PassengerCount", value=str(pax_count), timeout=1000)
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
            if getattr(self, "_last_searched_return_date", None):
                try:
                    self.page.wait_for_selector("tbody.return-trips tr", state="visible", timeout=15000)
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

    @staticmethod
    def _get_fallback_layout() -> Dict[str, Dict[str, List[str]]]:
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

    def _open_seat_modal_for_trip(self, tbody_cls: str, train_no: Optional[str] = None) -> bool:
        """
        Locates the specific trip row in `tbody_cls` (depart or return) and clicks its 'Pick Seats'
        anchor/button, waiting for KITS to fetch seats via AJAX and display the #seatSelect modal.
        Does NOT force-show the modal via jQuery/Bootstrap to avoid unpopulated seat layouts.
        """
        self.dismiss_modals()
        self.dismiss_popup_alerts()

        raw_train = str(train_no or "").strip()
        clean_train = re.sub(r"[^0-9]", "", raw_train)

        # 1. Attempt JS trigger directly on DOM button/anchor
        try:
            clicked = bool(self.page.evaluate(
                """({ tbodyCls, rawTrain, cleanTrain }) => {
                    const tbody = document.querySelector(tbodyCls);
                    if (!tbody) return false;
                    const rows = Array.from(tbody.querySelectorAll('tr:not(.disabled)'));
                    if (rows.length === 0) return false;

                    let targetRow = null;
                    if (rawTrain || cleanTrain) {
                        targetRow = rows.find(r => {
                            const text = (r.innerText || '').toUpperCase();
                            if (rawTrain && text.includes(rawTrain.toUpperCase())) return true;
                            if (cleanTrain && text.replace(/[^0-9]/g, '').includes(cleanTrain)) return true;
                            return false;
                        });
                    }
                    if (!targetRow) targetRow = rows[0];
                    if (!targetRow) return false;

                    const btn = targetRow.querySelector('.btn-seat-layout, a.select-btn, [data-tripdata], a, button');
                    if (!btn) return false;

                    try { btn.scrollIntoView({ block: 'center', behavior: 'instant' }); } catch (e) {}
                    btn.click();
                    return true;
                }""",
                {"tbodyCls": tbody_cls, "rawTrain": raw_train, "cleanTrain": clean_train}
            ))
        except Exception as e:
            logger.warning(f"JS 触发选座按钮失败: {e}")

        # 2. Wait for #seatSelect modal to become visible
        modal_opened = False
        try:
            self.page.wait_for_selector("#seatSelect", state="visible", timeout=6000)
            modal_opened = True
        except Exception:
            modal_opened = False

        # 3. If modal didn't open, retry with Playwright native locator click
        if not modal_opened:
            try:
                self.dismiss_popup_alerts()
                loc = None
                if clean_train:
                    loc = self.page.locator(
                        f"{tbody_cls} tr:has-text('{clean_train}') .btn-seat-layout, "
                        f"{tbody_cls} tr:has-text('{clean_train}') [data-tripdata], "
                        f"{tbody_cls} tr:has-text('{clean_train}') a:has-text('Pick Seats')"
                    ).first
                if not loc or loc.count() == 0:
                    loc = self.page.locator(
                        f"{tbody_cls} tr .btn-seat-layout, "
                        f"{tbody_cls} tr [data-tripdata], "
                        f"{tbody_cls} tr a:has-text('Pick Seats')"
                    ).first

                if loc and loc.count() > 0:
                    try:
                        loc.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    loc.click(timeout=4000, force=True)
                    self.page.wait_for_selector("#seatSelect", state="visible", timeout=6000)
                    modal_opened = True
            except Exception as ex:
                logger.warning(f"Native locator 选座按钮点击重试失败: {ex}")

        # 4. If modal is visible, wait briefly for seat icons or coaches to be rendered
        if modal_opened:
            try:
                self.page.wait_for_selector("#seatSelect .selectable-icon, #seatSelect .coache-btn, #seatSelect .cart", state="visible", timeout=4000)
            except Exception:
                pass
            time.sleep(0.5)

        return modal_opened

    def fetch_seats_layout(self, train_no: str, task: Optional[Any] = None, leg: str = "DEPART") -> Dict[str, Dict[str, List[str]]]:
        """
        Navigates to /Trip if not already present, clicks the Pick Seats button for train_no,
        and extracts available seats grouped by coach and type (window vs aisle).
        Supports both outbound (DEPART) and return (RETURN) trips.
        """
        self.dismiss_modals()
        try:
            target_count = len(task.passengers) if (task and getattr(task, "passengers", None)) else (getattr(task, "required_seats", 1) if task else 1)
            target_count = max(1, target_count)
            expected_ret = task.return_date if (task and getattr(task, "is_round_trip", False) and getattr(task, "return_date", None)) else None

            tbody_cls = "tbody.return-trips" if leg.upper() == "RETURN" else "tbody.depart-trips"
            raw_train = str(train_no or "").strip()
            clean_train = re.sub(r"[^0-9]", "", raw_train)

            needs_search = False
            if "/Trip" not in (self.page.url or ""):
                needs_search = True
            elif self.page.locator("tbody.depart-trips tr").count() == 0:
                needs_search = True
            elif expected_ret and self.page.locator("tbody.return-trips tr").count() == 0:
                needs_search = True
            elif task is not None and getattr(self, "_last_searched_return_date", None) != expected_ret:
                needs_search = True
            elif task is not None and getattr(self, "_last_searched_pax", None) not in (None, target_count):
                needs_search = True
            else:
                try:
                    train_in_table = self.page.evaluate(
                        """({ tbodyCls, rawTrain, cleanTrain }) => {
                            const tbody = document.querySelector(tbodyCls);
                            if (!tbody) return false;
                            const rows = Array.from(tbody.querySelectorAll('tr:not(.disabled)'));
                            if (rows.length === 0) return false;
                            if (!rawTrain && !cleanTrain) return rows.some(r => !!r.querySelector('.btn-seat-layout, [data-tripdata]'));
                            return rows.some(r => {
                                const t = (r.innerText || '').toUpperCase();
                                const match = (rawTrain && t.includes(rawTrain.toUpperCase())) || (cleanTrain && t.replace(/[^0-9]/g, '').includes(cleanTrain));
                                return match && !!r.querySelector('.btn-seat-layout, [data-tripdata], a, button');
                            });
                        }""",
                        {"tbodyCls": tbody_cls, "rawTrain": raw_train, "cleanTrain": clean_train}
                    )
                    if train_in_table is False:
                        needs_search = True
                except Exception:
                    pass

            if needs_search and task is not None:
                logger.info(f"🧭 正在为获取席位布局重新查询车次 /Trip ({task.origin} -> {task.destination} on {task.date}, 返程: {expected_ret or '单程'}, 乘车人数: {target_count})...")
                self.navigate_to_booking()
                self.fill_search_criteria(task)
                self.trigger_search()

            self.dismiss_modals()
            if task is not None:
                try:
                    self.page.evaluate(
                        """(pax) => {
                            const fp = document.querySelector('#fixedPax');
                            if (fp) fp.value = String(pax);
                        }""",
                        target_count,
                    )
                except Exception:
                    pass

            opened = self._open_seat_modal_for_trip(tbody_cls, train_no)
            if opened:
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
                self._close_seat_modal()
                if isinstance(layout_data, dict):
                    total_seats = sum(len(c.get("window", [])) + len(c.get("aisle", [])) for c in layout_data.values() if isinstance(c, dict))
                    if total_seats > 0:
                        return layout_data
        except Exception as e:
            logger.warning(f"获取车次 {train_no} 实时座舱布局时异常: {e}")
            self._close_seat_modal()

        # Fallback layout
        return self._get_fallback_layout()
    def scrape_return_trips(self) -> List[TripInfo]:
        """
        Scrapes available return trips from tbody.return-trips on KITS /Trip page.
        """
        try:
            raw_trips = self.page.evaluate("""
                () => {
                    const results = [];
                    const rows = document.querySelectorAll('tbody.return-trips tr:not(.disabled)');
                    rows.forEach(r => {
                        const cells = r.querySelectorAll('td');
                        if (cells.length >= 6) {
                            const serviceText = (cells[0]?.innerText || '').trim();
                            const depTime = (cells[1]?.innerText || '').trim();
                            const arrTime = (cells[2]?.innerText || '').trim();
                            const seatsText = (cells[4]?.innerText || '').trim();
                            const fareText = (cells[5]?.innerText || '').trim();
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
                    return results;
                }
            """)
            return [
                TripInfo(
                    train_no=item.get("train_no", "ETS"),
                    train_class=item.get("train_class", "ETS Gold"),
                    origin=item.get("origin", ""),
                    destination=item.get("destination", ""),
                    departure_time=item.get("departure_time", "00:00"),
                    arrival_time=item.get("arrival_time", "00:00"),
                    available_seats=int(item.get("available_seats", 0)),
                    fare=float(item.get("fare", 0.0)),
                    trip_id=item.get("trip_id", "")
                )
                for item in (raw_trips or [])
            ]
        except Exception:
            return []

    SESSION_EXPIRED_MARKER = "KITS_SESSION_EXPIRED"

    def _close_seat_modal(self) -> None:
        """Safely hides #seatSelect (if open) without triggering KITS CancelBooking handler."""
        try:
            self.page.evaluate("""
                () => {
                    const modal = document.querySelector('#seatSelect');
                    if (modal) {
                        modal.classList.remove('show');
                        modal.style.display = 'none';
                    }
                    document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                    if (window.jQuery && window.jQuery.fn && window.jQuery.fn.modal) {
                        try { window.jQuery('#seatSelect').modal('hide'); } catch (e) {}
                    }
                }
            """)
        except Exception:
            pass

    def _click_proceed_to_passenger(self) -> bool:
        """Clicks the official PROCEED TO PASSENGER DETAILS button."""
        try:
            clicked = self.page.evaluate("""
                () => {
                    const btn = document.querySelector('button.btn-passenger, #btnProceedPassenger, .proceed-btn');
                    if (btn) {
                        try { btn.scrollIntoView({block: 'center'}); } catch (e) {}
                        btn.click();
                        return true;
                    }
                    const cands = Array.from(document.querySelectorAll('button, a, input[type=submit]'))
                        .filter(b => /PROCEED TO PASSENGER/i.test(b.innerText || b.value || ''));
                    if (cands.length > 0) {
                        try { cands[0].scrollIntoView({block: 'center'}); } catch (e) {}
                        cands[0].click();
                        return true;
                    }
                    const form = document.querySelector('form[action*="/Book"], form#tripForm, form#formBook');
                    if (form) {
                        try { form.submit(); return true; } catch (e) {}
                    }
                    return false;
                }
            """)
            return bool(clicked)
        except Exception as e:
            logger.warning(f"点击 PROCEED TO PASSENGER DETAILS 失败: {e}")
            return False

    def _collect_page_alert_text(self) -> str:
        try:
            text = self.page.evaluate("""
                () => {
                    const sels = ['#popupModal', '#validationSummaryModal', '.modal.show', '.swal2-popup', '.alert-danger'];
                    const out = [];
                    for (const s of sels) {
                        for (const el of document.querySelectorAll(s)) {
                            if (el.offsetParent === null && !el.classList.contains('show')) continue;
                            const t = (el.innerText || '').trim();
                            if (t && !out.includes(t)) out.push(t);
                        }
                    }
                    return out.join(' | ').slice(0, 300);
                }
            """)
            return text if isinstance(text, str) else ""
        except Exception:
            return ""

    def dismiss_popup_alerts(self) -> bool:
        """
        Dismisses any unexpected alert modals (e.g. #popupModal, 'Error when cancelling booking')
        that might block pointer events or seat confirmation on /Trip or /Book pages.
        """
        try:
            return bool(self.page.evaluate("""
                () => {
                    let dismissed = false;
                    const popups = document.querySelectorAll('#popupModal, #validationSummaryModal, .modal.show:not(#seatSelect)');
                    for (const pop of popups) {
                        const btn = pop.querySelector('#popupModalOkButton, #popupModalCloseButton, .close, [data-dismiss="modal"], [data-bs-dismiss="modal"], button');
                        if (btn) {
                            try { btn.click(); dismissed = true; } catch (e) {}
                        }
                        pop.classList.remove('show');
                        pop.style.display = 'none';
                        dismissed = true;
                    }
                    const seatModal = document.querySelector('#seatSelect');
                    const seatOpen = seatModal && (seatModal.classList.contains('show') || seatModal.style.display === 'block');
                    if (!seatOpen) {
                        document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                    }
                    return dismissed;
                }
            """))
        except Exception:
            return False

    def _ensure_on_book_page(self, max_retries: int = 1) -> None:
        """Raises a descriptive error if the browser did not reach the KITS /Book passenger page."""
        url = self.page.url or ""
        if "/Book" in url:
            return
        try:
            if self.page.locator("#Passengers_0__FullName, input.FullName").count() > 0:
                return
        except Exception:
            pass
        alert = self._collect_page_alert_text()

        # If blocked on /Trip by an alert modal (e.g. "Error when cancelling booking") or lingering popup
        if max_retries > 0 and ("/Trip" in url or not url):
            logger.warning(f"⚠️ 正在处理 /Trip 页面提示并尝试重新推进至乘车人页: {alert or '未知遮罩'}")
            # Retry dismissal a few times: KITS sometimes re-renders the alert modal
            for _attempt in range(3):
                self.dismiss_popup_alerts()
                self._close_seat_modal()
                try:
                    self.page.wait_for_selector(
                        '#popupModal.show, #validationSummaryModal.show, .modal.show:not(#seatSelect)',
                        state='hidden', timeout=3000
                    )
                except Exception:
                    pass
                time.sleep(0.5)
                if not self._collect_page_alert_text():
                    break
            self._click_proceed_to_passenger()
            try:
                self.page.wait_for_url("**/Book**", timeout=10000)
                if "/Book" in (self.page.url or ""):
                    return
            except Exception:
                pass
            try:
                self.page.wait_for_load_state("networkidle", timeout=6000)
                if "/Book" in (self.page.url or ""):
                    return
            except Exception:
                pass
            url = self.page.url or ""
            alert = self._collect_page_alert_text()

        try:
            self.page.screenshot(path="data/debug_proceed_fail.png", full_page=True)
        except Exception:
            pass
        logger.error(f"❌ 未能进入 /Book 乘车人页面: url={url} alert={alert!r}")
        login_hint = ("login" in alert.lower()) or ("/Account/Login" in url)
        if login_hint or not self.is_logged_in():
            raise RuntimeError(f"{self.SESSION_EXPIRED_MARKER}: KITS 登录会话已失效 ({alert or url})")
        # 'Error when cancelling booking' is a transient KITS-side state; signal retryable failure
        if "cancelling booking" in (alert or "").lower():
            raise RuntimeError(f"KITS_TRANSIENT_CANCEL_ERROR: {alert}")
        raise RuntimeError(f"未能进入乘车人页面 (KITS 提示: {alert or '无'}; 当前页面: {url})")

    def lock_seat_and_proceed(
        self,
        seat_no: str = "auto",
        train_no: Optional[str] = None,
        task: Optional[Any] = None,
        return_train_no: Optional[str] = None,
        return_seat_no: Optional[str] = None
    ) -> bool:
        """
        Ensures seat selection modal is open, selects seat (supporting Window/Aisle preferences
        or specific seat numbers), confirms seat, and proceeds to passenger details (/Book).
        """
        try:
            pref = (seat_no or "AUTO").strip().upper()
            ret_pref = (return_seat_no or seat_no or "AUTO").strip().upper()
            target_count = len(task.passengers) if (task and getattr(task, "passengers", None)) else (getattr(task, "required_seats", 1) if task else 1)
            target_count = max(1, target_count)
            expected_ret = task.return_date if (task and getattr(task, "return_date", None)) else None
            raw_train = str(train_no or "").strip()
            clean_train = re.sub(r"[^0-9]", "", raw_train)

            # 1. Ensure browser is on /Trip page and train table is rendered with matching passenger count & return_date
            needs_search = False
            if "/Trip" not in (self.page.url or ""):
                needs_search = True
            elif self.page.locator("tbody.depart-trips tr").count() == 0:
                needs_search = True
            elif expected_ret and self.page.locator("tbody.return-trips tr").count() == 0:
                needs_search = True
            elif task is not None and getattr(self, "_last_searched_return_date", None) != expected_ret:
                needs_search = True
            elif task is not None and getattr(self, "_last_searched_pax", None) not in (None, target_count):
                needs_search = True
            elif task is not None:
                try:
                    fixed_pax = self.page.evaluate("() => { const el = document.querySelector('#fixedPax'); return el ? parseInt(el.value || '0', 10) : 0; }")
                    if isinstance(fixed_pax, int) and not isinstance(fixed_pax, bool) and fixed_pax > 0 and fixed_pax != target_count:
                        needs_search = True
                except Exception:
                    pass

            if not needs_search and (raw_train or clean_train):
                try:
                    train_in_table = self.page.evaluate(
                        """({ rawTrain, cleanTrain }) => {
                            const tbody = document.querySelector('tbody.depart-trips');
                            if (!tbody) return false;
                            const rows = Array.from(tbody.querySelectorAll('tr:not(.disabled)'));
                            if (rows.length === 0) return false;
                            return rows.some(r => {
                                const t = (r.innerText || '').toUpperCase();
                                return (rawTrain && t.includes(rawTrain.toUpperCase())) || (cleanTrain && t.replace(/[^0-9]/g, '').includes(cleanTrain));
                            });
                        }""",
                        {"rawTrain": raw_train, "cleanTrain": clean_train}
                    )
                    if train_in_table is False:
                        needs_search = True
                except Exception:
                    pass

            if needs_search and task is not None:
                logger.info(f"🔄 正在按乘车人数 ({target_count} 人) 与返程日期 ({expected_ret or '单程'}) 重新查询车次并分配席位槽...")
                self.navigate_to_booking()
                self.fill_search_criteria(task)
                self.trigger_search()

            self.dismiss_modals()
            self.dismiss_popup_alerts()

            try:
                self.page.evaluate("""(pax) => {
                    const fp = document.querySelector('#fixedPax');
                    if (fp) fp.value = String(pax);
                }""", target_count)
            except Exception:
                pass

            # 2. Open #seatSelect modal for outbound trip
            seat_modal_opened = self._open_seat_modal_for_trip("tbody.depart-trips", train_no)
            if not seat_modal_opened:
                m_init = self.check_maintenance_modal()
                if m_init:
                    raise RuntimeError(f"KTMB 官方系统维护中（每晚 23:00 - 00:15 例行维护，暂停订票与支付）：{m_init}")
                raise RuntimeError(f"未能打开车次 {train_no or 'AUTO'} 的去程选座窗口（可能车票已售罄）")

            # 4. Smart Seat Selection via DOM click (supports multiple passengers/seats)
            seat_select_js = """
                ({ pref, targetCount }) => {
                    const fp = document.querySelector('#fixedPax');
                    if (fp && parseInt(fp.value || '0', 10) < targetCount) {
                        fp.value = String(targetCount);
                    }

                    // Only clear excess seats if cart currently exceeds targetCount, avoiding KITS CancelBooking alert storms
                    const removeBtns = Array.from(document.querySelectorAll('#seatSelect .cart .btn-remove-cart, #seatSelect .btn-remove-cart'));
                    if (removeBtns.length > targetCount) {
                        const excess = removeBtns.length - targetCount;
                        for (let i = 0; i < excess; i++) {
                            try { removeBtns[i].click(); } catch (e) {}
                        }
                    }

                    const userSpecificSeats = pref.split(/[, ]+/).filter(s => s.length > 0 && !['AUTO', 'WINDOW', 'AISLE', 'ANY', 'DEFAULT'].includes(s));
                    const selected = [];

                    let activeCoach = '';
                    const coachBtns = Array.from(document.querySelectorAll('#seatSelect .coache-btn'));
                    const activeBtn = coachBtns.find(b => b.classList.contains('active') || b.classList.contains('btn-secondary') || b.classList.contains('selected'));
                    if (activeBtn) {
                        const rawLbl = (activeBtn.getAttribute('data-coach-label') || activeBtn.innerText || '').trim();
                        const m = rawLbl.match(/^([A-Z0-9]+)/i);
                        if (m) activeCoach = m[1].toUpperCase();
                    }

                    const allAvailable = () => Array.from(document.querySelectorAll('#seatSelect .selectable-icon[data-selected="false"]'));
                    const getSeatNo = (el) => (el.getAttribute('data-seat-no') || el.getAttribute('title') || el.innerText || '').trim().toUpperCase();
                    const getCoachLbl = (el) => (el.getAttribute('data-coach-label') || activeCoach || '').trim().toUpperCase();

                    // Group available seats by coach to pick a coach with >= targetCount seats
                    const initialAvail = allAvailable();
                    const coachMap = {};
                    for (const el of initialAvail) {
                        const c = getCoachLbl(el) || 'B';
                        if (!coachMap[c]) coachMap[c] = [];
                        coachMap[c].push(el);
                    }
                    let preferredCoach = activeCoach;
                    if (!preferredCoach || !coachMap[preferredCoach] || coachMap[preferredCoach].length < targetCount) {
                        const candidateCoach = Object.keys(coachMap).find(c => coachMap[c].length >= targetCount);
                        if (candidateCoach) preferredCoach = candidateCoach;
                    }

                    // Pre-compute adjacent/same-row seats for AUTO with multiple passengers
                    let autoPlan = [];
                    if (userSpecificSeats.length === 0 && (pref === 'AUTO' || pref === 'ANY' || pref === 'DEFAULT') && targetCount > 1) {
                        const pool = (preferredCoach && coachMap[preferredCoach] && coachMap[preferredCoach].length >= targetCount)
                            ? coachMap[preferredCoach]
                            : initialAvail;
                        const rowGroups = {};
                        const orderedPool = [...pool].sort((a, b) => getSeatNo(a).localeCompare(getSeatNo(b), undefined, { numeric: true }));
                        for (const el of orderedPool) {
                            const sNo = getSeatNo(el);
                            const rowMatch = sNo.match(/^(\\d+)/);
                            const rowKey = `${getCoachLbl(el)}_${rowMatch ? rowMatch[1].padStart(2, '0') : sNo}`;
                            if (!rowGroups[rowKey]) rowGroups[rowKey] = [];
                            rowGroups[rowKey].push(el);
                        }
                        // 1. Check if a single row can accommodate all targetCount passengers
                        const fullRowKey = Object.keys(rowGroups).find(k => rowGroups[k].length >= targetCount);
                        if (fullRowKey) {
                            autoPlan = rowGroups[fullRowKey].slice(0, targetCount);
                        } else {
                            // 2. Otherwise pick from sorted pool (consecutive rows in the same coach)
                            autoPlan = orderedPool.slice(0, targetCount);
                        }
                    }

                    for (let i = 0; i < targetCount; i++) {
                        const available = allAvailable();
                        if (!available.length) break;

                        const coachAvail = (preferredCoach && available.filter(s => getCoachLbl(s) === preferredCoach).length > 0)
                            ? available.filter(s => getCoachLbl(s) === preferredCoach)
                            : available;

                        let chosen = null;
                        if (i < userSpecificSeats.length) {
                            const targetNo = userSpecificSeats[i].toUpperCase();
                            chosen = available.find(s => {
                                const no = getSeatNo(s);
                                return no === targetNo || no.endsWith(targetNo) || targetNo.endsWith(no);
                            });
                        }

                        if (!chosen && i < autoPlan.length && autoPlan[i].getAttribute('data-selected') !== 'true') {
                            chosen = autoPlan[i];
                        }

                        if (!chosen && (pref.includes('WINDOW') || pref === 'AUTO')) {
                            chosen = coachAvail.find(s => {
                                const no = getSeatNo(s);
                                return no.endsWith('A') || no.endsWith('D');
                            }) || available.find(s => {
                                const no = getSeatNo(s);
                                return no.endsWith('A') || no.endsWith('D');
                            });
                        } else if (!chosen && pref.includes('AISLE')) {
                            chosen = coachAvail.find(s => {
                                const no = getSeatNo(s);
                                return no.endsWith('B') || no.endsWith('C');
                            }) || available.find(s => {
                                const no = getSeatNo(s);
                                return no.endsWith('B') || no.endsWith('C');
                            });
                        }

                        if (!chosen) chosen = coachAvail[0] || available[0];
                        const targetCoach = getCoachLbl(chosen);
                        if (targetCoach && targetCoach !== activeCoach) {
                            const cBtn = coachBtns.find(b => (b.getAttribute('data-coach-label') || '').trim().toUpperCase() === targetCoach);
                            if (cBtn) {
                                try { cBtn.click(); activeCoach = targetCoach; } catch (e) {}
                            }
                        }
                        chosen.click();
                        chosen.setAttribute('data-selected', 'true');
                        const sNo = (chosen.getAttribute('data-seat-no') || chosen.getAttribute('title') || chosen.innerText || 'Seat').trim();
                        const cLbl = (chosen.getAttribute('data-coach-label') || activeCoach || '').trim();
                        selected.push(cLbl && !sNo.includes('Coach') ? `Coach ${cLbl} - ${sNo}` : sNo);
                    }
                    return selected.join(', ');
                }
            """
            target_seat_no = self.page.evaluate(seat_select_js, {"pref": pref, "targetCount": target_count})

            time.sleep(1.0)
            try:
                table_seats = self.page.evaluate("""
                    () => {
                        const rows = Array.from(document.querySelectorAll('#seatSelect .cart tr.seat-row'));
                        const items = [];
                        for (const r of rows) {
                            const cells = Array.from(r.querySelectorAll('td')).map(c => (c.innerText || '').trim());
                            if (cells.length >= 4) {
                                const coach = cells[1];
                                const seat = cells[3];
                                if (seat && seat.toLowerCase() !== 'select seat' && seat !== '-') {
                                    items.push(coach && coach !== '-' ? `Coach ${coach} - ${seat}` : seat);
                                }
                            }
                        }
                        return items.join(', ');
                    }
                """)
                if isinstance(table_seats, str) and table_seats.strip():
                    target_seat_no = table_seats.strip()
            except Exception:
                pass

            if not target_seat_no or target_seat_no.strip() == "-":
                raise RuntimeError("未能选定车次可用席位（可能席位已售罄）")

            logger.info(f"💺 已选定座位: {target_seat_no} (席位数: {target_count}, 偏好/指定: {pref})")

            # 5. Confirm seat selection
            self.dismiss_popup_alerts()
            try:
                self.page.wait_for_selector("#confirmSeatBtn:not(.disabled-btn), #seatSelect button:has-text('CONFIRM SEATS')", timeout=5000)
            except Exception:
                pass

            confirm_btn = self.page.locator("#confirmSeatBtn").first
            if confirm_btn.count() > 0:
                confirm_btn.evaluate("e => e.click()")
            time.sleep(2.0)
            try:
                self.page.wait_for_selector("#seatSelect", state="hidden", timeout=8000)
            except Exception:
                pass

            return_target_seat_no = ""
            self.last_return_trip_info = {}
            # 5b. Check if this is a Round Trip where Return trip seats also need to be selected
            target_ret_train = return_train_no or (
                task.return_preferred_trains[0]
                if (task and getattr(task, "return_preferred_trains", None))
                else None
            )
            ret_tf = getattr(task, "return_time_from", "00:00") or "00:00" if task else "00:00"
            ret_tt = getattr(task, "return_time_to", "23:59") or "23:59" if task else "23:59"

            # Find best matching return row by train_no and/or return time window
            matched_ret_train = target_ret_train
            if not matched_ret_train and (ret_tf != "00:00" or ret_tt != "23:59"):
                try:
                    matched_ret_train = self.page.evaluate(
                        """({ tf, tt }) => {
                            const rows = Array.from(document.querySelectorAll('tbody.return-trips tr:not(.disabled)'));
                            for (const r of rows) {
                                const cells = Array.from(r.querySelectorAll('td')).map(c => (c.innerText || '').trim());
                                const dep = cells[1] || '';
                                if (dep && dep >= tf && dep <= tt) {
                                    const svc = cells[0] || '';
                                    return svc.includes(' - ') ? svc.split(' - ')[1].trim() : svc.trim();
                                }
                            }
                            return '';
                        }""",
                        {"tf": ret_tf, "tt": ret_tt},
                    ) or None
                except Exception:
                    pass

            if matched_ret_train or expected_ret or (self.page.locator("tbody.return-trips tr").count() > 0):
                try:
                    ret_meta = self.page.evaluate(
                        """(targetTrain) => {
                            const rows = Array.from(document.querySelectorAll('tbody.return-trips tr:not(.disabled)'));
                            let row = targetTrain ? rows.find(r => (r.innerText || '').includes(targetTrain)) : rows[0];
                            if (!row) row = rows[0] || document.querySelector('tbody.return-trips tr');
                            if (!row) return {};
                            const cells = Array.from(row.querySelectorAll('td')).map(c => (c.innerText || '').trim());
                            const serviceText = cells[0] || '';
                            let trainClass = 'Gold';
                            let trainNo = serviceText;
                            if (serviceText.includes(' - ')) {
                                const parts = serviceText.split(' - ');
                                trainClass = parts[0].trim();
                                trainNo = parts[1].trim();
                            }
                            return {
                                train_no: trainNo,
                                train_class: trainClass,
                                departure_time: cells[1] || '',
                                arrival_time: cells[2] || ''
                            };
                        }""",
                        matched_ret_train or ""
                    )
                    if isinstance(ret_meta, dict) and ret_meta:
                        self.last_return_trip_info = dict(ret_meta)
                except Exception:
                    pass

                logger.info(f"🔄 检测到往返双程（Round Trip），正在选取返程车次 ({self.last_return_trip_info.get('train_no', matched_ret_train or 'AUTO')} @ {self.last_return_trip_info.get('departure_time', '')}) 座位...")
                ret_modal_opened = self._open_seat_modal_for_trip("tbody.return-trips", matched_ret_train)
                if ret_modal_opened:
                    return_target_seat_no = self.page.evaluate(seat_select_js, {"pref": ret_pref, "targetCount": target_count})

                    time.sleep(1.0)
                    try:
                        return_table_seats = self.page.evaluate("""
                            () => {
                                const rows = Array.from(document.querySelectorAll('#seatSelect .cart tr.seat-row'));
                                const items = [];
                                for (const r of rows) {
                                    const cells = Array.from(r.querySelectorAll('td')).map(c => (c.innerText || '').trim());
                                    if (cells.length >= 4) {
                                        const coach = cells[1];
                                        const seat = cells[3];
                                        if (seat && seat.toLowerCase() !== 'select seat' && seat !== '-') {
                                            items.push(coach && coach !== '-' ? `Coach ${coach} - ${seat}` : seat);
                                        }
                                    }
                                }
                                return items.join(', ');
                            }
                        """)
                        if isinstance(return_table_seats, str) and return_table_seats.strip():
                            return_target_seat_no = return_table_seats.strip()
                    except Exception:
                        pass

                    self.last_return_trip_info["selected_seat"] = return_target_seat_no
                    logger.info(f"💺 返程车次已选定座位: {return_target_seat_no}")

                    # Confirm return seat selection (match button by id, class or text)
                    self.dismiss_popup_alerts()
                    try:
                        self.page.wait_for_selector("#confirmSeatBtn:not(.disabled-btn), #seatSelect button:has-text('CONFIRM SEATS')", timeout=5000)
                    except Exception:
                        pass

                    confirm_clicked = False
                    try:
                        c_btn = self.page.locator("#confirmSeatBtn:not(.disabled-btn), #seatSelect button:has-text('CONFIRM SEATS')").first
                        if c_btn.count() > 0:
                            c_btn.scroll_into_view_if_needed(timeout=2000)
                            c_btn.click(timeout=5000, force=True)
                            confirm_clicked = True
                    except Exception as ex:
                        logger.warning(f"常规点击返程确认座位按钮失败，尝试 JS 原生触发: {ex}")

                    if not confirm_clicked:
                        try:
                            self.page.evaluate("""() => {
                                const btn = document.querySelector('#confirmSeatBtn') || Array.from(document.querySelectorAll('#seatSelect button')).find(b => /CONFIRM SEATS/i.test(b.innerText || ''));
                                if (btn) btn.click();
                            }""")
                        except Exception:
                            pass

                    time.sleep(2.0)
                    self.dismiss_popup_alerts()
                    try:
                        self.page.wait_for_selector("#seatSelect", state="hidden", timeout=5000)
                    except Exception:
                        self.dismiss_popup_alerts()
                        try:
                            self.page.evaluate("""() => {
                                const btn = document.querySelector('#confirmSeatBtn') || Array.from(document.querySelectorAll('#seatSelect button')).find(b => /CONFIRM SEATS/i.test(b.innerText || ''));
                                if (btn && !btn.disabled) btn.click();
                                const modal = document.querySelector('#seatSelect');
                                if (modal) modal.classList.remove('show');
                                document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
                            }""")
                        except Exception:
                            pass
                        time.sleep(1.0)

            # 6. Click PROCEED TO PASSENGER DETAILS
            # Dismiss any popup or stray backdrop first
            self.dismiss_popup_alerts()
            self.dismiss_modals()
            self._click_proceed_to_passenger()

            try:
                self.page.wait_for_url("**/Book**", timeout=15000)
            except Exception:
                # If still on /Trip, dismiss any modal alerts and retry proceed click once
                self.dismiss_modals()
                self._click_proceed_to_passenger()
                try:
                    self.page.wait_for_url("**/Book**", timeout=10000)
                except Exception:
                    pass
            try:
                self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            self._ensure_on_book_page()

            self.last_locked_outbound_seat = target_seat_no or ""
            self.last_locked_return_seat = return_target_seat_no or ""
            final_locked_seat = (
                f"{target_seat_no} (返程: {return_target_seat_no})"
                if return_target_seat_no
                else (target_seat_no or "")
            )
            try:
                if not final_locked_seat or "Coach" not in final_locked_seat:
                    book_page_seats = self.page.evaluate(r"""
                        () => {
                            const text = document.body ? document.body.innerText : '';
                            const regex = /-\s*([A-Z0-9]{1,3})\s*\/\s*([0-9]{1,2}[A-Z])\b/g;
                            const found = [];
                            let m;
                            while ((m = regex.exec(text)) !== null) {
                                const item = `Coach ${m[1]} - ${m[2]}`;
                                if (!found.includes(item)) found.push(item);
                            }
                            return found.join(', ');
                        }
                    """)
                    if isinstance(book_page_seats, str) and book_page_seats.strip():
                        final_locked_seat = book_page_seats.strip()
            except Exception:
                pass

            self.last_locked_seat = final_locked_seat
            return final_locked_seat or True
        except Exception as e:
            logger.warning(f"锁座流程推进失败: {e}")
            raise RuntimeError(f"{e}")

    def fill_and_submit_passenger_form(self, passengers: list, task: Optional[Any] = None) -> Dict[str, Any]:
        """
        Fills passenger information on official KITS /Book page, handles Takaful insurance
        and meal prompts, and reaches the official Order / Payment options page.
        Returns dict with status, official booking_id, and payment_url.
        """
        try:
            self.page.wait_for_selector("#Passengers_0__FullName, input.FullName, #theForm", timeout=10000)

            pax_payload = []
            for p in passengers:
                pax_payload.append({
                    "name": getattr(p, "name", p.get("name") if isinstance(p, dict) else ""),
                    "id": getattr(p, "id_number", p.get("id_number") if isinstance(p, dict) else ""),
                    "phone": getattr(p, "phone", p.get("phone") if isinstance(p, dict) else ""),
                    "gender": getattr(p, "gender", p.get("gender") if isinstance(p, dict) else "Male"),
                })

            # 1. First: fix data-ticket-index on .TicketData elements and match Friends & Family (PNR) / IsSelf
            logger.info(f"📝 正在填报 {len(pax_payload)} 位乘车人信息并选定车票类型 (Adult)...")
            pnr_fill_result = self.page.evaluate("""(paxList) => {
                // Fix KITS bug: ensure each .TicketData in each section has data-ticket-index matching its leg (0, 1)
                if (window.jQuery) {
                    window.jQuery('.passenger-section').each(function(pIndex, pEl) {
                        window.jQuery(pEl).find('.TicketData').each(function(tIndex, tEl) {
                            window.jQuery(tEl).attr('data-ticket-index', tIndex);
                        });
                    });
                } else {
                    document.querySelectorAll('.passenger-section').forEach(sec => {
                        sec.querySelectorAll('.TicketData').forEach((el, tIndex) => {
                            el.setAttribute('data-ticket-index', String(tIndex));
                        });
                    });
                }

                const actions = [];
                // Check if passenger 0 can use IsSelf
                const selfBox = document.querySelector('#Passengers_0__IsSelf');
                let selfChecked = false;
                if (selfBox && !selfBox.checked) {
                    selfBox.click();
                    selfChecked = true;
                    actions.push('Passenger 0 checked IsSelf');
                }

                // Match remaining passengers against PNRSelect dropdowns
                for (let idx = (selfChecked ? 1 : 0); idx < paxList.length; idx++) {
                    const p = paxList[idx];
                    const targetName = (p.name || '').trim().toLowerCase();
                    const pnrSel = document.querySelector('#PNRSelect_' + idx);
                    let matched = false;
                    if (pnrSel && targetName) {
                        for (let opt of pnrSel.options) {
                            const optText = (opt.text || '').trim().toLowerCase();
                            if (optText && (targetName.includes(optText) || optText.includes(targetName))) {
                                pnrSel.value = opt.value;
                                if (window.jQuery) {
                                    window.jQuery(pnrSel).val(opt.value).trigger('change');
                                } else {
                                    pnrSel.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                matched = true;
                                actions.push(`Passenger ${idx} selected PNR: ${opt.text}`);
                                break;
                            }
                        }
                    }

                    // Fallback to manual fill if PNR not matched
                    if (!matched) {
                        const sec = document.querySelectorAll('.passenger-section')[idx];
                        if (sec) {
                            const nameInput = sec.querySelector('input.FullName, #Passengers_' + idx + '__FullName');
                            if (nameInput) {
                                nameInput.removeAttribute('disabled');
                                nameInput.removeAttribute('readonly');
                                nameInput.value = p.name;
                            }
                            const idInput = sec.querySelector('input.IdentityNo, #Passengers_' + idx + '__IdentityNo');
                            if (idInput) {
                                idInput.removeAttribute('disabled');
                                idInput.removeAttribute('readonly');
                                idInput.value = p.id;
                            }
                            const phoneInput = sec.querySelector('input.ContactNo, #Passengers_' + idx + '__ContactNo');
                            if (phoneInput) {
                                phoneInput.removeAttribute('disabled');
                                phoneInput.removeAttribute('readonly');
                                phoneInput.value = p.phone;
                            }
                            const isFemale = (p.gender || '').toLowerCase().startsWith('f');
                            const gMale = sec.querySelector('#Passengers_' + idx + '__GenderMale, input.Gender[value="Male"], input.Gender[value="M"]');
                            const gFemale = sec.querySelector('#Passengers_' + idx + '__GenderFemale, input.Gender[value="Female"], input.Gender[value="F"]');
                            if (isFemale && gFemale) { gFemale.checked = true; }
                            else if (gMale) { gMale.checked = true; }
                            actions.push(`Passenger ${idx} manually filled`);
                        }
                    }
                }
                return actions;
            }""", pax_payload)
            logger.info(f"📝 乘车人 PNR 与基本信息匹配完成: {pnr_fill_result}")

            # Wait briefly for KITS AJAX (GetPassengerPNRTicketTypeV4) to fetch ticket types
            time.sleep(2.5)

            # 2. Re-verify .TicketData data-ticket-index and select Adult for all legs
            fill_result = self.page.evaluate("""(paxList) => {
                if (window.jQuery) {
                    window.jQuery('.passenger-section').each(function(pIndex, pEl) {
                        window.jQuery(pEl).find('.TicketData').each(function(tIndex, tEl) {
                            window.jQuery(tEl).attr('data-ticket-index', tIndex);
                        });
                    });
                }
                // Ensure Adult option is present and selected
                document.querySelectorAll('.TicketTypeId').forEach(sel => {
                    let opt = Array.from(sel.options).find(o => /ADULT|DEWASA/i.test(o.text || o.value));
                    if (!opt) {
                        opt = Array.from(sel.options).find(o => o.value && !/OKU/i.test(o.text));
                    }
                    if (!opt) {
                        opt = new Option("DEWASA/ADULT", "Adult", true, true);
                        opt.setAttribute("data-ispnr", "True");
                        sel.add(opt);
                    }
                    sel.value = opt.value || "Adult";
                    sel.removeAttribute('disabled');
                    sel.classList.remove('input-validation-error');
                    if (window.jQuery) window.jQuery(sel).trigger('change');
                });

                // Clean all validation errors and tooltips
                if (window.jQuery && window.jQuery('#theForm').length) {
                    try { window.jQuery('#theForm').validate().resetForm(); } catch (e) {}
                }
                document.querySelectorAll('.tooltip, .tooltipValidation, .field-validation-error').forEach(el => el.remove());
                document.querySelectorAll('.input-validation-error').forEach(el => el.classList.remove('input-validation-error'));

                // Call UpdatePassenger or click #btnConfirmPayment
                try {
                    if (typeof UpdatePassenger === 'function') {
                        UpdatePassenger();
                        return { submitted: true, method: 'UpdatePassenger' };
                    }
                } catch (e) {}

                const btn = document.querySelector('#btnConfirmPayment');
                if (btn) {
                    btn.click();
                    return { submitted: true, method: 'btnConfirmPayment' };
                }
                return { submitted: false, method: 'none' };
            }""", pax_payload)
            logger.info(f"📝 乘车人表单提交执行状态: {fill_result}")

            # 3. Wait for page transition away from /Book to insurance or payment
            try:
                self.page.wait_for_url(lambda u: "/Book" not in u or "Payment" in u or "Checkout" in u, timeout=20000)
            except Exception:
                pass

            # If still on /Book, check if #frmPassenger needs a manual submit trigger
            if "/Book" in (self.page.url or ""):
                try:
                    self.dismiss_popup_alerts()
                    self.page.evaluate("""() => {
                        const frm = document.querySelector('#frmPassenger');
                        if (frm && frm.BookingData && frm.BookingData.value) {
                            frm.submit();
                        }
                    }""")
                    time.sleep(2.0)
                except Exception:
                    pass

            # Step 2: Handle Takaful Insurance page
            include_insurance = bool(getattr(task, "include_insurance", False)) if task else False
            if include_insurance:
                logger.info("🛡️ 用户已开启 Takaful 行程保险，正在勾选保险选项...")
                try:
                    self.page.evaluate("""
                        () => {
                            const boxes = document.querySelectorAll(
                                "input[type='checkbox'][id*='Insurance'], input[type='checkbox'][name*='Insurance'], " +
                                "input[type='checkbox'][id*='Takaful'], input[type='checkbox'][name*='Takaful']"
                            );
                            boxes.forEach(cb => {
                                if (!cb.checked) cb.click();
                            });
                        }
                    """)
                    time.sleep(0.5)
                except Exception:
                    pass

            # Only match specific insurance button
            try:
                takaful_btn_sel = "#btnUpdateInsuranceYes" if include_insurance else "#btnUpdateInsuranceNo"
                self.page.wait_for_selector(f"{takaful_btn_sel}, #btnUpdateInsuranceYes, #btnUpdateInsuranceNo", timeout=4000)
            except Exception:
                pass

            takaful_proceed = self.page.locator("#btnUpdateInsuranceYes, #btnUpdateInsuranceNo").first
            if takaful_proceed.count() > 0 and takaful_proceed.is_visible():
                logger.info(f"🛡️ 正在处理 Takaful 保险确认 (购买保险={include_insurance})...")
                try:
                    takaful_proceed.click(timeout=5000)
                except Exception:
                    takaful_proceed.evaluate("el => el.click()")
                time.sleep(1.5)

            for _ in range(6):
                takaful_confirm = self.page.locator(
                    "button:has-text('purchase Takaful'), "
                    "a:has-text('purchase Takaful'), "
                    "#popupModal.show #popupModalOkButton, "
                    "#popupModal.show button:has-text('OK')"
                ).first
                if takaful_confirm.count() > 0 and takaful_confirm.is_visible():
                    try:
                        takaful_confirm.evaluate("el => el.click()")
                    except Exception:
                        try:
                            takaful_confirm.click(timeout=3000, force=True)
                        except Exception:
                            pass
                    time.sleep(2.0)
                    break
                popup_close = self.page.locator("#popupModal.show #popupModalCloseButton").first
                if popup_close.count() > 0 and popup_close.is_visible():
                    try:
                        popup_close.evaluate("el => el.click()")
                    except Exception:
                        pass
                    time.sleep(1.0)
                    break
                time.sleep(0.3)

            # Step 3: Handle Confirmation & Meals page
            final_pay = self.page.locator(
                "button:has-text('PROCEED TO PAYMENT'):not(#btnConfirmPayment):not(#btnUpdateInsuranceYes):not(#btnUpdateInsuranceNo), "
                "a:has-text('PROCEED TO PAYMENT'):not(#btnConfirmPayment):not(#btnUpdateInsuranceYes):not(#btnUpdateInsuranceNo)"
            ).last
            if final_pay.count() > 0 and final_pay.is_visible():
                logger.info("🍽️ 正在处理餐食确认并提交订单...")
                try:
                    final_pay.click(timeout=3000)
                except Exception:
                    try:
                        final_pay.evaluate("el => el.click()")
                    except Exception:
                        pass
                time.sleep(1.5)

            meal_confirm = self.page.locator(
                "button:has-text('Confirmed'), a:has-text('Confirmed'), #popupModal.show #popupModalOkButton"
            ).first
            if meal_confirm.count() > 0 and meal_confirm.is_visible():
                try:
                    meal_confirm.evaluate("el => el.click()")
                except Exception:
                    try:
                        meal_confirm.click(timeout=3000, force=True)
                    except Exception:
                        pass
                time.sleep(2.5)

            # Wait for checkout page
            try:
                self.page.wait_for_url("**/Payment/Checkout**", timeout=15000)
            except Exception:
                pass

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
                official_id = "PENDING_PAYMENT"

            # Determine payment/checkout URL:
            if "checkout" in current_url.lower() or "payment" in current_url.lower():
                checkout_url = current_url
            elif official_id and official_id != "PENDING_PAYMENT" and not official_id.startswith("KITS-"):
                checkout_url = f"{self.base_url}/Payment/Checkout?bookingId={official_id}"
            else:
                checkout_url = f"{self.base_url}/Booking/UpcomingList"

            # Step 4: Automatically select DuitNow QR to display the payment QR code
            qr_screenshot_path = "data/payment_qr.png"
            os.makedirs("data", exist_ok=True)
            has_qr = False
            gateway_url = None
            payment_amount = ""
            payment_ref = ""

            try:
                # Save pre-payment HTML for debugging and audit, and extract real PaymentAmount
                try:
                    pre_html = self.page.content()
                    with open("data/payment_page.html", "w", encoding="utf-8") as f:
                        f.write(pre_html)
                    if isinstance(pre_html, str):
                        m_amt = re.search(r'name="PaymentAmount"[^>]*value="([0-9]+(?:\.[0-9]+)?)"', pre_html, re.IGNORECASE)
                        if not m_amt:
                            m_amt = re.search(r'Order\s*Amount[^0-9]*RM\s*([0-9,]+(?:\.[0-9]+)?)', pre_html, re.IGNORECASE)
                        if m_amt:
                            try:
                                payment_amount = f"{float(m_amt.group(1).replace(',', '')):.2f}"
                            except Exception:
                                payment_amount = m_amt.group(1)
                        m_k = re.search(r"\b(K\d{10,14})\b", pre_html)
                        if m_k and official_id == "PENDING_PAYMENT":
                            official_id = m_k.group(1)
                except Exception:
                    pass

                # 4.1 Locate and click the exact DuitNow QR option
                # KTMB KITS payment page uses deterministic ID #btnGoPaymentDuitNow
                click_result = self.page.evaluate("""
                    () => {
                        const directBtn = document.querySelector('#btnGoPaymentDuitNow');
                        if (directBtn) {
                            directBtn.scrollIntoView({ behavior: 'instant', block: 'center' });
                            directBtn.click();
                            directBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                            return { found: true, tag: directBtn.tagName, id: 'btnGoPaymentDuitNow' };
                        }

                        // Fallback: Search for DuitNow image or text
                        const images = Array.from(document.querySelectorAll('img'));
                        let target = images.find(img => {
                            const s = (img.src || '').toLowerCase();
                            const a = (img.alt || '').toLowerCase();
                            return s.includes('duitnow') || a.includes('duitnow');
                        });

                        if (!target) {
                            const leafElements = Array.from(document.querySelectorAll('*')).filter(el => el.children.length === 0);
                            target = leafElements.find(el => {
                                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                                return t === 'duitnow qr' || t === 'duitnow' || t.includes('duitnow');
                            });
                        }

                        if (!target) {
                            return { found: false, reason: 'No DuitNow element found' };
                        }

                        let card = target;
                        while (card && card !== document.body && card.tagName !== 'BODY') {
                            const cl = (card.className || '').toString().toLowerCase();
                            if (card.onclick || card.tagName === 'BUTTON' || card.tagName === 'A' || cl.includes('payment') || cl.includes('box') || cl.includes('card') || cl.includes('option') || cl.includes('col-')) {
                                break;
                            }
                            card = card.parentElement;
                        }
                        if (!card || card === document.body) {
                            card = target.parentElement || target;
                        }

                        const radio = card.querySelector('input[type="radio"], input[type="checkbox"]');
                        if (radio) {
                            radio.click();
                            radio.checked = true;
                        }

                        card.scrollIntoView({ behavior: 'instant', block: 'center' });
                        card.click();
                        card.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        return { found: true, tag: card.tagName, class: card.className, targetText: target.innerText || '' };
                    }
                """)
                logger.info(f"💳 官方支付方式点击状态: {click_result}")

                if isinstance(click_result, dict) and click_result.get("found"):
                    time.sleep(1.5)

                    # 4.2 If a Pay / Proceed button exists or appears, click it (ignoring Cancel)
                    submitted = self.page.evaluate("""
                        () => {
                            const proceedBtn = document.querySelector('#btnProceedToPayment');
                            if (proceedBtn && proceedBtn.offsetParent !== null && !proceedBtn.disabled) {
                                proceedBtn.scrollIntoView({ behavior: 'instant', block: 'center' });
                                proceedBtn.click();
                                return { clicked: true, text: proceedBtn.innerText || 'btnProceedToPayment' };
                            }

                            const buttons = Array.from(document.querySelectorAll('button, a, input[type="submit"], input[type="button"]'));
                            for (const btn of buttons) {
                                const t = (btn.innerText || btn.value || '').trim().toUpperCase();
                                if (t.includes('CANCEL') || t.includes('BACK')) {
                                    continue;
                                }
                                if (t === 'PAY' || t.includes('PAY') || t.includes('PROCEED') || t.includes('CONFIRM') || t.includes('CONTINUE') || t.includes('GATEWAY')) {
                                    if (btn.offsetParent !== null && !btn.disabled) {
                                        btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                                        btn.click();
                                        return { clicked: true, text: t };
                                    }
                                }
                            }
                            return { clicked: false };
                        }
                    """)
                    logger.info(f"💳 确认提交支付按钮触发状态: {submitted}")

                    # 4.3 Wait for payment gateway / countdown page to load
                    time.sleep(2.0)
                    try:
                        self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass

                    # Extract payment URL from parent page variable window._paymentUrl if present
                    payment_url_js = None
                    try:
                        payment_url_js = self.page.evaluate("() => window._paymentUrl || null")
                    except Exception:
                        pass

                    # Find or open the gateway page (KTMB opens Fiuu / Razer in a popup tab)
                    gateway_page = None
                    for p in self.page.context.pages:
                        if p != self.page and ("fiuu.com" in p.url or "pay" in p.url or "GoPayment" in p.url):
                            gateway_page = p
                            break

                    if not gateway_page and len(self.page.context.pages) > 1:
                        gateway_page = self.page.context.pages[-1]

                    # If popup wasn't automatically opened, open it explicitly using _paymentUrl
                    if not gateway_page and payment_url_js:
                        try:
                            full_url = f"{self.base_url}{payment_url_js}" if payment_url_js.startswith("/") else payment_url_js
                            logger.info(f"🌐 正在主动打开支付网关页面: {full_url}")
                            gateway_page = self.page.context.new_page()
                            gateway_page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                        except Exception as e:
                            logger.warning(f"主动打开支付网关异常: {e}")

                    target_page = gateway_page if gateway_page else self.page
                    if target_page != self.page:
                        target_page.bring_to_front()
                        try:
                            target_page.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass

                    # Check pre-QR gateway URL for orderid / bookingId
                    pre_gw_url = target_page.url or ""
                    for cand_url in (pre_gw_url, str(payment_url_js or "")):
                        m_pre = re.search(r"(?:orderid|bookingId|ReferenceNo)=([A-Za-z0-9\-_]+)", cand_url, re.IGNORECASE)
                        if m_pre and official_id == "PENDING_PAYMENT":
                            official_id = m_pre.group(1)

                    # 4.4 If on Fiuu payment checkout summary, click Pay button to render QR Code!
                    # On pay.fiuu.com, the button is #pay-now-nomultipayment ("Pay MYR ...")
                    try:
                        pay_now_btn = target_page.locator("#pay-now-nomultipayment, button.pay-button, button:has-text('Pay MYR'), button:has-text('Pay '), input[value*='Pay']").first
                        if pay_now_btn.is_visible(timeout=4000):
                            logger.info("💳 检测到 Fiuu 支付网关【Pay MYR】确认按钮，正在触发以生成真实 DuitNow QR 码...")
                            pay_now_btn.scroll_into_view_if_needed(timeout=2000)
                            pay_now_btn.click()
                            time.sleep(2.5)
                            try:
                                target_page.wait_for_load_state("networkidle", timeout=8000)
                            except Exception:
                                pass
                    except Exception as e:
                        logger.debug(f"检查 Fiuu pay-now 按钮跳过: {e}")

                    # Save gateway page HTML for audit & extract Fiuu transaction reference
                    try:
                        gw_html = target_page.content()
                        with open("data/gateway_page.html", "w", encoding="utf-8") as f:
                            f.write(gw_html)
                        if isinstance(gw_html, str):
                            m_k_gw = re.search(r"\b(K\d{10,14})\b", gw_html)
                            if m_k_gw and official_id == "PENDING_PAYMENT":
                                official_id = m_k_gw.group(1)
                            m_tx = re.search(r"checkTransaction\(['\"]([a-f0-9]{16,64})['\"]", gw_html, re.IGNORECASE)
                            if m_tx:
                                payment_ref = f"FIUU-{m_tx.group(1)[:12].upper()}"
                    except Exception:
                        pass

                    # 4.5 Polling loop for genuine QR Code (up to 15 seconds)
                    # Genuine QR code is at least 120x120 (filters out 80x80 logo icon)
                    logger.info(f"🔍 正在目标网关页面 [{target_page.url}] 定位 DuitNow 二维码...")
                    qr_found = False

                    for poll_sec in range(1, 16):
                        # Search target_page and all its frames
                        frames_to_check = target_page.frames
                        for frame in frames_to_check:
                            if qr_found:
                                break

                            qr_candidates = [
                                "canvas",
                                "svg:has(rect), svg:has(path)",
                                "img[src*='qr'], img[src*='QR'], img[src*='data:image']",
                                "#qrCode img, .qrcode img, .qr-code img, #qrImage, .qr-image img, #duitnow-qr",
                                ".qrcode, #qrcode, .qr-code, #qrCode",
                            ]

                            for selector in qr_candidates:
                                try:
                                    matches = frame.locator(selector)
                                    count = matches.count()
                                    for idx in range(count):
                                        loc = matches.nth(idx)
                                        if not loc.is_visible():
                                            continue
                                        box = loc.bounding_box()
                                        # Exclude tiny icons: real QR codes are >= 120x120
                                        if not box or box["width"] < 120 or box["height"] < 120:
                                            continue

                                        # We found a visible QR element with adequate dimensions!
                                        logger.info(
                                            f"📸 成功定位真实 DuitNow 二维码！Frame: [{frame.name or frame.url}], "
                                            f"选择器: '{selector}', 尺寸: {int(box['width'])}x{int(box['height'])} (耗时 {poll_sec}s)"
                                        )
                                        loc.scroll_into_view_if_needed(timeout=2000)
                                        time.sleep(0.5)
                                        loc.screenshot(path=qr_screenshot_path)
                                        if os.path.exists(qr_screenshot_path) and os.path.getsize(qr_screenshot_path) > 1000:
                                            qr_found = True
                                            has_qr = True
                                            break
                                except Exception:
                                    continue

                        if qr_found:
                            break

                        time.sleep(1.0)

                    # 4.6 Fallback: if no isolated QR element screenshot succeeded, screenshot target_page (Fiuu)
                    if not qr_found:
                        logger.info("ℹ️ 未能隔离单个二维码元素，截取网关页面完整内容作为凭据...")
                        target_page.screenshot(path=qr_screenshot_path, full_page=True)
                        has_qr = True

                    # Update booking ID & checkout URL
                    final_url = target_page.url
                    m_final = re.search(r"orderid=([A-Za-z0-9\-_]+)", final_url, re.IGNORECASE)
                    if not m_final:
                        m_final = re.search(r"bookingId=([A-Za-z0-9\-_]+)", final_url, re.IGNORECASE)
                    if m_final:
                        official_id = m_final.group(1)

                    if target_page != self.page and target_page.url.startswith("http"):
                        checkout_url = target_page.url
                    elif "payment" in final_url.lower() or "checkout" in final_url.lower() or "book" in final_url.lower():
                        checkout_url = final_url
                else:
                    if os.path.exists(screenshot_path):
                        shutil.copyfile(screenshot_path, qr_screenshot_path)
                        has_qr = True
            except Exception as e:
                logger.warning(f"调起 DuitNow QR 异常: {e}")
                if os.path.exists(screenshot_path):
                    shutil.copyfile(screenshot_path, qr_screenshot_path)
                    has_qr = True

            ret_info = dict(getattr(self, "last_return_trip_info", None) or {})
            if not ret_info.get("train_no") or not ret_info.get("departure_time"):
                try:
                    ret_card_meta = self.page.evaluate("""
                        () => {
                            const details = Array.from(document.querySelectorAll('.station_detail'));
                            const retBlock = details.find(d => (d.innerText || '').toLowerCase().includes('return'));
                            if (!retBlock) return null;
                            const text = retBlock.innerText || '';
                            const svcMatch = text.match(/([A-Za-z]+)\\s*-\\s*(\\d{3,5})/);
                            const timeMatch = text.match(/\\/\\s*(\\d{2}:\\d{2})/);
                            return {
                                train_class: svcMatch ? svcMatch[1].trim() : '',
                                train_no: svcMatch ? svcMatch[2].trim() : '',
                                departure_time: timeMatch ? timeMatch[1].trim() : ''
                            };
                        }
                    """)
                    if isinstance(ret_card_meta, dict):
                        for k, v in ret_card_meta.items():
                            if v and not ret_info.get(k):
                                ret_info[k] = v
                except Exception:
                    pass

            logger.info(f"🎉 KTMB 官方席位锁定完成！订单状态/编号: {official_id}, 应付金额: {payment_amount or 'N/A'}, 支付页面: {checkout_url}")
            return {
                "status": "SUCCESS",
                "booking_id": official_id,
                "payment_ref": payment_ref,
                "payment_amount": payment_amount,
                "selected_seat": getattr(self, "last_locked_seat", None),
                "outbound_seat": getattr(self, "last_locked_outbound_seat", None),
                "return_seat": getattr(self, "last_locked_return_seat", None) or ret_info.get("selected_seat"),
                "return_train_no": ret_info.get("train_no"),
                "return_train_class": ret_info.get("train_class"),
                "return_departure_time": ret_info.get("departure_time"),
                "payment_url": checkout_url,
                "payment_qr": qr_screenshot_path if has_qr else None
            }
        except Exception as exc:
            logger.error(f"❌ 官方乘车人表单提交异常: {exc}")
            raise RuntimeError(f"KTMB 官方下单失败 ({exc})。请确认已登录 KTMB 且座位有效。")

    def scrape_upcoming_bookings(self) -> List[Dict[str, Any]]:
        """
        Navigates to KTMB KITS /Booking/UpcomingList and scrapes all confirmed/paid
        upcoming tickets, including official K-prefixed Booking No and T-prefixed Ticket Nos.
        """
        try:
            target_url = f"{self.base_url}/Booking/UpcomingList"
            self.page.goto(target_url, timeout=25000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            self.dismiss_modals()

            if "/Account/Login" in (self.page.url or ""):
                raise RuntimeError(f"{self.SESSION_EXPIRED_MARKER}: 访问 /Booking/UpcomingList 时未处于登录状态")

            bookings = self.page.evaluate("""
                () => {
                    const cards = Array.from(document.querySelectorAll('.my-booking'));
                    return cards.map(card => {
                        const header = card.querySelector('.my-ticket');
                        const headerText = header ? (header.innerText || '') : (card.innerText || '');
                        const bMatch = headerText.match(/\\b(K\\d{10,14})\\b/);
                        const bookingNo = bMatch ? bMatch[1] : '';
                        const depMatch = headerText.match(/Depart\\s+([0-9]{1,2}\\s+[A-Za-z]{3,9}\\s+[0-9]{4})/i);
                        const retMatch = headerText.match(/Return\\s+([0-9]{1,2}\\s+[A-Za-z]{3,9}\\s+[0-9]{4})/i);
                        const routeEl = header ? header.querySelector('.d-flex.align-items-center') : null;
                        const route = routeEl ? (routeEl.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                        const printLink = header ? header.querySelector('a[href*="PrintTicket"]') : null;
                        const printUrl = printLink ? printLink.href : (bookingNo ? `https://online.ktmb.com.my/Booking/PrintTicket?p=${bookingNo}` : '');

                        const legs = Array.from(card.querySelectorAll('.bookingLogDetail')).map(leg => {
                            const legRouteEl = leg.querySelector('b');
                            const legRoute = legRouteEl ? (legRouteEl.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                            const detailDivs = Array.from(leg.querySelectorAll('.ticket-detail-view > div')).map(d => (d.innerText || '').trim());
                            const train = detailDivs[0] || '';
                            const datetime = detailDivs[1] || '';

                            const tickets = [];
                            const ticketTables = Array.from(leg.querySelectorAll('.col-xl-5 table'));
                            for (const tbl of ticketTables) {
                                const tNoEl = tbl.querySelector('.seat_no');
                                const tInfoEl = tbl.querySelector('.seat_info');
                                const rawText = (tbl.innerText || '').replace(/\\s+/g, ' ').trim();
                                const seatMatch = rawText.match(/\\b([A-Z0-9]{1,3}\\s*\\/\\s*[0-9]{1,2}[A-Z])\\b/);
                                const tNo = tNoEl ? (tNoEl.innerText || '').trim() : '';
                                const pInfo = tInfoEl ? (tInfoEl.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                                if (tNo || seatMatch) {
                                    tickets.push({
                                        seat: seatMatch ? seatMatch[1].replace(/\\s+/g, ' ') : '',
                                        ticket_no: tNo,
                                        passenger: pInfo
                                    });
                                }
                            }
                            return {
                                route: legRoute,
                                train: train,
                                datetime: datetime,
                                tickets: tickets
                            };
                        });

                        return {
                            booking_no: bookingNo,
                            route: route,
                            depart_date: depMatch ? depMatch[1] : '',
                            return_date: retMatch ? retMatch[1] : '',
                            print_url: printUrl,
                            legs: legs
                        };
                    }).filter(b => b.booking_no || b.legs.length > 0);
                }
            """)
            return list(bookings) if isinstance(bookings, list) else []
        except Exception as e:
            logger.warning(f"查询 KTMB UpcomingList 待出行车票异常: {e}")
            raise

