import os
from dataclasses import dataclass

def _load_env_file(path: str = ".env"):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

_load_env_file()

@dataclass(frozen=True)
class Settings:
    BASE_URL: str = os.getenv("KITS_BASE_URL", "https://online.ktmb.com.my")
    DEFAULT_TIMEOUT: float = float(os.getenv("KITS_TIMEOUT", "15.0"))
    HEADLESS: bool = os.getenv("KTM_HEADLESS", "true").lower() in ("true", "1", "yes")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    DEFAULT_POLL_INTERVAL: float = float(os.getenv("POLL_INTERVAL", "4.5"))
    DEFAULT_JITTER: float = float(os.getenv("POLL_JITTER", "1.5"))
    CIRCUIT_BREAKER_FAILURES: int = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
    CIRCUIT_BREAKER_TIMEOUT: float = float(os.getenv("CB_RECOVERY_TIMEOUT", "15.0"))
    # KTMB KITS Login Credentials
    KTM_EMAIL: str = os.getenv("KTM_EMAIL", "")
    KTM_PASSWORD: str = os.getenv("KTM_PASSWORD", "")

settings = Settings()
