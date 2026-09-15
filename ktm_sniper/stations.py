from typing import Dict, List, Tuple, Optional

class KTMStationRegistry:
    """
    Registry mapping Malaysian railway station names and codes
    for KTMB (KITS) ETS, Intercity, and Komuter services.
    Supports all major stations extracted from live KITS metadata, aliases,
    and fuzzy/substring auto-resolution.
    """
    _STATIONS: Dict[str, str] = {
        # Core original stations
        "KL SENTRAL": "KLS",
        "JB SENTRAL": "JBS",
        "PADANG BESAR": "PDB",
        "BUTTERWORTH": "BTW",
        "IPOH": "IPH",

        # Penang & Northern ETS stations
        "BUKIT MERTAJAM": "BMT",
        "TASEK GELUGOR": "TGR",
        "NIBONG TEBAL": "NTB",
        "PARIT BUNTAR": "PBR",
        "BAGAN SERAI": "BGS",

        # Kedah & Perlis stations
        "ARAU": "ARU",
        "ANAK BUKIT": "ABK",
        "ALOR SETAR": "ASR",
        "GURUN": "GRN",
        "SUNGAI PETANI": "SPN",

        # Perak ETS stations
        "TAIPING": "TPG",
        "PADANG RENGAS": "PRG",
        "KUALA KANGSAR": "KKR",
        "SUNGAI SIPUT": "SSP",
        "BATU GAJAH": "BGH",
        "KAMPAR": "KMP",
        "TAPAH ROAD": "TPH",
        "SUNGKAI": "SKI",
        "SLIM RIVER": "SRV",
        "BEHRANG": "BHG",
        "TANJONG MALIM": "TGM",
        "TANJUNG MALIM": "TGM",

        # Selangor & KL stations
        "KUALA KUBU BHARU": "KKB",
        "BATANG KALI": "BKL",
        "RAWANG": "RWG",
        "SUNGAI BULOH": "SGB",
        "KEPONG SENTRAL": "KPS",
        "KUALA LUMPUR": "KLP",
        "BDR TASEK SELATAN": "BTS",
        "BANDAR TASEK SELATAN": "BTS",
        "BANDAR TASIK SELATAN": "BTS",
        "KAJANG": "KJG",
        "SUBANG JAYA": "SBJ",
        "SHAH ALAM": "SHA",
        "KLANG": "KLG",

        # Negeri Sembilan & Melaka stations
        "NILAI": "NLI",
        "SEREMBAN": "SBN",
        "REMBAU": "RBU",
        "PULAU SEBANG": "TBN",
        "TAMPIN": "TBN",
        "PULAU SEBANG/TAMPIN": "TBN",
        "BATANG MELAKA": "BML",

        # Johor & Southern stations
        "GEMAS": "GMS",
        "SEGAMAT": "SGT",
        "LABIS": "LBS",
        "BEKOK": "BKK",
        "PALOH": "PLH",
        "KLUANG": "KLU",
        "RENGAM": "RGM",
        "LAYANG LAYANG": "LYG",
        "KULAI": "KLI",
        "KEMPAS BARU": "KPB",

        # East Coast Line (KTM Intercity / DMU)
        "BAHAU": "BHU",
        "TRIANG": "TRG",
        "MENGKARAK": "MGK",
        "KEMAYAN": "KMY",
        "MENTAKAB": "MTB",
        "JERANTUT": "JRT",
        "KRAMBIT": "KBT",
        "KUALA LIPIS": "KLP",
        "PADANG TUNGKU": "PTK",
        "CHEGAR PERAH": "CPH",
        "MERAPOH": "MPH",
        "GUA MUSANG": "GMS",
        "DABONG": "DBG",
        "KRAI": "KRI",
        "TANAH MERAH": "TNM",
        "PASIR MAS": "PMS",
        "WAKAF BHARU": "WKB",
        "TUMPAT": "TPT",
        "HAT YAI": "HYT"
    }

    _ALIASES: Dict[str, str] = {
        "PENANG": "BUTTERWORTH",
        "PULAU PINANG": "BUTTERWORTH",
        "PG": "BUTTERWORTH",
        "槟城": "BUTTERWORTH",
        "北海": "BUTTERWORTH",
        "BM": "BUKIT MERTAJAM",
        "大山脚": "BUKIT MERTAJAM",
        "KL": "KL SENTRAL",
        "KUALA LUMPUR": "KL SENTRAL",
        "吉隆坡": "KL SENTRAL",
        "JB": "JB SENTRAL",
        "JOHOR BAHRU": "JB SENTRAL",
        "新山": "JB SENTRAL",
        "TBS": "BDR TASEK SELATAN",
        "BTS": "BDR TASEK SELATAN",
        "TAMPIN": "PULAU SEBANG",
        "PERLIS": "PADANG BESAR",
        "KEDAH": "ALOR SETAR",
        "怡保": "IPOH"
    }

    @classmethod
    def get_code(cls, station_name: str) -> str:
        code, _ = cls.resolve_station(station_name)
        return code

    @classmethod
    def get_name(cls, code: str) -> str:
        norm_code = code.strip().upper()
        for name, c in cls._STATIONS.items():
            if c == norm_code:
                return name
        raise ValueError(f"Station code '{code}' not found in registry.")

    @classmethod
    def resolve_station(cls, query: str) -> Tuple[str, str]:
        """
        Resolves station query (name, code, or alias) to (station_code, canonical_name).
        Supports exact match, alias match, and fuzzy substring match.
        """
        raw = query.strip().upper()

        # 1. Direct match with 3-letter station code
        for name, code in cls._STATIONS.items():
            if code == raw:
                return code, name

        # 2. Exact station name
        if raw in cls._STATIONS:
            return cls._STATIONS[raw], raw

        # 3. Known alias
        if raw in cls._ALIASES:
            canonical = cls._ALIASES[raw]
            return cls._STATIONS[canonical], canonical

        # 4. Fuzzy / substring match
        # Normalize punctuation/spaces
        cleaned_raw = raw.replace("-", " ")
        for name, code in cls._STATIONS.items():
            if cleaned_raw in name or name in cleaned_raw:
                return code, name

        # Check aliases fuzzy
        for alias, canonical in cls._ALIASES.items():
            if cleaned_raw in alias or alias in cleaned_raw:
                return cls._STATIONS[canonical], canonical

        raise ValueError(f"Station '{query}' not found in registry.")

    @classmethod
    def search_stations(cls, query: str) -> List[Dict[str, str]]:
        """
        Fuzzy search stations by name, code, or alias (including abbreviations & Chinese names).
        Returns a list of matching stations: [{"name": canonical_name, "code": station_code}, ...]
        """
        q = query.strip().upper()
        if not q:
            return []

        matches = []
        seen_codes = set()

        # 1. Exact / substring match with 3-letter station code or station name
        for name, code in cls._STATIONS.items():
            if q == code or q in name or name in q:
                if code not in seen_codes:
                    matches.append({"name": name, "code": code})
                    seen_codes.add(code)

        # 2. Check aliases (BM, Penang, 大山脚, etc.)
        for alias, canonical in cls._ALIASES.items():
            norm_alias = alias.strip().upper()
            if q == norm_alias or q in norm_alias or norm_alias in q:
                code = cls._STATIONS.get(canonical)
                if code and code not in seen_codes:
                    matches.append({"name": canonical, "code": code})
                    seen_codes.add(code)

        return matches

    @classmethod
    def get_popular_stations(cls) -> List[Dict[str, str]]:
        """
        Returns top 10 major transit hubs and popular tourist/business stations.
        """
        popular = [
            ("KL SENTRAL", "KLS"),
            ("BUTTERWORTH", "BTW"),
            ("BUKIT MERTAJAM", "BMT"),
            ("IPOH", "IPH"),
            ("PADANG BESAR", "PDB"),
            ("JB SENTRAL", "JBS"),
            ("GEMAS", "GMS"),
            ("ALOR SETAR", "ASR"),
            ("ARAU", "ARU"),
            ("TAIPING", "TPG")
        ]
        return [{"name": name, "code": code} for name, code in popular]

    @classmethod
    def all_stations(cls) -> List[Dict[str, str]]:
        return [{"name": name, "code": code} for name, code in cls._STATIONS.items()]

