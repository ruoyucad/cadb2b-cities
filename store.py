"""DuckDB storage: stg_muni_tenders (append-only snapshots) +
mart_muni_goods_leads (brokerable-goods view, rebuilt each scan).

Additive only — no federal pipeline table (fct_contract etc.) is touched.
Supplier names are normalized with the same supplier_key() definition as
sql/20_fct_contract.sql so municipal winners join to dim_supplier.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import duckdb

from .config import HARD_EXCLUSIONS, MIN_AWARD_CAD, WHITELIST

SUPPLIER_KEY_MACRO = """
CREATE OR REPLACE MACRO supplier_key(x) AS
  trim(regexp_replace(regexp_replace(
    regexp_replace(
      regexp_replace(upper(strip_accents(coalesce(x, ''))), '&', ' AND ', 'g'),
      '[^A-Z0-9 ]', ' ', 'g'),
    '\\b(THE|INC|INCORPORATED|LTD|LIMITED|LTEE|LIMITEE|CORP|CORPORATION|CO|COMPANY|LLC|LLP|ULC|LP|SENC|SEC|INCORPOREE)\\b', ' ', 'g'),
    '\\s+', ' ', 'g'));
"""

DDL = """
CREATE TABLE IF NOT EXISTS stg_muni_tenders (
  source_portal VARCHAR, agency VARCHAR, bid_number VARCHAR, detail_guid VARCHAR,
  url VARCHAR, title VARCHAR, bid_classification VARCHAR, categories_raw VARCHAR,
  status VARCHAR, publish_date DATE, closing_date DATE, award_date DATE,
  awarded_supplier_raw VARCHAR, awarded_value DOUBLE, all_bidders_json VARCHAR,
  plan_takers_count INTEGER, scraped_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ref_muni_category_map (
  matched_keyword VARCHAR, segment VARCHAR, segment_en VARCHAR, segment_zh VARCHAR
);
"""


def connect(db_path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    con.execute(SUPPLIER_KEY_MACRO)
    con.execute(DDL)
    return con


def insert_snapshots(con, rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    con.executemany(
        """INSERT INTO stg_muni_tenders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(r["source_portal"], r["agency"], r["bid_number"], r["detail_guid"], r["url"],
          r["title"], r["bid_classification"], r["categories_raw"], r["status"],
          r["publish_date"], r["closing_date"], r["award_date"],
          r["awarded_supplier_raw"], r["awarded_value"], r["all_bidders_json"],
          r["plan_takers_count"], now) for r in rows])
    return len(rows)


def _segment_names(con) -> dict[str, tuple[str, str]]:
    try:
        return {r[0]: (r[1], r[2]) for r in
                con.execute("SELECT segment, segment_en, segment_zh FROM ref_unspsc_segment").fetchall()}
    except duckdb.Error:
        return {}


def classify_goods(title: str | None, categories: str | None) -> tuple[str, str] | None:
    """Returns (segment, matched_keyword) or None. Exclusions win first."""
    text = f"{title or ''} {categories or ''}".lower()
    for bad in HARD_EXCLUSIONS:
        if bad in text:
            return None
    for keywords, segment in WHITELIST:
        for kw in keywords:
            if kw in text:
                return segment, kw
    return None


def rebuild_mart(con) -> int:
    names = _segment_names(con)
    # Latest snapshot per tender (per supplier for multi-winner Toronto rows).
    latest = con.execute("""
        SELECT source_portal, agency, bid_number, detail_guid, url, title,
               bid_classification, categories_raw, status, publish_date,
               closing_date, award_date, awarded_supplier_raw, awarded_value,
               all_bidders_json, plan_takers_count
        FROM (
          SELECT *, row_number() OVER (
            PARTITION BY source_portal, agency,
                         coalesce(bid_number, detail_guid, ''),
                         coalesce(awarded_supplier_raw, '')
            ORDER BY scraped_at DESC) AS rn
          FROM stg_muni_tenders)
        WHERE rn = 1""").fetchall()
    cols = ["source_portal", "agency", "bid_number", "detail_guid", "url", "title",
            "bid_classification", "categories_raw", "status", "publish_date",
            "closing_date", "award_date", "awarded_supplier_raw", "awarded_value",
            "all_bidders_json", "plan_takers_count"]
    fct_keys = set()
    try:
        fct_keys = {(r[0], r[1], r[2]) for r in con.execute(
            "SELECT supplier_key, year(award_date), value_cad FROM fct_contract "
            "WHERE supplier_key IS NOT NULL AND award_date IS NOT NULL").fetchall()}
    except duckdb.Error:
        pass
    leads, map_rows = [], set()
    for row in latest:
        r = dict(zip(cols, row))
        cls = (r["bid_classification"] or "")
        if r["source_portal"] == "toronto_opendata":
            if "goods" not in cls.lower():
                continue
        elif cls.strip().lower() != "goods":
            continue
        if r["awarded_value"] is None or r["awarded_value"] < MIN_AWARD_CAD:
            continue
        hit = classify_goods(r["title"], r["categories_raw"])
        if not hit:
            continue
        segment, kw = hit
        en, zh = names.get(segment, (None, None))
        map_rows.add((kw, segment, en, zh))
        skey = con.execute("SELECT supplier_key(?)", [r["awarded_supplier_raw"]]).fetchone()[0] \
            if r["awarded_supplier_raw"] else None
        possible_dup = False
        if skey and r["award_date"] and r["awarded_value"]:
            yr = r["award_date"].year
            possible_dup = any(sk == skey and y == yr and v and abs(v - r["awarded_value"]) / r["awarded_value"] < 0.01
                               for (sk, y, v) in fct_keys)
        n_bidders = None
        if r["all_bidders_json"]:
            try:
                n_bidders = len(json.loads(r["all_bidders_json"]))
            except ValueError:
                pass
        leads.append((r["source_portal"], r["agency"], r["bid_number"], r["title"], r["url"],
                      segment, en, zh, r["categories_raw"], r["status"], r["publish_date"],
                      r["closing_date"], r["award_date"], r["awarded_supplier_raw"], skey,
                      r["awarded_value"], n_bidders, r["plan_takers_count"], possible_dup))
    con.execute("DELETE FROM ref_muni_category_map")
    if map_rows:
        con.executemany("INSERT INTO ref_muni_category_map VALUES (?,?,?,?)", sorted(map_rows))
    con.execute("""
        CREATE OR REPLACE TABLE mart_muni_goods_leads (
          source_portal VARCHAR, buyer VARCHAR, bid_number VARCHAR, title VARCHAR,
          url VARCHAR, segment VARCHAR, segment_en VARCHAR, segment_zh VARCHAR,
          categories_raw VARCHAR, status VARCHAR, publish_date DATE,
          closing_date DATE, award_date DATE, supplier_raw VARCHAR,
          supplier_key VARCHAR, value_cad DOUBLE, n_bidders INTEGER,
          plan_takers_count INTEGER, possible_dup BOOLEAN)""")
    if leads:
        con.executemany("INSERT INTO mart_muni_goods_leads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        leads)
    return len(leads)
