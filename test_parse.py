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
