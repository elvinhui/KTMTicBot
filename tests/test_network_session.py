import pytest
from unittest.mock import MagicMock, patch
from ktm_sniper.network.session import KITSClient

def test_kits_client_creation():
    client = KITSClient(timeout=10.0)
    assert client.timeout == 10.0
    assert "User-Agent" in client.session.headers
    assert "online.ktmb.com.my" in client.session.headers["Origin"]
    client.close()
