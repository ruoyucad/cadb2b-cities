"""bids&tenders (eSolutions) cluster: discovery + detail parsing.

Discovery mechanism (reverse-engineered from the site's own JS,
Resources/scriptsV2/home/index.js): the homepage repeater POSTs to

    {Home}/Tender/Search/{NodeId}?status=&limit=&start=&dir=&from=&to=&sort=
    form data: keywords, __RequestVerificationToken

where NodeId/Home/token are all embedded in the public homepage HTML.
Detail pages are fully server-rendered HTML — parse labels directly.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from datetime import datetime, timedelta, timezone

import requests

from . import http as http_util

LABELS = ["Bid Classification", "Bid Type", "Bid Number", "Bid Name", "Bid Status",
          "Bid Publish Date", "Publish Date", "Published Date", "Date Published",
          "Bid Closing Date", "Closing Date", "Bid Awarded Date", "Awarded Date",
          "Categories", "Category"]

# Lines that terminate a multi-line Categories list.
_CAT_STOP = ("this information is only available", "no addenda", "awarded",
             "bids submitted", "plan takers", "mailing address")


def _ms_date(v) -> str | None:
    if not v:
        return None
    m = re.search(r"/Date\((-?\d+)\)/", str(v))
    if not m:
        return None
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Toronto")).date().isoformat()
    except Exception:
        return None


def homepage_params(session: requests.Session, base: str) -> dict:
    r = http_util.get(session, f"{base}/Module/Tenders/en/Home/BidsHomepage")
    h = r.text
    node = re.search(r'id="NodeId" value="([^"]+)"', h).group(1)
    home = re.search(r"id=\"Home\" value='([^']*)'", h)
    home = home.group(1) if home else "/Module/Tenders/en"
    tok = re.search(r'id="bidDetailAntiForgery">\s*<input[^>]*value="([^"]+)"', h, re.S).group(1)
    return {"node": node, "home": home, "token": tok}


def search(session: requests.Session, base: str, params: dict, status: str,
           date_from: str = "", date_to: str = "", page_size: int = 100,
           max_items: int = 1000) -> list[dict]:
    """All list rows for one status (paginated). Dates as mm/dd/yyyy."""
    out, start = [], 0
    while len(out) < max_items:
        url = (f"{base}{params['home']}/Tender/Search/{params['node']}"
               f"?status={requests.utils.quote(status)}&limit={page_size}&start={start}"
               f"&dir=DESC&from={date_from}&to={date_to}&sort=DateClosing%20DESC,Id")
        r = http_util.post(session, url,
                           data={"keywords": "", "__RequestVerificationToken": params["token"]},
                           headers={"X-Requested-With": "XMLHttpRequest",
                                    "Referer": f"{base}/Module/Tenders/en/Home/BidsHomepage"})
        d = r.json()
        if not d.get("success"):
            raise RuntimeError(f"search failed for {base} status={status}: {str(d)[:200]}")
        rows = d.get("data") or []
        out.extend(rows)
        total = d.get("total") or 0
        start += page_size
        if not rows or start >= total:
            break
    return out


def html_to_lines(raw: str) -> list[str]:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", "\n", txt)
    txt = html_mod.unescape(txt)
    return [l.strip().strip("​") for l in txt.split("\n") if l.strip()]


def label_values(lines: list[str]) -> dict[str, str]:
    out = {}
    for i, l in enumerate(lines):
        lab = l.rstrip(":").strip()
        if lab in LABELS and lab not in out and i + 1 < len(lines):
            val = lines[i + 1]
            if lab in ("Categories", "Category") and val.lower().startswith("show categories"):
                cats = []
                for nxt in lines[i + 2:]:
                    if any(nxt.lower().startswith(s) for s in _CAT_STOP) or nxt.rstrip(":") in LABELS:
                        break
                    cats.append(nxt)
                val = "; ".join(cats)
            out[lab] = val
    return out


def parse_bids_submitted(raw: str) -> list[dict]:
    """Best-effort: rows in any table on the page that carry a $ amount are
    treated as bidder rows (name = first cell). Awarded pages on Durham post
    full per-bidder tables; other agencies may show none."""
    bidders = []
    for tbl in re.findall(r"<table.*?</table>", raw, flags=re.S | re.I):
        for row in re.findall(r"<tr.*?</tr>", tbl, flags=re.S | re.I):
            cells = [re.sub(r"<[^>]+>", " ", c) for c in
                     re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)]
            cells = [html_mod.unescape(re.sub(r"\s+", " ", c)).strip() for c in cells]
            cells = [c for c in cells if c]
            if len(cells) < 2:
                continue
            amt = None
            for c in cells[1:]:
                m = re.search(r"\$\s*([\d,]+\.?\d*)", c)
                if m:
                    amt = float(m.group(1).replace(",", ""))
                    break
            name = cells[0]
            if amt is not None and name and not re.search(r"(bidder|vendor|supplier|company|name)", name, re.I):
                bidders.append({"name": name, "amount": amt})
    return bidders


def parse_detail(raw: str, list_row: dict) -> dict:
    lines = html_to_lines(raw)
    lv = label_values(lines)
    text = "\n".join(lines)
    winner, value, award_date = None, None, None
    m = re.search(r"Awarded to (.+?) in the amount of \$\s*([\d,]+\.?\d*) on ([A-Za-z]+ \d{1,2}, \d{4})",
                  text)
    if m:
        winner = m.group(1).strip()
        value = float(m.group(2).replace(",", ""))
        try:
            award_date = datetime.strptime(m.group(3), "%B %d, %Y").date().isoformat()
        except ValueError:
            award_date = None
    if not award_date and lv.get("Bid Awarded Date"):
        award_date = _parse_human_date(lv["Bid Awarded Date"])
    bidders = parse_bids_submitted(raw)
    if winner and value is None and bidders:
        hit = [b for b in bidders if b["name"].lower() == winner.lower()]
        if hit:
            value = hit[0]["amount"]
    return {
        "bid_number": lv.get("Bid Number"),
        "title": lv.get("Bid Name") or list_row.get("Title"),
        "bid_classification": lv.get("Bid Classification"),
        "categories_raw": lv.get("Categories") or lv.get("Category"),
        "status": lv.get("Bid Status") or list_row.get("Status"),
        "publish_date": _parse_human_date(lv.get("Bid Publish Date") or lv.get("Publish Date")
                                          or lv.get("Published Date")
                                          or lv.get("Date Published")) or _ms_date(list_row.get("DateAvailable")),
        "closing_date": _parse_human_date(lv.get("Bid Closing Date") or lv.get("Closing Date"))
                        or _ms_date(list_row.get("DateClosing")),
        "award_date": award_date,
        "awarded_supplier_raw": winner,
        "awarded_value": value,
        "all_bidders": bidders,
        "plan_takers_count": list_row.get("PlanTakers"),
    }


def _parse_human_date(v: str | None) -> str | None:
    if not v:
        return None
    v = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", v).strip()
    for fmt in ("%a %b %d, %Y %I:%M:%S %p", "%a %b %d, %Y %I:%M %p",
                "%a %b %d, %Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def scan_site(session: requests.Session, subdomain: str, agency: str,
              awarded_days: int, caps: dict) -> tuple[list[dict], dict]:
    """Discover + fetch details for one bids&tenders site.
    Returns (staging rows, stats)."""
    base = f"https://{subdomain}.bidsandtenders.ca"
    params = homepage_params(session, base)
    today = datetime.now().date()
    stats = {"agency": agency, "discovered": 0, "details": 0, "by_status": {}}
    seen: dict[str, dict] = {}
    jobs = [("Open", "", ""), ("Awarded",
             (today - timedelta(days=awarded_days)).strftime("%m/%d/%Y"),
             today.strftime("%m/%d/%Y")),
            ("Closed", "", ""), ("Unofficial Results", "", "")]
    for status, df, dt_ in jobs:
        try:
            rows = search(session, base, params, status, df, dt_,
                          max_items=caps.get(status, 50) * 2)
        except Exception as e:
            stats[f"error_{status}"] = str(e)[:160]
            continue
        stats["by_status"][status] = len(rows)
        for row in rows:
            gid = row.get("Id")
            if gid and gid not in seen:
                seen[gid] = row
    stats["discovered"] = len(seen)
    # Detail fetches: awarded/closed/unofficial first (they carry values),
    # then open. Capped per status for politeness/runtime.
    ordered = sorted(seen.values(),
                     key=lambda r: {"Awarded": 0, "Unofficial Results": 1,
                                    "Closed": 2, "Open": 3}.get(r.get("Status"), 4))
    counts: dict[str, int] = {}
    out = []
    for row in ordered:
        st = row.get("Status") or ""
        if counts.get(st, 0) >= caps.get(st, 40):
            continue
        counts[st] = counts.get(st, 0) + 1
        url = f"{base}/Module/Tenders/en/Tender/Detail/{row['Id']}"
        try:
            r = http_util.get(session, url)
            parsed = parse_detail(r.text, row)
        except Exception as e:
            stats[f"detail_error"] = str(e)[:160]
            continue
        stats["details"] += 1
        out.append({
            "source_portal": "bidsandtenders",
            "agency": agency,
            "bid_number": parsed["bid_number"],
            "detail_guid": row["Id"],
            "url": url,
            "title": parsed["title"],
            "bid_classification": parsed["bid_classification"],
            "categories_raw": parsed["categories_raw"],
            "status": parsed["status"],
            "publish_date": parsed["publish_date"],
            "closing_date": parsed["closing_date"],
            "award_date": parsed["award_date"],
            "awarded_supplier_raw": parsed["awarded_supplier_raw"],
            "awarded_value": parsed["awarded_value"],
            "all_bidders_json": json.dumps(parsed["all_bidders"], ensure_ascii=False)
                                  if parsed["all_bidders"] else None,
            "plan_takers_count": parsed["plan_takers_count"],
        })
    return out, stats
