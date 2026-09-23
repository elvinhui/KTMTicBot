import logging
import time
from typing import Optional
from datetime import datetime

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import requests as cffi_requests

from ktm_sniper.network.kits_parser import (
    StationIndex,
    parse_stations,
    parse_trip_html,
    extract_hidden_fields,
    extract_csrf,
    ParsedTrip,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://online.ktmb.com.my"
_SEARCH_DATA_TTL_SECONDS = 600  # Re-initialize session after 10 minutes


class KITSClient:
    """
    HTTP session wrapper for KTMB KITS web portal.
    Implements the 3-step curl_cffi flow for unauthenticated trip availability polling:

      Step 1: GET /  -> parse jsStations[] + groupedStations[] + CSRF token
      Step 2: POST /Trip -> form submit -> extract SearchData/FormValidationCode
      Step 3: POST /Trip/Trip -> AJAX poll -> parse train HTML fragment

    SearchData is cached per (origin, destination) combination and refreshed
    every _SEARCH_DATA_TTL_SECONDS to avoid stale tokens.

    Falls back to standard requests if curl_cffi is unavailable.
    """

    def __init__(self, impersonate: str = "chrome124", timeout: float = 15.0, base_url: str = BASE_URL):
        self.impersonate = impersonate
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.session = self._create_session()
        self._station_index: Optional[StationIndex] = None
        self._homepage_csrf: str = ""
        self._search_cache: dict = {}  # key: (origin, dest) -> {SearchData, FormValidationCode, csrf, expires_at}
        self._initialized: bool = False

    # ── Session factory ───────────────────────────────────────────────

    def _create_session(self):
        if HAS_CURL_CFFI:
            try:
                session = cffi_requests.Session(impersonate=self.impersonate)
            except Exception as e:
                logger.warning(f"curl_cffi session creation failed ({e}), falling back to requests")
                import requests
                session = requests.Session()
        else:
            import requests
            session = requests.Session()

        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ms;q=0.8",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        })
        return session

    # ── Step 1: Initialize ────────────────────────────────────────────

    def initialize(self) -> bool:
        """
        GET homepage, parse station index and CSRF token.
        Must be called once before search_trips().
        Returns True on success.
        """
        try:
            resp = self.session.get(self.base_url + "/", timeout=self.timeout)
            if hasattr(resp, "raise_for_status"):
                resp.raise_for_status()
            self._station_index = parse_stations(resp.text)
            self._homepage_csrf = extract_csrf(resp.text)
            self._initialized = True
            logger.info(f"KITSClient initialized: {len(self._station_index.name_to_id)} stations loaded")
            return True
        except Exception as exc:
            logger.error(f"KITSClient initialization failed: {exc}")
            return False

    # ── Step 2: POST /Trip ────────────────────────────────────────────

    def _post_trip_form(self, from_id: str, from_data: str, to_id: str, to_data: str,
                        date_display: str, pax: int, csrf: str) -> dict:
        """
        POST /Trip form submission. Returns extracted hidden fields dict.
        Raises RuntimeError on HTTP error or missing SearchData.
        """
        form = {
            "FromStationData":            from_data,
            "ToStationData":              to_data,
            "FromStationId":              from_id,
            "ToStationId":                to_id,
            "OnwardDate":                 date_display,
            "ReturnDate":                 "",
            "PassengerCount":             str(pax),
            "__RequestVerificationToken": csrf,
        }
        resp = self.session.post(
            self.base_url + "/Trip",
            data=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer":      self.base_url + "/",
                "Accept":       "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=self.timeout,
            allow_redirects=True,
        )
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
        fields = extract_hidden_fields(resp.text)
        if not fields.get("SearchData"):
            raise RuntimeError("SearchData not found in /Trip response — session may have expired")
        return fields

    # ── Step 3: POST /Trip/Trip ───────────────────────────────────────

    def _fetch_trip_list(self, search_data: str, form_val_code: str,
                         date_iso: str, csrf_header: str) -> list[ParsedTrip]:
        """
        POST /Trip/Trip AJAX call. Returns list of ParsedTrip.
        """
        payload = {
            "SearchData":            search_data,
            "FormValidationCode":    form_val_code,
            "DepartDate":            date_iso,
            "IsReturn":              False,
            "BookingTripSequenceNo": 1,
        }
        resp = self.session.post(
            self.base_url + "/Trip/Trip",
            json=payload,
            headers={
                "Accept":                   "application/json, text/javascript, */*; q=0.01",
                "Content-Type":             "application/json",
                "X-Requested-With":         "XMLHttpRequest",
                "Referer":                  self.base_url + "/Trip",
                "RequestVerificationToken": csrf_header,
            },
            timeout=self.timeout,
        )
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
        data = resp.json()
        if not data.get("status"):
            msgs = data.get("messages", [])
            logger.warning(f"/Trip/Trip returned status=false: {msgs}")
            return []
        return parse_trip_html(data.get("data", ""))

    # ── Public API ────────────────────────────────────────────────────

    def search_trips(self, origin: str, destination: str,
                     date: str, passengers: int = 1) -> list[ParsedTrip]:
        """
        Full 3-step KITS trip search. Returns list[ParsedTrip].

        `date` must be in 'YYYY-MM-DD' format.
        Uses cached SearchData token when possible (same origin/destination,
        token not expired) and only re-initializes when necessary.
        """
        if not self._initialized:
            if not self.initialize():
                return []

        # Resolve station IDs and StationData blobs
        try:
            from_id, from_data = self._station_index.resolve(origin)
            to_id,   to_data   = self._station_index.resolve(destination)
        except (KeyError, AttributeError) as exc:
            logger.error(str(exc))
            return []

        # Convert date: 'YYYY-MM-DD' -> '05 Oct 2026' (display) and keep ISO
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            date_display = dt.strftime("%d %b %Y")  # "05 Oct 2026"
            date_iso     = date                       # "2026-10-05"
        except ValueError:
            logger.error(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")
            return []

        # Check cache
        cache_key = (origin.upper(), destination.upper())
        cached    = self._search_cache.get(cache_key)
        now       = time.monotonic()

        if cached and cached["expires_at"] > now:
            search_data   = cached["SearchData"]
            form_val_code = cached["FormValidationCode"]
            csrf_header   = cached["csrf"]
            logger.debug(f"Using cached SearchData for {origin}->{destination}")
        else:
            # Step 2: POST /Trip to get fresh SearchData
            logger.debug(f"Fetching fresh SearchData for {origin}->{destination} on {date_display}")
            try:
                fields = self._post_trip_form(
                    from_id, from_data, to_id, to_data,
                    date_display, passengers, self._homepage_csrf,
                )
            except Exception as exc:
                logger.error(f"POST /Trip failed: {exc}")
                self._initialized = False
                return []

            search_data   = fields["SearchData"]
            form_val_code = fields["FormValidationCode"]
            csrf_header   = fields["__RequestVerificationToken"]

            self._search_cache[cache_key] = {
                "SearchData":          search_data,
                "FormValidationCode":  form_val_code,
                "csrf":                csrf_header,
                "expires_at":          now + _SEARCH_DATA_TTL_SECONDS,
            }

        # Step 3: POST /Trip/Trip
        try:
            return self._fetch_trip_list(search_data, form_val_code, date_iso, csrf_header)
        except Exception as exc:
            logger.error(f"POST /Trip/Trip failed: {exc}")
            self._search_cache.pop(cache_key, None)
            return []

    def get(self, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(url, **kwargs)

    def close(self):
        if hasattr(self.session, "close"):
            self.session.close()
