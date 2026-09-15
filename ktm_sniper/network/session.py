import logging
from typing import Optional, Dict, Any

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import requests as cffi_requests

logger = logging.getLogger(__name__)

class KITSClient:
    """
    Session wrapper utilizing curl_cffi with Chrome 120 JA3/JA4 TLS fingerprint impersonation,
    falling back to standard requests when cffi is unavailable.
    """
    def __init__(self, impersonate: str = "chrome120", timeout: float = 15.0):
        self.impersonate = impersonate
        self.timeout = timeout
        self.session = self._create_session()

    def _create_session(self):
        if HAS_CURL_CFFI:
            try:
                session = cffi_requests.Session(impersonate=self.impersonate)
            except Exception as e:
                logger.warning(f"Failed to create curl_cffi session with impersonate={self.impersonate}: {e}. Falling back.")
                import requests
                session = requests.Session()
        else:
            import requests
            session = requests.Session()

        # Inject realistic browser headers
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ms;q=0.8",
            "Origin": "https://online.ktmb.com.my",
            "Referer": "https://online.ktmb.com.my/"
        })
        return session

    def get(self, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(url, **kwargs)

    def close(self):
        if hasattr(self.session, "close"):
            self.session.close()
