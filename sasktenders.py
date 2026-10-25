"""Saskatchewan — SaskTenders (sasktenders.ca), the province's official
tender site for the Government of Saskatchewan, Crown corporations,
municipalities, universities and health regions.

The list pages are server-rendered and public: each competition block
carries title, organization, competition #, open/close dates, status,
synopsis and categories inline (50 per page), so no detail fetch is
needed. Winner names and amounts, however, live in "Tender Results"
PDFs behind the site login — awarded rows therefore carry no supplier
or value (documented barrier, same thin layer as bidsandtenders
awards). Rows land in staging with source_portal='sasktenders'.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import config, http as H
from .bidsandtenders import html_to_lines
from .store import classify_goods

PORTAL = "sasktenders"
_PRINT_RE = re.compile(r"/Competitions/Print\?id=([0-9a-f-]{36})")
_TZ_RE = re.compile(r"\s+(CST|CDT|MST|MDT|EST|EDT|PST|PDT|UTC|AST|ADT)$")


def parse_sask_date(raw: str | None) -> str | None:
    """'Sep 21, 2026 1:00 PM CST' / 'Oct 23, 2026' -> ISO 'YYYY-MM-DD'."""
    if not raw:
        return None
    txt = _TZ_RE.sub("", str(raw).strip())
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y", "%B %d, %Y %I:%M %p", "%B %d, %Y"):
        try:
            return datetime.strptime(txt, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _after(lines: list[str], label: str) -> str | None:
    """Value line following an exact label line inside one block."""
    for i, ln in enumerate(lines):
        if ln == label and i + 1 < len(lines):
            return lines[i + 1]
    return None


def _between(lines: list[str], start_label: str, stop_labels: set[str]) -> list[str]:
    out, on = [], False
    for ln in lines:
        if ln == start_label:
            on = True
            continue
        if on and ln in stop_labels:
            break
        if on:
            out.append(ln)
    return out


def parse_list_page(html: str) -> list[dict]:
    """Split one list page into competition records."""
    lines = html_to_lines(html)
    starts = [i for i, ln in enumerate(lines) if ln == "Organization Name"]
    guids = _PRINT_RE.findall(html)
    records = []
    for bi, start in enumerate(starts):
        end = starts[bi + 1] if bi + 1 < len(starts) else len(lines)
        block = lines[start:end]
        # Title = last non-empty line before the "Organization Name" label.
        title = None
        for ln in reversed(lines[max(0, start - 3):start]):
            if ln and ln not in ("Competition Documents", "Amendments"):
                title = ln
                break
        synopsis = " ".join(_between(block, "Synopsis:",
                                     {"Competition Type:", "Categories:"}))
        cats = _between(block, "Categories:",
                        {"UNSPSC Categories:", "Procurement Mode:"})
        unspsc = _between(block, "UNSPSC Categories:", {"Procurement Mode:"})
        records.append({
            "title": title,
            "agency": _after(block, "Organization Name"),
            "bid_number": _after(block, "Competition #"),
            "opens_raw": _after(block, "Opens"),
            "closes_raw": _after(block, "Closes"),
            "status": _after(block, "Status"),
            "synopsis": synopsis or None,
            "comp_type": _after(block, "Competition Type:"),
            "categories": cats,
            "unspsc_cats": unspsc,
            "detail_guid": None,
        })
    # Print GUIDs appear twice per block in document order; only trust
    # the mapping when the parity is exact.
    if records and len(guids) == 2 * len(records):
        for i, rec in enumerate(records):
            rec["detail_guid"] = guids[2 * i]
    return records


def to_staging(rec: dict, list_url: str) -> dict:
    cat_parts = []
    if rec.get("comp_type"):
        cat_parts.append(f"Competition Type: {rec['comp_type']}")
    if rec.get("categories"):
        cat_parts.append("Categories: " + ", ".join(rec["categories"]))
    seg_token = None
    for u in rec.get("unspsc_cats") or []:
        m = re.search(r"\b(\d{2})\d{6}\b", u)
        if m:
            seg_token = m.group(1)
            break
    if rec.get("unspsc_cats"):
        cat_parts.append("UNSPSC: " + ", ".join(rec["unspsc_cats"]))
    if seg_token:
        cat_parts.append(f"unspsc_segment:{seg_token}")
    if rec.get("synopsis"):
        cat_parts.append(rec["synopsis"][:300])
    categories_raw = " | ".join(cat_parts) or None
    hit = classify_goods(rec.get("title"), categories_raw)
    return {
        "source_portal": PORTAL,
        "url": list_url,
        "agency": rec.get("agency"),
        "title": rec.get("title"),
        "bid_number": rec.get("bid_number"),
        "detail_guid": rec.get("detail_guid"),
        "bid_classification": "Goods" if hit else None,
        "categories_raw": categories_raw,
        "status": rec.get("status"),
        "publish_date": parse_sask_date(rec.get("opens_raw")),
        "closing_date": parse_sask_date(rec.get("closes_raw")),
        "award_date": None,
        "awarded_value": None,
        "awarded_supplier_raw": None,  # behind the site's login (Tender Results PDFs)
        "all_bidders_json": None,
        "plan_takers_count": None,
        "agency_contact_name": None,
        "agency_contact_email": None,
        "agency_contact_phone": None,
    }


def _fetch_page(session, status: str, page: int) -> tuple[str, str]:
    url = (f"{config.SASK_BASE}/?statusId={config.SASK_STATUS_IDS[status]}"
           f"&pageNumber={page}&pageSize={config.SASK_PAGE_SIZE}")
    return H.get(session, url).text, url


def scan_status(session, status: str, window_start: date) -> list[dict]:
    """Page through one status list, applying the window rules per status."""
    cap = config.SASK_MAX_PAGES.get(status, 10)
    lag_cutoff = (window_start - timedelta(days=config.SASK_AWARD_LAG_DAYS)).isoformat()
    window_iso = window_start.isoformat()
    rows: list[dict] = []
    for page in range(1, cap + 1):
        html, url = _fetch_page(session, status, page)
        recs = parse_list_page(html)
        if not recs:
            break
        staged = [to_staging(r, url) for r in recs]
        if status == "Open":
            rows.extend(staged)
        elif status == "Awarded":
            rows.extend(r for r in staged
                        if r["closing_date"] is None or r["closing_date"] >= lag_cutoff)
            closes = [r["closing_date"] for r in staged if r["closing_date"]]
            if closes and max(closes) < lag_cutoff:
                break
        else:  # Closed
            rows.extend(r for r in staged
                        if r["closing_date"] is None or r["closing_date"] >= window_iso)
    return rows


def scan_sask(session, window_start: date) -> list[dict]:
    rows: list[dict] = []
    for status in ("Open", "Awarded", "Closed"):
        rows.extend(scan_status(session, status, window_start))
    return rows
