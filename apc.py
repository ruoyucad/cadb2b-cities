"""Alberta — Alberta Purchasing Connection (purchasing.alberta.ca).

The site is an Angular SPA backed by a public JSON API on the same
origin. Anonymous search works (no account, no captcha on the API):

  POST /api/opportunity/search
      {"query": "", "limit": N, "offset": <page index>,
       "filter": {...}, "sortOptions": [{"field": "PostDateTime",
       "direction": "Desc"}]}
      -> {"values": [...], "totalCount": n, "facets": {...}}

Filter notes (reverse-engineered from the SPA + API validation):
  - postDateRange/closeDateRange use "$$custom" with explicit
    postDateStart/postDateEnd / closeDateStart/closeDateEnd dates;
    deliveryRegion must be present ("" = all).
  - list filters take Selectable objects: {"value": CODE, "selected": true}.
  - categoryCode is native: GD=Goods, SRV=Services, CNST=Construction.
  - statusCode: OPEN, EVALUATION, CLOSED, AWARD, CANCELLED,
    UNAWARDABLE, SELECTION. Items carry UNSPSC commodityCodes.

Award details (supplier name + amount + award date) are public per
posting: GET /api/opportunity/public/{year}/{number} -> {"awards": [...]}.

Rows land in staging with source_portal='alberta_apc'; one row per
award for awarded postings, one row otherwise.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import http as H
from .store import to_date

PORTAL = "alberta_apc"
BASE = "https://purchasing.alberta.ca"
SEARCH_URL = f"{BASE}/api/opportunity/search"
PAGE_SIZE = 100

_CAT = {"GD": "Goods", "SRV": "Services", "CNST": "Construction"}
_STATUS = {"OPEN": "Open", "AWARD": "Awarded", "CLOSED": "Closed",
           "EVALUATION": "Closed", "CANCELLED": "Cancelled",
           "UNAWARDABLE": "No Award", "SELECTION": "Closed"}
_REF_RE = re.compile(r"^AB-(\d{4})-(\d+)$")


def make_filter(post_start: date, post_end: date, close_start: date,
                close_end: date, statuses: list[str] | None = None) -> dict:
    return {
        "solicitationNumber": "", "categories": [],
        "statuses": [{"value": s, "selected": True} for s in (statuses or [])],
        "agreementTypes": [], "solicitationTypes": [], "opportunityTypes": [],
        "deliveryRegions": [], "deliveryRegion": "", "organizations": [],
        "unspsc": [],
        "postDateRange": "$$custom", "postDateStart": post_start.isoformat(),
        "postDateEnd": post_end.isoformat(),
        "closeDateRange": "$$custom", "closeDateStart": close_start.isoformat(),
        "closeDateEnd": close_end.isoformat(),
    }


def search(session, filt: dict) -> list[dict]:
    """All values for a filter, paging with offset = page index."""
    items, offset = [], 0
    while True:
        body = {"query": "", "limit": PAGE_SIZE, "offset": offset, "filter": filt,
                "sortOptions": [{"field": "PostDateTime", "direction": "Desc"}]}
        resp = session.post(SEARCH_URL, json=body, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"APC search failed: HTTP {resp.status_code} "
                               f"{resp.text[:160]}")
        data = resp.json()
        values = data.get("values") or []
        items.extend(values)
        if not values or len(items) >= (data.get("totalCount") or 0):
            return items
        offset += 1


def split_ref(ref: str | None) -> tuple[int, int] | None:
    m = _REF_RE.match((ref or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def fetch_awards(session, ref: str | None) -> list[dict]:
    parts = split_ref(ref)
    if not parts:
        return []
    data = H.get(session, f"{BASE}/api/opportunity/public/{parts[0]}/{parts[1]}").json()
    return data.get("awards") or []


def item_to_rows(item: dict, awards: list[dict] | None = None) -> list[dict]:
    codes = [str(c) for c in item.get("commodityCodes") or []]
    titles = [str(t) for t in item.get("commodityCodeTitles") or []]
    cat_parts = []
    if codes:
        labelled = ", ".join(f"{c} {t}".strip() for c, t in zip(codes, titles)) \
            if titles else ", ".join(codes)
        cat_parts.append(f"UNSPSC: {labelled}")
        cat_parts.append(f"unspsc_segment:{codes[0][:2]}")
    if item.get("solicitationTypeCode"):
        cat_parts.append(f"Solicitation: {item['solicitationTypeCode']}")
    base = {
        "source_portal": PORTAL,
        "url": f"{BASE}/posting/{item.get('referenceNumber')}",
        "agency": item.get("contractingOrganization"),
        "title": item.get("shortTitle") or item.get("title"),
        "bid_number": item.get("referenceNumber"),
        "detail_guid": item.get("id"),
        "bid_classification": _CAT.get(item.get("categoryCode") or ""),
        "categories_raw": " | ".join(cat_parts) or None,
        "status": _STATUS.get(item.get("statusCode") or "", item.get("statusCode")),
        "publish_date": to_date(item.get("postDateTime")),
        "closing_date": to_date(item.get("closeDateTime")),
        "all_bidders_json": None,
        "plan_takers_count": None,
        "agency_contact_name": None,
        "agency_contact_email": None,
        "agency_contact_phone": None,
    }
    if awards:
        rows = []
        for aw in awards:
            supplier = (aw.get("alternativeSupplierDisplayName")
                        or aw.get("operatingOrTradeName"))
            amount = aw.get("amount")
            rows.append({**base, "status": "Awarded",
                         "award_date": to_date(aw.get("awardDate")),
                         "awarded_value": float(amount) if amount is not None else None,
                         "awarded_supplier_raw": supplier})
        return rows
    return [{**base, "award_date": None, "awarded_value": None,
             "awarded_supplier_raw": None}]


def scan_apc(session, window_start: date, today: date | None = None) -> list[dict]:
    """Recent postings + recently-awarded postings (incl. older ones).

    Award detail documents are fetched only for AWARD postings in the
    native Goods category — those are the rows the leads mart can use.
    """
    today = today or date.today()
    recent = search(session, make_filter(window_start, today,
                                         window_start, today + timedelta(days=365)))
    awarded = search(session, make_filter(today - timedelta(days=400), today,
                                          window_start, today + timedelta(days=30),
                                          statuses=["AWARD"]))
    by_ref: dict[str, dict] = {}
    for item in recent + awarded:
        ref = item.get("referenceNumber")
        if ref:
            by_ref[ref] = item
    rows: list[dict] = []
    for item in by_ref.values():
        awards = None
        if item.get("statusCode") == "AWARD" and item.get("categoryCode") == "GD":
            try:
                awards = fetch_awards(session, item.get("referenceNumber")) or None
            except Exception:
                awards = None
        rows.extend(item_to_rows(item, awards))
    return rows
