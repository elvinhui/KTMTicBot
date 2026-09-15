import os
import pytest
from ktm_sniper.security import mask_ic, mask_phone, DataEncryptor, mask_sensitive_dict
from ktm_sniper.models import Passenger

def test_mask_ic():
    # Malaysian MyKad 12-digit
    assert mask_ic("900101145566") == "900101****66"
    # Malaysian MyKad with dashes
    assert mask_ic("900101-14-5566") == "900101-14-****"
    # Passport or short ID
    assert mask_ic("A1234567") == "A123****"
    assert mask_ic("123") == "****"
    assert mask_ic("") == "****"

def test_mask_phone():
    assert mask_phone("+60123456789") == "+601****789"
    assert mask_phone("0192233445") == "0192****445"
    assert mask_phone("123") == "****"
    assert mask_phone("") == "****"

def test_passenger_repr_does_not_leak_ic_or_phone():
    p = Passenger(
        name="Ahmad Faiz",
        id_number="920101-14-5566",
        gender="Male",
        phone="0192233445"
    )
    # The repr should show masked ID and masked phone, NOT the raw values
    repr_str = repr(p)
    assert "920101-14-5566" not in repr_str
    assert "0192233445" not in repr_str
    assert "920101-14-****" in repr_str
    assert "0192****445" in repr_str

def test_data_encryptor_roundtrip(tmp_path):
    key_file = tmp_path / "test.key"
    encryptor = DataEncryptor(key_file=str(key_file))

    secret = "900101-14-5566"
    encrypted = encryptor.encrypt(secret)
    assert encrypted != secret
    assert "900101" not in encrypted

    decrypted = encryptor.decrypt(encrypted)
    assert decrypted == secret

def test_mask_sensitive_dict():
    raw_data = {
        "name": "John Doe",
        "id_number": "900101145566",
        "phone": "+60123456789",
        "nested": {
            "ic": "920101-14-5566",
            "mobile": "0192233445",
            "safe_field": "safe"
        }
    }
    masked = mask_sensitive_dict(raw_data)
    assert "900101145566" not in str(masked)
    assert "920101-14-5566" not in str(masked)
    assert "+60123456789" not in str(masked)
    assert "0192233445" not in str(masked)
    assert masked["name"] == "John Doe"
    assert masked["nested"]["safe_field"] == "safe"

def test_mask_pii_in_text():
    from ktm_sniper.security import mask_pii_in_text

    raw_command = '/add_passenger "Siti Nurhaliza" 950202-10-5566 0198765432 Female'
    masked_cmd = mask_pii_in_text(raw_command)
    assert "950202-10-5566" not in masked_cmd
    assert "0198765432" not in masked_cmd
    assert "950202-10-****" in masked_cmd
    assert "0198****432" in masked_cmd
    assert "Siti Nurhaliza" in masked_cmd

    raw_log = "User with IC 920101145566 and phone +60123456789 requested lock"
    masked_log = mask_pii_in_text(raw_log)
    assert "920101145566" not in masked_log
    assert "+60123456789" not in masked_log
    assert "920101****66" in masked_log
    assert "+6012****789" in masked_log

def test_passenger_to_dict_mask_pii():
    p = Passenger(name="Tan", id_number="900101-14-5566", gender="Male", phone="0123456789")
    full_dict = p.to_dict(mask_pii=False)
    assert full_dict["id_number"] == "900101-14-5566"
    assert full_dict["phone"] == "0123456789"

    masked_dict = p.to_dict(mask_pii=True)
    assert masked_dict["id_number"] == "900101-14-****"
    assert masked_dict["phone"] == "0123****789"
