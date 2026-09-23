"""
ktm_sniper/network/kits_parser.py
==================================
Pure HTML parsing layer for KTMB KITS web portal responses.
Completely decoupled from the HTTP transport layer.

Parses two types of KITS HTML:
  1. Homepage HTML  -> station lookup tables (groupedStations, jsStations)
  2. /Trip/Trip response HTML -> train availability rows
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class ParsedTrip:
    """
    A single train service parsed from the KITS /Trip/Trip HTML response.
    """
    train_service:   str
    train_no:        str
    train_class:     str
    depart_time:     str
    arrive_time:     str
    available_seats: int
    min_fare:        float
    is_available:    bool

    @classmethod
    def from_row(cls, cells: list) -> Optional["ParsedTrip"]:
        if len(cells) < 6:
            return None
        try:
            service_raw = cells[0].get_text(strip=True)
            depart_raw  = cells[1].get_text(strip=True)
            arrive_raw  = cells[2].get_text(strip=True)
            seats_raw   = cells[4].get_text(strip=True)
            fare_raw    = cells[5].get_text(strip=True)

            parts = [p.strip() for p in service_raw.split("-", 1)]
            train_class = parts[0] if parts else service_raw
            train_no    = parts[1] if len(parts) > 1 else service_raw

            seat_match = re.search(r"\d+", seats_raw)
            seats = int(seat_match.group()) if seat_match else 0

            fare_match = re.search(r"[\d.]+", fare_raw)
            fare = float(fare_match.group()) if fare_match else 0.0

            return cls(
                train_service=service_raw,
                train_no=train_no.strip(),
                train_class=train_class.strip(),
                depart_time=depart_raw,
                arrive_time=arrive_raw,
                available_seats=seats,
                min_fare=fare,
                is_available=seats > 0,
            )
        except Exception as exc:
            logger.debug(f"Failed to parse trip row: {exc}")
            return None


@dataclass
class StationIndex:
    name_to_id:  dict = field(default_factory=dict)
    id_to_data:  dict = field(default_factory=dict)

    def resolve(self, name: str):
        # 1. Canonical alias resolution (e.g. 'Penang' -> 'BUTTERWORTH', 'BM' -> 'BUKIT MERTAJAM')
        try:
            from ktm_sniper.stations import KTMStationRegistry
            _, canon = KTMStationRegistry.resolve_station(name)
            key = canon.upper().strip()
        except Exception:
            key = name.upper().strip()

        # 2. Exact match with canonical name
        if key in self.name_to_id:
            sid = self.name_to_id[key]
            return sid, self.id_to_data.get(sid, "")

        # 3. Direct match with raw query
        raw_key = name.upper().strip()
        if raw_key in self.name_to_id:
            sid = self.name_to_id[raw_key]
            return sid, self.id_to_data.get(sid, "")

        # 4. Substring / partial match
        for k, sid in self.name_to_id.items():
            if key in k or raw_key in k:
                return sid, self.id_to_data.get(sid, "")
        raise KeyError(f"Station '{name}' not found in KITS station index.")

    def all_names(self) -> list:
        return sorted(self.name_to_id.keys())


def parse_stations(homepage_html: str) -> StationIndex:
    index = StationIndex()

    gs_match = re.search(r"var\s+groupedStations\s*=\s*(\[.*?\]);", homepage_html, re.DOTALL)
    if not gs_match:
        logger.warning("groupedStations not found in homepage HTML")
        return index

    try:
        grouped = json.loads(gs_match.group(1))
        for group in grouped:
            for station in group.get("Stations", []):
                desc = station.get("Description", "").upper()
                sid  = station.get("Id", "")
                if desc and sid:
                    index.name_to_id[desc] = sid
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error(f"Failed to parse groupedStations: {exc}")

    js_match = re.search(r"var\s+jsStations\s*=\s*(\[.*?\]);", homepage_html, re.DOTALL)
    if js_match:
        try:
            js_stations = json.loads(js_match.group(1))
            for s in js_stations:
                sid  = s.get("Id", "")
                data = s.get("StationData", "")
                if sid:
                    index.id_to_data[sid] = data
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(f"Failed to parse jsStations: {exc}")

    logger.debug(f"Station index built: {len(index.name_to_id)} stations")
    return index


def parse_trip_html(html: str) -> list[ParsedTrip]:
    soup = BeautifulSoup(html, "html.parser")
    trips = []

    tbody = soup.find("tbody", class_="depart-trips")
    if not tbody:
        logger.debug("No <tbody class='depart-trips'> found in trip HTML")
        return trips

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        trip = ParsedTrip.from_row(cells)
        if trip:
            trips.append(trip)

    logger.debug(f"Parsed {len(trips)} trips from HTML fragment")
    return trips


def extract_hidden_fields(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for field_id in ("SearchData", "FormValidationCode"):
        tag = soup.find("input", {"id": field_id})
        result[field_id] = tag.get("value", "") if tag else ""

    csrf = soup.find("input", {"name": "__RequestVerificationToken"})
    result["__RequestVerificationToken"] = csrf.get("value", "") if csrf else ""

    return result


def extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    tag  = soup.find("input", {"name": "__RequestVerificationToken"})
    return tag.get("value", "") if tag else ""
