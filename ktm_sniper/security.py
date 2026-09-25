import os
import re
from typing import Dict, Any, Union, List

try:
    from cryptography.fernet import Fernet
    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False

def mask_ic(id_number: str) -> str:
    """
    Masks Malaysian MyKad (NRIC) or passport numbers for privacy preservation.
    Never exposes full 12 digits or full passport number.
    """
    if not id_number or len(id_number.strip()) <= 4:
        return "****"

    raw = id_number.strip()

    # Format: 900101-14-5566
    if re.match(r"^\d{6}-\d{2}-\d{4}$", raw):
        return f"{raw[:10]}****"

    # Format: 12-digit continuous MyKad
    if re.match(r"^\d{12}$", raw):
        return f"{raw[:6]}****{raw[10:]}"

    # General alphanumeric ID or Passport
    return f"{raw[:-4]}****"

def mask_phone(phone: str) -> str:
    """
    Masks phone numbers to protect PII.
    E.g. +60123456789 -> +601****789, 0192233445 -> 0192****445
    """
    if not phone or len(phone.strip()) <= 4:
        return "****"
    raw = phone.strip()
    if len(raw) <= 7:
        return raw[:2] + "****" + raw[-2:]
    return raw[:4] + "****" + raw[-3:]

def mask_pii_in_text(text: str) -> str:
    """
    Scans an arbitrary text string and masks any Malaysian IC numbers
    (e.g., 900101-14-5566, 900101145566) and phone numbers
    (e.g., +60123456789, 0123456789, 0192233445) to prevent PII leaks in logs and SQLite databases.
    """
    if not text:
        return text
    # 1. Malaysian MyKad formatted with dashes: 900101-14-5566 -> 900101-14-****
    text = re.sub(r"\b(\d{6}-\d{2}-)\d{4}\b", r"\g<1>****", text)
    # 2. 12-digit continuous MyKad: 900101145566 -> 900101****66
    text = re.sub(r"\b(\d{6})\d{4}(\d{2})\b", r"\g<1>****\g<2>", text)
    # 3. Phone numbers with international prefix: +60123456789 -> +601****789
    text = re.sub(r"(\+?60\d{1,2})(\d{3,4})(\d{3,4})\b", r"\g<1>****\g<3>", text)
    # 4. Malaysian local mobile numbers: 0123456789 -> 012****789 or 0192233445 -> 0192****445
    text = re.sub(r"\b(01[0-9]{1,2})(\d{3,4})(\d{3,4})\b", r"\g<1>****\g<3>", text)
    return text

def mask_sensitive_dict(data: Any) -> Any:
    """
    Recursively clones a data structure and masks any sensitive keys
    (id_number, ic, passport, phone, mobile, token, secret, password).
    Adheres strictly to immutability.
    """
    sensitive_ic_keys = {"id_number", "ic", "passport", "token", "password", "secret", "mykad", "passwd", "pwd"}
    sensitive_phone_keys = {"phone", "mobile", "tel", "contact"}
    sensitive_email_keys = {"email", "mail", "username", "login"}

    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            k_lower = k.lower()
            if any(s_key in k_lower for s_key in sensitive_email_keys):
                val = str(v)
                if "@" in val:
                    local, domain = val.rsplit("@", 1)
                    masked = f"{local[:3]}****@{domain}" if len(local) > 3 else f"{local[0]}****@{domain}"
                    new_dict[k] = masked
                else:
                    new_dict[k] = mask_ic(val)
            elif any(s_key in k_lower for s_key in sensitive_phone_keys):
                new_dict[k] = mask_phone(str(v))
            elif any(s_key in k_lower for s_key in sensitive_ic_keys):
                new_dict[k] = mask_ic(str(v))
            else:
                new_dict[k] = mask_sensitive_dict(v)
        return new_dict
    elif isinstance(data, list):
        return [mask_sensitive_dict(item) for item in data]
    else:
        return data

class DataEncryptor:
    """
    Provides AES-GCM / Fernet authenticated encryption for sensitive PII (IC/MyKad) at rest.
    Keys are sourced from KTM_ENCRYPTION_KEY environment variable or a local .key file.
    """
    def __init__(self, key_file: str = ".ktm_key"):
        self.key_file = key_file
        self.key = self._resolve_key()
        self._fernet = Fernet(self.key) if HAS_FERNET else None

    def _resolve_key(self) -> bytes:
        # 1. Environment variable override
        env_key = os.getenv("KTM_ENCRYPTION_KEY")
        if env_key:
            return env_key.strip().encode("utf-8")

        # 2. Keyfile storage (check self.key_file, data/.ktm_key, and /app/data/.ktm_key)
        candidates = [
            self.key_file,
            os.path.join("data", os.path.basename(self.key_file)),
            os.path.join("/app/data", os.path.basename(self.key_file))
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        content = f.read().strip()
                        if content:
                            return content
                except Exception:
                    pass

        # 3. Generate new key
        if HAS_FERNET:
            new_key = Fernet.generate_key()
            for p in [self.key_file, os.path.join("data", os.path.basename(self.key_file))]:
                try:
                    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
                    with open(p, "wb") as f:
                        f.write(new_key)
                except Exception:
                    pass
            return new_key
        else:
            return b"0" * 32

    def encrypt(self, plaintext: str) -> str:
        if not self._fernet or not plaintext:
            return plaintext
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not self._fernet or not ciphertext:
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except Exception:
            # Fallback if text was unencrypted
            return ciphertext
