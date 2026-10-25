"""City of Toronto awarded contracts via Toronto Open Data (CKAN).

The Bids Portal's own awarded-contracts search is a JS app; the same data is
published as the `tobids-awarded-contracts` open dataset with a datastore
API — that is the machine-readable route (awards only, no Ariba scraping).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from . import http as http_util
from .config import TORONTO_CKAN, TORONTO_AWARDED_RESOURCE, TORONTO_PORTAL_URL, TORONTO_SINCE_DAYS


def _money(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def scan(session: requests.Session) -> tuple[list[dict], dict]:
    since = (datetime.now().date() - timedelta(days=TORONTO_SINCE_DAYS)).isoformat()
    rows, offset, page = [], 0, 1000
    while True:
        url = (f"{TORONTO_CKAN}/api/3/action/datastore_search"
               f"?resource_id={TORONTO_AWARDED_RESOURCE}&limit={page}&offset={offset}")
        d = http_util.get(session, url).json()
        recs = d["result"]["records"]
        if not recs:
            break
        for r in recs:
            dt = (r.get("Award Authority Obtained Date") or "")[:10]
            if dt and dt < since:
                continue
            desc = (r.get("Solicitation Document Description") or "").strip()
            # The dataset has no title field; use the description's first
            # sentence as the display title (full text stays searchable via
            # the whitelist match on categories/description keywords).
            title = desc.split(". ")[0].strip()[:200] or None
            rows.append({
                "source_portal": "toronto_opendata",
                "agency": "City of Toronto",
                "bid_number": r.get("Document Number"),
                "detail_guid": None,
                "url": TORONTO_PORTAL_URL,
                "title": title,
                "bid_classification": r.get("High Level Category"),
                # Third element carries a description excerpt so the mart's
                # keyword whitelist can match on it (dataset has no category
                # tree of its own).
                "categories_raw": " | ".join(x for x in [r.get("High Level Category"),
                                                         r.get("RFx (Solicitation) Type"),
                                                         desc[:300]] if x),
                "status": "Awarded",
                "publish_date": None,
                "closing_date": None,
                "award_date": dt or None,
                "awarded_supplier_raw": r.get("Successful Supplier"),
                "awarded_value": _money(r.get("Award")),
                "all_bidders_json": None,
                "plan_takers_count": None,
            })
        offset += page
        if offset >= d["result"]["total"]:
            break
    return rows, {"agency": "City of Toronto", "discovered": len(rows), "details": len(rows)}
