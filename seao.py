"""Quebec — SEAO notices/awards via Données Québec open data (OCDS).

The SEAO portal itself sells access to documents and award details, so
it is not scanned. Quebec publishes the same notices as open data in
OCDS JSON: weekly delta files ("hebdo_YYYYMMDD_YYYYMMDD") and monthly
files on donneesquebec.ca. We use the weekly files only (monthly files
are ~70MB snapshots of the same releases). Each release carries the
buyer, the tender (with a native mainProcurementCategory —
goods/services/works — and UNSPSC item classifications) and, for
awarded notices, the awards with supplier names and CAD amounts.

Rows land in staging with source_portal='seao_opendata'. One staging
row per (award, supplier); tenders without awards get one row.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from . import config, http as H
from .store import to_date

PORTAL = "seao_opendata"

_CAT_MAP = {"goods": "Goods", "services": "Services", "works": "Construction"}
_STATUS_MAP = {"active": "Open", "complete": "Closed", "cancelled": "Cancelled",
               "unsuccessful": "No Award", "planned": "Open", "planning": "Open"}
_WEEK_RE = re.compile(r"hebdo_(\d{8})_(\d{8})")


def _d(raw) -> date | None:
    """OCDS ISO datetimes -> date (take the YYYY-MM-DD prefix)."""
    if not raw:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw))
    return to_date(m.group(1)) if m else None


def parse_release(rel: dict) -> dict | None:
    """Normalize one OCDS release to a tender record (awards kept raw)."""
    tender = rel.get("tender") or {}
    buyer = rel.get("buyer") or {}
    title = tender.get("title")
    if not title and not rel.get("ocid"):
        return None
    items = tender.get("items") or []
    descs, segment = [], None
    for it in items:
        if it.get("description"):
            descs.append(str(it["description"]))
        for cls in it.get("additionalClassifications") or []:
            if segment is None and str(cls.get("scheme", "")).upper().startswith("UNSPSC"):
                segment = str(cls.get("id") or "")[:2]
        main_cls = it.get("classification") or {}
        if segment is None and str(main_cls.get("scheme", "")).upper().startswith("UNSPSC"):
            segment = str(main_cls.get("id") or "")[:2]
    cats = " | ".join(descs)
    if segment and segment.isdigit():
        cats = (cats + f" | unspsc_segment:{segment}").strip(" |")
    return {
        "ocid": rel.get("ocid"),
        "release_date": _d(rel.get("date")),
        "agency": buyer.get("name"),
        "title": title,
        "bid_number": tender.get("id"),
        "status_raw": tender.get("status"),
        "category_raw": tender.get("mainProcurementCategory"),
        "categories_raw": cats or None,
        "closing_date": _d((tender.get("tenderPeriod") or {}).get("endDate")),
        "n_tenderers": tender.get("numberOfTenderers"),
        "tenderers": [t.get("name") for t in tender.get("tenderers") or []
                      if t.get("name")],
        "awards": rel.get("awards") or [],
    }


def to_staging(rec: dict) -> list[dict]:
    base = {
        "source_portal": PORTAL,
        "url": config.SEAO_PORTAL_URL,
        "agency": rec.get("agency"),
        "title": rec.get("title"),
        "bid_number": rec.get("bid_number") or (rec.get("ocid") or "").rsplit("-", 1)[-1],
        "detail_guid": rec.get("ocid"),
        "bid_classification": _CAT_MAP.get((rec.get("category_raw") or "").lower()),
        "categories_raw": rec.get("categories_raw"),
        "publish_date": rec.get("release_date"),
        "closing_date": rec.get("closing_date"),
        "all_bidders_json": json.dumps(rec["tenderers"], ensure_ascii=False)
        if rec.get("tenderers") else None,
        "plan_takers_count": rec.get("n_tenderers"),
        "agency_contact_name": None,
        "agency_contact_email": None,
        "agency_contact_phone": None,
    }
    awards = rec.get("awards") or []
    if awards:
        rows = []
        for aw in awards:
            suppliers = [s.get("name") for s in aw.get("suppliers") or [] if s.get("name")]
            value = (aw.get("value") or {}).get("amount")
            for sup in suppliers or [None]:
                rows.append({**base, "status": "Awarded",
                             "award_date": _d(aw.get("date")),
                             "awarded_value": float(value) if value is not None else None,
                             "awarded_supplier_raw": sup})
        return rows
    status = _STATUS_MAP.get((rec.get("status_raw") or "").lower(), "Closed")
    return [{**base, "status": status, "award_date": None,
             "awarded_value": None, "awarded_supplier_raw": None}]


def merge_releases(releases: list[dict]) -> list[dict]:
    """One record per ocid: prefer the latest release that has awards,
    else the latest release; publish_date = earliest release date seen."""
    best: dict[str, dict] = {}
    first_seen: dict[str, date] = {}
    for rel in releases:
        rec = parse_release(rel)
        if not rec or not rec.get("ocid"):
            continue
        ocid = rec["ocid"]
        rd = rec.get("release_date")
        if rd and (ocid not in first_seen or rd < first_seen[ocid]):
            first_seen[ocid] = rd
        cur = best.get(ocid)
        if cur is None:
            best[ocid] = rec
            continue
        cur_has, new_has = bool(cur.get("awards")), bool(rec.get("awards"))
        if new_has and not cur_has:
            best[ocid] = rec
        elif new_has == cur_has and (rd or "") >= (cur.get("release_date") or ""):
            best[ocid] = rec
    out = []
    for ocid, rec in best.items():
        if ocid in first_seen:
            rec["release_date"] = first_seen[ocid]
        out.extend(to_staging(rec))
    return out


def scan_seao(session, window_start: date, window_end: date) -> list[dict]:
    data = H.get(session, f"{config.SEAO_CKAN}/api/3/action/package_show",
                 params={"id": config.SEAO_DATASET_ID}).json()
    files = []
    for res in (data.get("result") or {}).get("resources") or []:
        m = _WEEK_RE.search(res.get("name") or "")
        if not m or not res.get("url"):
            continue
        try:
            start = datetime.strptime(m.group(1), "%Y%m%d").date()
            end = datetime.strptime(m.group(2), "%Y%m%d").date()
        except ValueError:
            continue
        if end >= window_start and start <= window_end:
            files.append((start, res["url"]))
    rows: list[dict] = []
    for _start, url in sorted(files):
        resp = H.get(session, url)
        payload = resp.json()
        rows.extend(merge_releases(payload.get("releases") or []))
    return rows
