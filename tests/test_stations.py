import pytest
from ktm_sniper.stations import KTMStationRegistry

def test_legacy_station_lookup():
    assert KTMStationRegistry.get_code("KL Sentral") == "KLS"
    assert KTMStationRegistry.get_code("Padang Besar") == "PDB"
    assert KTMStationRegistry.get_code("  ipoh  ") == "IPH"
    assert KTMStationRegistry.get_code("Butterworth") == "BTW"
    assert KTMStationRegistry.get_code("JB Sentral") == "JBS"

def test_extended_stations():
    assert KTMStationRegistry.get_code("Gemas") == "GMS"
    assert KTMStationRegistry.get_code("Taiping") == "TPG"
    assert KTMStationRegistry.get_code("Alor Setar") == "ASR"
    assert KTMStationRegistry.get_code("Seremban") == "SBN"
    assert KTMStationRegistry.get_code("Kluang") == "KLU"
    assert KTMStationRegistry.get_code("Bukit Mertajam") == "BMT"
    assert KTMStationRegistry.get_code("Parit Buntar") == "PBR"
    assert KTMStationRegistry.get_code("Nibong Tebal") == "NTB"

def test_station_aliases():
    # Alias: Penang -> Butterworth
    code, name = KTMStationRegistry.resolve_station("Penang")
    assert code == "BTW"
    assert name == "BUTTERWORTH"

    # Alias: KL -> KL Sentral
    code, name = KTMStationRegistry.resolve_station("KL")
    assert code == "KLS"
    assert name == "KL SENTRAL"

    # Alias: BM -> Bukit Mertajam
    code, name = KTMStationRegistry.resolve_station("BM")
    assert code == "BMT"
    assert name == "BUKIT MERTAJAM"

    # Direct code: KLS
    code, name = KTMStationRegistry.resolve_station("KLS")
    assert code == "KLS"
    assert name == "KL SENTRAL"

def test_fuzzy_station_resolution():
    # Partial query: "Mertajam" should resolve to "BUKIT MERTAJAM"
    code, name = KTMStationRegistry.resolve_station("Mertajam")
    assert code == "BMT"
    assert name == "BUKIT MERTAJAM"

def test_station_search():
    results = KTMStationRegistry.search_stations("Sentral")
    assert len(results) >= 2
    codes = [r["code"] for r in results]
    assert "KLS" in codes
    assert "JBS" in codes

def test_station_search_aliases():
    # Searching by abbreviation "BM" should find BUKIT MERTAJAM
    bm_results = KTMStationRegistry.search_stations("BM")
    assert any(r["code"] == "BMT" for r in bm_results)

    # Searching by Chinese alias "大山脚" should find BUKIT MERTAJAM
    chinese_results = KTMStationRegistry.search_stations("大山脚")
    assert any(r["code"] == "BMT" for r in chinese_results)

    # Searching by alias "Penang" should find BUTTERWORTH
    penang_results = KTMStationRegistry.search_stations("Penang")
    assert any(r["code"] == "BTW" for r in penang_results)

def test_popular_stations():
    popular = KTMStationRegistry.get_popular_stations()
    assert len(popular) == 10
    codes = [p["code"] for p in popular]
    assert "KLS" in codes
    assert "BTW" in codes
    assert "BMT" in codes

def test_unknown_station():
    with pytest.raises(ValueError, match="Station 'Atlantis' not found"):
        KTMStationRegistry.get_code("Atlantis")

