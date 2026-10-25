"""Parser unit tests with snippets shaped like real detail pages (2026-10-25)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muni.bidsandtenders import parse_detail
from muni.store import classify_goods

PEEL_SNIPPET = """
<html><body>
Bid Classification:</td><td>Goods
Bid Number:</td><td>2026-001P
Bid Name:</td><td>Supply of Office Furniture
Bid Status:</td><td>Awarded
Bid Awarded Date:</td><td>Fri Oct 23, 2026 (EDT)
Published Date:</td><td>Fri Aug 28, 2026 3:00:00 PM (EDT)
Categories:</td><td>Show Categories [+]
Furniture
Office Supplies
Awarded Summary
Awarded to Acme Furniture Inc. in the amount of $ 152,340.50 on August 12, 2026.
</body></html>
"""


def test_parse_detail_award_summary():
    d = parse_detail(PEEL_SNIPPET, {"Title": "x", "Status": "Awarded", "PlanTakers": 7})
    assert d["bid_classification"] == "Goods"
    assert d["bid_number"] == "2026-001P"
    assert d["awarded_supplier_raw"] == "Acme Furniture Inc."
    assert d["awarded_value"] == 152340.50
    assert d["award_date"] == "2026-08-12"
    assert d["publish_date"] == "2026-08-28"
    assert "Furniture" in (d["categories_raw"] or "")


def test_classify_whitelist_and_exclusion():
    assert classify_goods("Supply of office furniture", None)[0] == "56"
    assert classify_goods("Supply of uniforms and apparel", None)[0] == "53"
    assert classify_goods("Supply and delivery of fresh food", None) is None
    assert classify_goods("Purchase of diesel fuel and furniture", None) is None


# --- Provincial sources (v2, 2026-10-25) ---

from muni import apc, bc, seao, sasktenders  # noqa: E402


def test_classify_unspsc_token_rule():
    hit = classify_goods("Some notice", "Items | unspsc_segment:56")
    assert hit == ("56", "unspsc:56")
    # Non-whitelisted segments fall through to keyword matching (no hit here).
    assert classify_goods("Some notice", "Items | unspsc_segment:84") is None
    # Hard exclusions still win over the token.
    assert classify_goods("Fuel supply", "unspsc_segment:56 carburant") is None


BC_CSV = (
    "Issued by Organization,Issued for Organization,Title,Opportunity ID,"
    "Procurement Method,Issued Date,Closing Date,Successful Vendor,"
    "Successful Vendor Address,Successful Vendor Country,Award Total,Date Awarded\n"
    "Ministry of Health,Ministry of Health,Office furniture supply,BC-123,"
    "Direct Award,entered 2026-07-02,2026-07-20,Acme Furniture Ltd.,"
    "\"123 Road, Victoria\",Canada,\"$152,340.50\",2026-08-01\n"
)


def test_bc_parse_and_stage():
    rows = bc.parse_awards_csv(BC_CSV)
    assert len(rows) == 1
    st = bc.to_staging(rows[0])
    assert st["source_portal"] == "bc_opendata"
    assert st["agency"] == "Ministry of Health"
    assert st["awarded_supplier_raw"] == "Acme Furniture Ltd."
    assert st["awarded_value"] == 152340.50
    assert st["publish_date"] == "2026-07-02"  # 'entered ' prefix stripped
    assert st["bid_classification"] == "Goods"  # title-classified (no native field)


def test_seao_release_goods_award():
    rel = {
        "ocid": "ocds-ec9k95-123", "date": "2026-09-05T00:00:00Z",
        "buyer": {"name": "Ville de Test"},
        "tender": {"id": "T-2026-01", "title": "Achat de mobilier de bureau",
                   "status": "complete", "mainProcurementCategory": "goods",
                   "tenderPeriod": {"endDate": "2026-08-20T00:00:00Z"},
                   "numberOfTenderers": 3,
                   "items": [{"description": "Mobilier",
                              "classification": {"scheme": "UNSPSC", "id": "56101500"}}],
                   "tenderers": [{"name": "Bidder A"}]},
        "awards": [{"date": "2026-09-01T00:00:00Z",
                    "value": {"amount": 45000, "currency": "CAD"},
                    "suppliers": [{"name": "Fournisseur Inc."}]}],
    }
    rows = seao.merge_releases([rel])
    assert len(rows) == 1
    r = rows[0]
    assert r["source_portal"] == "seao_opendata"
    assert r["status"] == "Awarded"
    assert r["bid_classification"] == "Goods"
    assert r["awarded_supplier_raw"] == "Fournisseur Inc."
    assert r["awarded_value"] == 45000.0
    assert r["award_date"] == "2026-09-01"
    assert r["publish_date"] == "2026-09-05"
    assert "unspsc_segment:56" in r["categories_raw"]
    # French whitelist keyword maps the segment in the leads classifier too.
    assert classify_goods(r["title"], r["categories_raw"])[0] == "56"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeSession:
    """Serves a canned CKAN package_show + one weekly OCDS file."""

    def __init__(self, release):
        self._release = release

    def get(self, url, timeout=None, **kw):
        if "package_show" in url:
            return _FakeResp({"result": {"resources": [
                {"name": "hebdo_20261005_20261011", "url": "https://x/week-in.json"},
                {"name": "hebdo_20260105_20260111", "url": "https://x/week-out.json"},
                {"name": "mensuel_20260901_20260930", "url": "https://x/month.json"},
            ]}})
        assert url == "https://x/week-in.json"  # out-of-window/monthly never fetched
        return _FakeResp({"releases": [self._release]})


def test_seao_scan_window_selection():
    import datetime as _dt
    rel = {
        "ocid": "ocds-ec9k95-9", "date": "2026-10-06T00:00:00Z",
        "buyer": {"name": "Ville de Test"},
        "tender": {"id": "T-9", "title": "Achat de mobilier", "status": "complete",
                   "mainProcurementCategory": "goods",
                   "items": [{"description": "Mobilier",
                              "classification": {"scheme": "UNSPSC", "id": "56101500"}}]},
        "awards": [{"date": "2026-10-07T00:00:00Z", "value": {"amount": 30000},
                    "suppliers": [{"name": "Fournisseur Inc."}]}],
    }
    rows = seao.scan_seao(_FakeSession(rel),
                          _dt.date(2026, 9, 28), _dt.date(2026, 10, 25))
    assert len(rows) == 1
    assert rows[0]["awarded_supplier_raw"] == "Fournisseur Inc."
    assert rows[0]["awarded_value"] == 30000.0


SASK_SNIPPET = """
<html><body>
<div>Supply of Office Furniture</div>
<div>Organization Name</div><div>City of Saskatoon</div>
<div>Competition #</div><div>26-0123</div>
<div>Opens</div><div>Oct 01, 2026 9:00 AM CST</div>
<div>Closes</div><div>Oct 22, 2026 2:00 PM CST</div>
<div>Status</div><div>Open</div>
<div>Synopsis:</div><div>Supply and delivery of office furniture.</div>
<div>Competition Type:</div><div>Request for Quotation</div>
<div>Categories:</div><div>Goods</div>
<div>UNSPSC Categories:</div><div>56101500 - Furniture</div>
<div>Procurement Mode:</div><div>Traditional</div>
</body></html>
"""


def test_sask_parse_block():
    recs = sasktenders.parse_list_page(SASK_SNIPPET)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["agency"] == "City of Saskatoon"
    assert rec["bid_number"] == "26-0123"
    st = sasktenders.to_staging(rec, "https://www.sasktenders.ca/")
    assert st["publish_date"] == "2026-10-01"
    assert st["closing_date"] == "2026-10-22"
    assert st["bid_classification"] == "Goods"
    assert "unspsc_segment:56" in st["categories_raw"]
    assert sasktenders.parse_sask_date("Sep 21, 2026 1:00 PM CST") == "2026-09-21"


def test_apc_item_and_award_rows():
    item = {"id": "guid-1", "referenceNumber": "AB-2026-01234",
            "shortTitle": "Supply of laptops", "title": "long title",
            "solicitationNumber": "RFQ-1", "categoryCode": "GD",
            "statusCode": "AWARD", "contractingOrganization": "City of Edmonton",
            "commodityCodes": ["43211500"], "commodityCodeTitles": ["Computers"],
            "postDateTime": "2026-09-01T08:00:00", "closeDateTime": "2026-09-30T14:00:00"}
    rows = apc.item_to_rows(item, [{"alternativeSupplierDisplayName": "Tech Co",
                                    "amount": 88000.5, "awardDate": "2026-10-05T00:00:00"}])
    assert len(rows) == 1
    r = rows[0]
    assert r["source_portal"] == "alberta_apc"
    assert r["bid_classification"] == "Goods"
    assert r["status"] == "Awarded"
    assert r["awarded_supplier_raw"] == "Tech Co"
    assert r["awarded_value"] == 88000.5
    assert r["publish_date"] == "2026-09-01"
    assert "unspsc_segment:43" in r["categories_raw"]
    assert apc.split_ref("AB-2026-01234") == (2026, 1234)
    f = apc.make_filter(__import__("datetime").date(2026, 6, 27),
                        __import__("datetime").date(2026, 10, 25),
                        __import__("datetime").date(2026, 6, 27),
                        __import__("datetime").date(2027, 12, 31), statuses=["AWARD"])
    assert f["statuses"] == [{"value": "AWARD", "selected": True}]
    assert f["postDateRange"] == "$$custom"
