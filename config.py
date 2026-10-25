"""Configuration for the municipal GOODS-tender pipeline (cadb2b-cities).

This package is designed to become the standalone `cadb2b-cities` repo:
everything lives under muni/, and the only link back to the parent cadb2b
project is the shared DuckDB file. Its location is configurable:

  MUNI_DB_PATH   full path to the DuckDB file
                 (default: <repo root>/data/cbintel.duckdb, i.e. the parent
                 cadb2b-run database while muni/ still lives inside it;
                 when muni/ becomes its own repo root, drop a cbintel.duckdb
                 in ./data/ or set MUNI_DB_PATH to the shared file.)
"""
from __future__ import annotations

import os
from pathlib import Path

MUNI_DIR = Path(__file__).resolve().parent
# While muni/ sits inside cadb2b-run, repo root is its parent. As a standalone
# repo (muni files copied to the root), set MUNI_REPO_ROOT or keep the
# MUNI_DB_PATH env var pointed at the shared database.
REPO_ROOT = Path(os.environ.get("MUNI_REPO_ROOT", MUNI_DIR.parent))
DB_PATH = Path(os.environ.get("MUNI_DB_PATH", REPO_ROOT / "data" / "cbintel.duckdb"))

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
REQUEST_DELAY = 0.4          # seconds between requests (ordinary browsing pace)
REQUEST_TIMEOUT = 30

# bids&tenders cluster — GTA core first (v1 scope from the procurement map).
BIDSANDTENDERS_SITES = [
    ("peelregion",    "Peel Region"),
    ("mississauga",   "City of Mississauga"),
    ("brampton",      "City of Brampton"),
    ("york",          "York Region"),
    ("durham",        "Durham Region"),
    ("haltonregion",  "Halton Region"),
]

# Discovery windows. First run seeds a wider awarded window; steady-state
# twice-weekly runs only need the recent window (older rows already stored).
AWARDED_WINDOW_DAYS_FIRST = 120
AWARDED_WINDOW_DAYS = 21
DETAIL_CAPS = {"Open": 60, "Awarded": 80, "Closed": 40, "Unofficial Results": 40}

MIN_AWARD_CAD = 25_000  # brokerable threshold from the map

TORONTO_CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
TORONTO_AWARDED_RESOURCE = "e211f003-5909-4bea-bd96-d75899d8e612"
TORONTO_PORTAL_URL = ("https://www.toronto.ca/business-economy/doing-business-with-the-city/"
                      "searching-bidding-on-city-contracts/toronto-bids-portal/#awarded")
TORONTO_SINCE_DAYS = 730  # the dataset's own retention is ~2 years of awards

# ---------------------------------------------------------------------------
# Brokerable-goods filter (from municipal-procurement-map.md).
# Hard exclusions are checked against title+categories first; then the text
# must hit a whitelist keyword group, which maps to an existing UNSPSC
# top-level segment in ref_unspsc_segment.
# ---------------------------------------------------------------------------
HARD_EXCLUSIONS = [
    "food", "beverage", "catering", "grocery", "groceries", "meat", "dairy",
    "vehicle", "automobile", "truck purchase", "bus purchase", "fleet purchase",
    "pharmaceutical", "vaccine", "medical device", "medical equipment",
    "fuel", "gasoline", "diesel fuel", "propane", "natural gas supply",
    "hazardous", "ammunition", "firearm", "weapon", "alcohol", "liquor",
    "cement", "aggregate", "asphalt", "gravel", "ready-mix", "ready mix",
    "livestock", "animal feed",
]

# (keywords, unspsc segment) — order matters: first hit wins.
WHITELIST: list[tuple[list[str], str]] = [
    (["personal protective", "ppe", "respirator", "protective clothing",
      "safety glove", "safety equipment", "fall protection"], "46"),
    (["furniture", "desk", "chair", "cabinet", "locker", "seating",
      "furnishing", "shelving", "bookcase"], "56"),
    (["uniform", "apparel", "clothing", "textile", "linen", "garment",
      "footwear", "curtain"], "53"),
    (["janitorial", "cleaning supplies", "cleaning product", "detergent",
      "paper towel", "toilet tissue", "garbage bag", "mop", "custodial"], "47"),
    (["lighting", "led ", "luminaire", "lamp", "light fixture"], "39"),
    (["hand tool", "power tool", "tools and", "hardware", "fastener",
      "locksmith", " padlock", "measuring tool"], "27"),
    (["computer accessor", "peripheral", "keyboard", "mouse", "cable",
      "charging", "monitor arm", "docking"], "43"),
    (["office supplies", "stationery", "office paper", "printing paper",
      "toner", "ink cartridge"], "44"),
    (["sports equipment", "recreational equipment", "playground",
      "fitness equipment"], "49"),
    (["musical instrument", "educational supplies", "toys and games",
      "arts and crafts"], "60"),
    (["packaging material", "packaging supplies"], "31"),
    (["signage", "signs and", "wayfinding"], "30"),
]
