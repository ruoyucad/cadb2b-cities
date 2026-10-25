"""Entrypoint: one full municipal GOODS-tender scan (idempotent).

Usage (from the cadb2b-run root, using the project venv):
    .venv/bin/python -m muni.scan [--days N] [--skip-toronto]

What one run does:
  1. bids&tenders GTA cluster (Peel / Mississauga / Brampton / York /
     Durham / Halton): discover via the portal's own Tender/Search JSON
     endpoint (Open + recent Awarded + Closed + Unofficial Results),
     fetch detail pages, parse labels/award/bidders.
  2. Toronto: awarded contracts from Toronto Open Data (CKAN datastore).
  3. bids&tenders buyers outside Ontario (Saskatoon SK, Halifax NS,
     Moncton NB, St. John's NL, Yellowknife NWT) — same Ontario pattern.
  4. BC: ministry contract awards from the BC Data Catalogue (BC Bid
     itself is behind a reCAPTCHA browser check — see README).
  5. Quebec: SEAO notices/awards from Données Québec open data (OCDS
     weekly files; the SEAO portal sells document/award access).
  6. Saskatchewan: SaskTenders public lists (winner names/amounts are
     in login-gated Tender Results PDFs — awarded rows stay thin).
  7. Alberta: Purchasing Connection public JSON API (search + public
     posting documents with supplier names and award amounts).
  8. Append snapshots to stg_muni_tenders and rebuild
     mart_muni_goods_leads in the shared DuckDB (MUNI_DB_PATH).

Safe to re-run: staging is append-only snapshots; the mart is rebuilt from
the latest snapshot per (portal, agency, bid number, supplier).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # allow `python muni/scan.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from muni import apc, bc, bidsandtenders, http, seao, sasktenders, store, toronto
    from muni.config import (AWARDED_WINDOW_DAYS, AWARDED_WINDOW_DAYS_FIRST,
                             BIDSANDTENDERS_SITES, BIDSANDTENDERS_SITES_NATIONAL,
                             DB_PATH, DETAIL_CAPS)
else:
    from . import apc, bc, bidsandtenders, http, seao, sasktenders, store, toronto
    from .config import (AWARDED_WINDOW_DAYS, AWARDED_WINDOW_DAYS_FIRST,
                         BIDSANDTENDERS_SITES, BIDSANDTENDERS_SITES_NATIONAL,
                         DB_PATH, DETAIL_CAPS)


def main() -> int:
    ap = argparse.ArgumentParser(description="Municipal GOODS tender scan")
    ap.add_argument("--days", type=int, default=None,
                    help="awarded discovery window (default: 120 first run, 21 after)")
    ap.add_argument("--skip-toronto", action="store_true")
    ap.add_argument("--skip-national", action="store_true",
                    help="skip non-Ontario bidsandtenders buyers")
    ap.add_argument("--skip-bc", action="store_true")
    ap.add_argument("--skip-seao", action="store_true")
    ap.add_argument("--skip-sask", action="store_true")
    ap.add_argument("--skip-apc", action="store_true")
    ap.add_argument("--sites", default=None, help="comma-separated subdomains to limit the run")
    args = ap.parse_args()

    t0 = time.time()
    con = store.connect(DB_PATH)
    have = con.execute("SELECT count(*) FROM stg_muni_tenders").fetchone()[0]
    days = args.days or (AWARDED_WINDOW_DAYS_FIRST if have == 0 else AWARDED_WINDOW_DAYS)
    print(f"[muni] db={DB_PATH} existing_snapshots={have} awarded_window={days}d")

    session = http.make_session()
    sites = BIDSANDTENDERS_SITES
    if args.sites:
        want = {s.strip() for s in args.sites.split(",")}
        sites = [s for s in sites if s[0] in want]

    total_new = 0
    for sub, agency in sites:
        try:
            rows, stats = bidsandtenders.scan_site(session, sub, agency, days, DETAIL_CAPS)
        except Exception as e:
            print(f"[muni] {agency}: FAILED {type(e).__name__}: {str(e)[:200]}")
            continue
        n = store.insert_snapshots(con, rows)
        total_new += n
        print(f"[muni] {agency}: discovered={stats['discovered']} details={stats['details']} "
              f"inserted={n} by_status={stats['by_status']}"
              + (f" errors={[k for k in stats if 'error' in k]}" if any('error' in k for k in stats) else ""))

    if not args.skip_toronto:
        try:
            rows, stats = toronto.scan(session)
            n = store.insert_snapshots(con, rows)
            total_new += n
            print(f"[muni] City of Toronto: rows={stats['discovered']} inserted={n}")
        except Exception as e:
            print(f"[muni] City of Toronto: FAILED {type(e).__name__}: {str(e)[:200]}")

    if not args.skip_national:
        national = BIDSANDTENDERS_SITES_NATIONAL
        if args.sites:
            want = {s.strip() for s in args.sites.split(",")}
            national = [s for s in national if s[0] in want]
        for sub, agency in national:
            try:
                rows, stats = bidsandtenders.scan_site(session, sub, agency, days, DETAIL_CAPS)
            except Exception as e:
                print(f"[muni] {agency}: FAILED {type(e).__name__}: {str(e)[:200]}")
                continue
            n = store.insert_snapshots(con, rows)
            total_new += n
            print(f"[muni] {agency}: discovered={stats['discovered']} details={stats['details']} "
                  f"inserted={n} by_status={stats['by_status']}"
                  + (f" errors={[k for k in stats if 'error' in k]}" if any('error' in k for k in stats) else ""))

    from datetime import date as _date, timedelta as _td
    window_start = _date.today() - _td(days=days)
    today = _date.today()

    if not args.skip_bc:
        try:
            rows = bc.scan_bc(session, window_start)
            n = store.insert_snapshots(con, rows)
            total_new += n
            print(f"[muni] British Columbia (BC Data Catalogue awards): rows={len(rows)} inserted={n}")
        except Exception as e:
            print(f"[muni] British Columbia: FAILED {type(e).__name__}: {str(e)[:200]}")

    if not args.skip_seao:
        try:
            rows = seao.scan_seao(session, window_start, today)
            n = store.insert_snapshots(con, rows)
            total_new += n
            print(f"[muni] Quebec (SEAO open data): rows={len(rows)} inserted={n}")
        except Exception as e:
            print(f"[muni] Quebec (SEAO): FAILED {type(e).__name__}: {str(e)[:200]}")

    if not args.skip_sask:
        try:
            rows = sasktenders.scan_sask(session, window_start)
            n = store.insert_snapshots(con, rows)
            total_new += n
            print(f"[muni] Saskatchewan (SaskTenders): rows={len(rows)} inserted={n}")
        except Exception as e:
            print(f"[muni] Saskatchewan: FAILED {type(e).__name__}: {str(e)[:200]}")

    if not args.skip_apc:
        try:
            rows = apc.scan_apc(session, window_start, today)
            n = store.insert_snapshots(con, rows)
            total_new += n
            print(f"[muni] Alberta (Purchasing Connection API): rows={len(rows)} inserted={n}")
        except Exception as e:
            print(f"[muni] Alberta (APC): FAILED {type(e).__name__}: {str(e)[:200]}")

    leads = store.rebuild_mart(con)
    per_buyer = con.execute(
        "SELECT agency, count(*) FROM stg_muni_tenders GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print(f"[muni] staging total={con.execute('SELECT count(*) FROM stg_muni_tenders').fetchone()[0]} "
          f"new={total_new} leads={leads}")
    for b, c in per_buyer:
        print(f"       {b}: {c}")
    con.close()
    print(f"[muni] done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
