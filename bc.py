"""British Columbia — ministry contract awards from the BC Data Catalogue.

BC Bid itself (bcbid.gov.bc.ca) gates every page behind a Google
reCAPTCHA v3 browser check, so it cannot be scanned politely. The same
award data is published openly as quarterly CSVs in the BC Data
Catalogue (dataset "Ministry Contract Awards - Province of British
Columbia"), refreshed quarterly. Awards-only feed — like Toronto's CKAN
awarded contracts — with winner name and award total, but no
Goods/Services split: the title is classified with the shared rules.

Rows land in staging with source_portal='bc_opendata'.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date

from . import config, http as H
from .store import classify_goods, to_date

PORTAL = "bc_opendata"


def _quarter_end(period: str) -> date | None:
    """'April - June 2026' -> 2026-06-30; 'July - Sept 2026' -> 2026-09-30."""
    m = re.search(r"(\d{4})", period or "")
    if not m:
        return None
    year = int(m.group(1))
    p = (period or "").lower()
    for months, end in ((("jan", "mar"), (3, 31)), (("apr", "jun"), (6, 30)),
                        (("jul", "sep"), (9, 30)), (("oct", "dec"), (12, 31))):
        if any(mo in p for mo in months):
            return date(year, end[0], end[1])
    return None


def list_award_resources(session) -> list[dict]:
    """CSVs in the awards dataset, newest first (xlsx quarters skipped)."""
    data = H.get(session, f"{config.BC_CKAN}/api/3/action/package_show",
                 params={"id": config.BC_AWARDS_DATASET}).json()
    out = []
    for res in (data.get("result") or {}).get("resources") or []:
        name = res.get("name") or ""
        if (res.get("format") or "").upper() != "CSV":
            continue
        qe = _quarter_end(name)
        if qe is None or not res.get("url"):
            continue
        out.append({"url": res["url"], "name": name, "quarter_end": qe})
    return sorted(out, key=lambda r: r["quarter_end"], reverse=True)


def _parse_money(raw: str | None) -> float | None:
    if not raw:
        return None
    txt = str(raw).replace("$", "").replace(",", "").strip()
    try:
        return float(txt)
    except ValueError:
        return None


def _clean_date(raw: str | None) -> str | None:
    """BC dates sometimes carry an 'entered ' prefix from the source system."""
    if not raw:
        return None
    txt = str(raw).strip()
    if txt.lower().startswith("entered"):
        txt = txt[7:].strip()
    return txt or None


def parse_awards_csv(text: str) -> list[dict]:
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        title = (rec.get("Title") or "").strip()
        vendor = (rec.get("Successful Vendor") or "").strip()
        if not title and not vendor:
            continue
        rows.append({
            "agency": (rec.get("Issued by Organization")
                       or rec.get("Issued for Organization") or "").strip() or None,
            "title": title or None,
            "bid_number": (rec.get("Opportunity ID") or "").strip() or None,
            "procurement_method": (rec.get("Procurement Method") or "").strip(),
            "publish_raw": _clean_date(rec.get("Issued Date")),
            "closing_raw": _clean_date(rec.get("Closing Date")),
            "award_raw": _clean_date(rec.get("Date Awarded")),
            "supplier": vendor or None,
            "vendor_address": (rec.get("Successful Vendor Address") or "").strip(),
            "vendor_country": (rec.get("Successful Vendor Country") or "").strip(),
            "awarded_value": _parse_money(rec.get("Award Total")),
        })
    return rows


def to_staging(row: dict) -> dict:
    method = row.get("procurement_method") or ""
    categories = f"Procurement Method: {method}" if method else None
    # No native Goods/Services field: classify from the title text.
    hit = classify_goods(row.get("title"), None)
    return {
        "source_portal": PORTAL,
        "url": config.BC_PORTAL_URL,
        "agency": row.get("agency"),
        "title": row.get("title"),
        "bid_number": row.get("bid_number"),
        "detail_guid": None,
        "bid_classification": "Goods" if hit else None,
        "categories_raw": categories or None,
        "status": "Awarded",
        "publish_date": to_date(row.get("publish_raw")),
        "closing_date": to_date(row.get("closing_raw")),
        "award_date": to_date(row.get("award_raw")),
        "awarded_value": row.get("awarded_value"),
        "awarded_supplier_raw": row.get("supplier"),
        "all_bidders_json": None,
        "plan_takers_count": None,
        "agency_contact_name": None,
        "agency_contact_email": None,
        "agency_contact_phone": None,
    }


def scan_bc(session, window_start: date) -> list[dict]:
    """All award rows whose quarter file ends on/after the window start.

    The window filters at file granularity (quarterly files); row-level
    dates are kept as published.
    """
    out: list[dict] = []
    for res in list_award_resources(session):
        if res["quarter_end"] < window_start:
            continue
        raw = H.get(session, res["url"]).content
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:  # some quarters ship Windows-1252 bytes
            text = raw.decode("cp1252")
        out.extend(to_staging(r) for r in parse_awards_csv(text))
    return out
