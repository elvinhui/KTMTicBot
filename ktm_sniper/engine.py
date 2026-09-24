import time
import uuid
import logging
import threading
from typing import Optional, Dict, Any, List
from ktm_sniper.models import SniperTaskConfig, TripInfo, TaskStatus
from ktm_sniper.poller import AdaptivePoller
from ktm_sniper.checker import KTMTicketChecker
from ktm_sniper.reserver import KTMSeatReserver
from ktm_sniper.notifier import TelegramTicketNotifier
from ktm_sniper.storage import TaskRepository
from ktm_sniper.browser.driver import KTMBrowserDriver
from ktm_sniper.network.circuit_breaker import CircuitBreakerOpenException

logger = logging.getLogger(__name__)

class KTMSniperEngine:
    """
    Main coordinator engine running the monitoring, jitter polling,
    seat reservation, and Telegram alerting loop.
    """
    SESSION_CHECK_INTERVAL = 10  # Check login state every N cycles

    def __init__(
        self,
        task: SniperTaskConfig,
        poller: Optional[AdaptivePoller] = None,
        checker: Optional[KTMTicketChecker] = None,
        reserver: Optional[KTMSeatReserver] = None,
        notifier: Optional[TelegramTicketNotifier] = None,
        repository: Optional[TaskRepository] = None,
        browser_driver: Optional[KTMBrowserDriver] = None,
        authenticator=None,
        max_cycles: Optional[int] = None
    ):
        self.task = task
        if not self.task.task_id:
            self.task.task_id = f"task-{uuid.uuid4().hex[:8]}"

        self.poller = poller or AdaptivePoller()
        if checker is None:
            try:
                from ktm_sniper.network.session import KITSClient
                self.checker = KTMTicketChecker(session=KITSClient())
            except Exception:
                self.checker = KTMTicketChecker()
        else:
            self.checker = checker
        self.reserver = reserver or KTMSeatReserver()
        self.notifier = notifier
        self.repository = repository
        self.browser_driver = browser_driver
        self.authenticator = authenticator
        self.max_cycles = max_cycles
        self.consecutive_errors = 0
        self._outbound_result: Optional[Dict[str, Any]] = None
        self.is_paused: bool = False
        self.total_cycles: int = 0
        self._browser_needs_update: bool = False
        self._lock = threading.Lock()
        self.last_found_trips: List[Any] = []
        self.selected_trip: Optional[Any] = None
        self.available_seats_cache: Dict[str, Any] = {}
        self.require_confirmation: bool = getattr(task, "require_confirmation", False)

    def fetch_seats_layout(self, train_no: str) -> Dict[str, Dict[str, List[str]]]:
        if self.browser_driver:
            return self.browser_driver.fetch_seats_layout(train_no, task=self.task)
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

    def execute_real_booking(self, trip: Any, seat_no: str = "auto") -> Dict[str, Any]:
        train_no = getattr(trip, "train_no", trip.get("train_no") if isinstance(trip, dict) else "9044")
        if self.browser_driver and not self.browser_driver.is_logged_in() and self.authenticator:
            logger.info("🔐 正在为官方订座执行认证登录...")
            self.authenticator.ensure_authenticated(self.browser_driver.page)
        return self.reserver.reserve_seat(
            trip_id=train_no,
            seat_preference=seat_no,
            passengers=self.task.passengers,
            driver=self.browser_driver,
            task=self.task
        )

    def update_task(self, new_task: SniperTaskConfig):
        """
        Thread-safely hot-updates the target sniper task criteria.
        Safe to call from background threads (e.g. TelegramCommandListener).
        """
        with self._lock:
            old_desc = f"{self.task.origin} -> {self.task.destination} ({self.task.date})"
            route_changed = (
                self.task.origin != new_task.origin or
                self.task.destination != new_task.destination or
                self.task.date != new_task.date
            )
            self.task = new_task
            self.consecutive_errors = 0
            if route_changed:
                self._browser_needs_update = True
            if self.repository:
                self.repository.save_task(new_task)
                self.repository.log_event(
                    new_task.task_id,
                    f"Task updated from {old_desc} to {new_task.origin} -> {new_task.destination} ({new_task.date})"
                )

    def get_status_summary(self) -> Dict[str, Any]:
        """
        Returns an operational summary for Telegram remote monitoring.
        """
        with self._lock:
            status_text = "PAUSED (已暂停)" if self.is_paused else ("MONITORING (监控中)" if self.task.status == TaskStatus.MONITORING else str(self.task.status.value))
            return {
                "status": status_text,
                "is_paused": self.is_paused,
                "total_cycles": self.total_cycles,
                "origin": self.task.origin,
                "destination": self.task.destination,
                "date": self.task.date,
                "time_window": f"{self.task.time_from} - {self.task.time_to}",
                "is_round_trip": self.task.is_round_trip,
                "return_date": self.task.return_date,
                "return_time_window": f"{self.task.return_time_from} - {self.task.return_time_to}" if self.task.return_date else "N/A",
                "passengers": [f"{p.name} ({p.masked_id})" for p in self.task.passengers]
            }


    def step(self) -> Optional[Dict[str, Any]]:
        """
        Executes a single check-and-reserve evaluation cycle in the main thread.
        """
        if self.repository:
            self.repository.update_task_status(self.task.task_id, TaskStatus.MONITORING)

        leg_label = "【去程】" if self.task.leg_type == "OUTBOUND" else ("【返程】" if self.task.leg_type == "RETURN" else "")

        # 0. Sync browser criteria if hot-updated by background Telegram thread
        if self._browser_needs_update and self.browser_driver:
            with self._lock:
                self._browser_needs_update = False
                task_snapshot = self.task
            try:
                self.browser_driver.fill_search_criteria(task_snapshot)
                self.browser_driver.trigger_search()
            except Exception as e:
                logger.warning(f"Failed to update browser criteria in main thread: {e}")

        # 0b. Periodic session health check — re-authenticate if session expired
        if (self.authenticator and self.browser_driver
                and self.total_cycles > 0
                and self.total_cycles % self.SESSION_CHECK_INTERVAL == 0):
            try:
                if not self.browser_driver.is_logged_in():
                    logger.warning("⚠️ KITS Session 已过期，正在自动重新登录...")
                    self.authenticator.ensure_authenticated(
                        page=self.browser_driver.page,
                        notifier=self.notifier,
                    )
                    # Re-navigate to booking page after re-login
                    self.browser_driver.navigate_to_booking()
                    self.browser_driver.fill_search_criteria(self.task)
            except Exception as e:
                logger.warning(f"Session re-auth check failed: {e}")

        # 1. Fetch available trips (curl_cffi HTTP polling is preferred for monitoring)
        trips: List[TripInfo] = []
        try:
            trips = self.checker.find_matching_trips(self.task)
        except Exception as e:
            logger.warning(f"HTTP checker query error ({e}), falling back to browser scraping if available...")
            if self.browser_driver:
                try:
                    scraped = self.browser_driver.scrape_trips()
                    trips = [t for t in scraped if self.task.matches_trip(t)]
                except Exception as b_err:
                    logger.error(f"Browser scraping error: {b_err}")
                    self.consecutive_errors += 1
                    return None
            else:
                self.consecutive_errors += 1
                return None

        # 2. Evaluate matches
        if not trips:
            if self.repository:
                self.repository.log_event(self.task.task_id, f"{leg_label}Polled trips: No matching seats found.")
            return None

        self.last_found_trips = trips

        # Interactive Confirmation Mode: send list to Telegram and wait for user reply!
        if self.require_confirmation and self.notifier and len(trips) > 0:
            logger.info(f"{leg_label} Found {len(trips)} available trips. Sending trip choices to Telegram...")
            self.notifier.send_trip_options(
                trips=trips,
                origin=self.task.origin,
                destination=self.task.destination,
                date=self.task.date
            )
            self.is_paused = True
            if self.repository:
                self.repository.log_event(
                    self.task.task_id,
                    f"{leg_label} Sent {len(trips)} candidate trips to Telegram. Waiting for user /book confirmation."
                )
            return {
                "status": "WAITING_CONFIRMATION",
                "trips": trips
            }

        # Select the best matching trip (first available)
        target_trip = trips[0]
        logger.info(f"{leg_label} Target trip found: {target_trip.train_no} with {target_trip.available_seats} seats!")

        if self.repository:
            self.repository.log_event(
                self.task.task_id,
                f"{leg_label} Seats found on {target_trip.train_no} ({target_trip.available_seats} seats). Proceeding to lock."
            )

        reservation_result = None
        booking_id = "MANUAL-ALERT"

        # 3. Auto-reserve if enabled and passengers provided
        if self.task.auto_reserve and self.task.passengers:
            if self.browser_driver and not self.browser_driver.is_logged_in() and self.authenticator:
                logger.info("🔐 正在为官方订座执行认证登录...")
                self.authenticator.ensure_authenticated(self.browser_driver.page)

            primary_passenger = self.task.passengers[0]
            trip_id = target_trip.trip_id or target_trip.train_no
            reservation_result = self.reserver.reserve_seat(
                trip_id=trip_id,
                seat_preference=self.task.seat_preference,
                passenger=primary_passenger,
                passengers=self.task.passengers,
                driver=self.browser_driver,
                task=self.task
            )
            booking_id = reservation_result.get("booking_id", "KITS-PENDING")

        # 4. Capture screenshot if browser is active
        screenshot_file = None
        if self.browser_driver:
            try:
                screenshot_file = f"reservation_{self.task.leg_type.lower()}_success.png"
                self.browser_driver.take_screenshot(screenshot_file)
            except Exception as e:
                logger.warning(f"Failed to capture screenshot: {e}")

        # 5. Notify via Telegram
        if self.notifier:
            trip_details = {
                "train_no": target_trip.train_no,
                "train_class": target_trip.train_class,
                "origin": self.task.origin,
                "destination": self.task.destination,
                "departure_time": f"{self.task.date} {target_trip.departure_time}"
            }
            passenger_name = self.task.passengers[0].name if self.task.passengers else "Passenger"
            raw_id = self.task.passengers[0].id_number if self.task.passengers else "N/A"

            if screenshot_file:
                caption = self.notifier.format_message(
                    booking_id, trip_details, passenger_name, raw_id, passengers=self.task.passengers
                )
                self.notifier.send_photo_alert(screenshot_file, caption=caption)
            else:
                self.notifier.send_alert(
                    booking_id, trip_details, passenger_name, raw_id, passengers=self.task.passengers
                )

        # 6. Update repository status
        if self.repository:
            self.repository.update_task_status(self.task.task_id, TaskStatus.RESERVED, booking_id=booking_id)

        return reservation_result or {"status": "SUCCESS", "booking_id": booking_id, "trip": target_trip}

    def run(self) -> Optional[Dict[str, Any]]:
        """
        Runs the polling loop with adaptive jitter and error backoff.
        Seamlessly coordinates both Outbound and Return legs when is_round_trip is configured.
        """
        cycles = 0
        while True:
            if self.is_paused:
                time.sleep(1.0)
                continue

            cycles += 1
            self.total_cycles += 1
            if self.max_cycles and cycles > self.max_cycles:
                logger.info("Reached maximum cycles limit. Stopping.")
                break

            try:
                result = self.step()
                if result:
                    if result.get("status") == "WAITING_CONFIRMATION":
                        continue
                    if self.task.is_round_trip:
                        logger.info(f"🎉 去程车票锁定成功 ({result.get('booking_id')})！正在自动无缝切换至返程票守护...")
                        self._outbound_result = result
                        return_task = self.task.get_return_task()
                        if self.repository:
                            self.repository.save_task(return_task)
                            self.repository.log_event(
                                self.task.task_id,
                                f"Outbound secured ({result.get('booking_id')}). Switching to return task {return_task.task_id}."
                            )
                        if self.browser_driver:
                            try:
                                self.browser_driver.fill_search_criteria(return_task)
                                self.browser_driver.trigger_search()
                            except Exception as e:
                                logger.warning(f"Failed to update browser for return trip: {e}")
                        self.task = return_task
                        self.consecutive_errors = 0
                        continue
                    elif self._outbound_result is not None:
                        logger.info(f"🎉 返程车票锁定成功 ({result.get('booking_id')})！往返双程车票均已全部锁定！")
                        if self.max_cycles is not None:
                            return {
                                "status": "SUCCESS",
                                "is_round_trip": True,
                                "outbound": self._outbound_result,
                                "return": result
                            }
                        self.is_paused = True
                        logger.info("⏸️ 双程车票均已锁定！守护已自动挂起为 PAUSED。")
                        while self.is_paused:
                            time.sleep(3)
                        continue
                    else:
                        logger.info(f"🎉 车票已成功锁定 ({result.get('booking_id')})！已发送 Telegram 付款直达链接！")
                        if self.max_cycles is not None:
                            return result
                        self.is_paused = True
                        logger.info("⏸️ 车票已成功锁定！守护引擎已自动挂起为 PAUSED，等待用户完成付款。发送 /resume 可随时开启下一轮守护。")
                        while self.is_paused:
                            time.sleep(3)
                        continue
                self.consecutive_errors = 0
            except CircuitBreakerOpenException as e:
                logger.warning(f"Circuit breaker open: {e}. Backing off.")
                self.consecutive_errors += 1
                delay = self.poller.get_backoff_delay(self.consecutive_errors)
                time.sleep(delay)
                continue
            except Exception as e:
                logger.error(f"Encountered cycle error: {e}")
                self.consecutive_errors += 1
                delay = self.poller.get_backoff_delay(self.consecutive_errors)
                time.sleep(delay)
                continue

            delay = self.poller.get_next_delay()
            time.sleep(delay)

        return None
