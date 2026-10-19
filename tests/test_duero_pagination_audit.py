from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from duero_pagination_audit import audit_collection


def product(i: int):
    return {
        "id": 100000 + i,
        "handle": "producto-" + str(i),
        "title": "Producto " + str(i),
        "variants": [
            {
                "id": 900000 + i,
                "sku": "SKU-" + str(i),
                "price": "15.00",
                "compare_at_price": None,
                "available": True,
            }
        ],
    }


class Response:
    status = 200

    def __init__(self, records):
        self.records = records

    def json(self):
        return {"products": self.records}


class Request:
    def __init__(self, all_products, *, repeat_first_page=False,
                 hide_last_control=False, changed_prices=False):
        self.products = all_products
        self.repeat_first_page = repeat_first_page
        self.hide_last_control = hide_last_control
        self.changed_prices = changed_prices
        self.calls = []

    def get(self, url, timeout=None):
        parsed = parse_qs(urlsplit(url).query)
        limit = int(parsed["limit"][0])
        page = int(parsed["page"][0])
        self.calls.append((limit, page))
        if self.repeat_first_page and limit == 50 and page > 1:
            page = 1
        start = (page - 1) * limit
        records = deepcopy(self.products[start:start+limit])
        if self.hide_last_control and limit == 250:
            records = records[:-1] if records else []
        if self.changed_prices and limit == 250:
            for item in records:
                item["variants"][0]["price"] = "16.00"
        return Response(records)


class DueroPaginationAuditTests(unittest.TestCase):
    def category(self):
        return SimpleNamespace(
            id="detergentes",
            url="https://dueromayoreo.mayoreoenlinea.mx/collections/detergentes",
        )

    def test_checks_every_page_and_explicit_empty_terminal(self):
        request = Request([product(i) for i in range(125)])
        objects, audit, pages = audit_collection(
            request, self.category(), max_pages=10, delay_seconds=0
        )
        self.assertEqual(len(objects), 125)
        self.assertTrue(audit["pagination_verified"])
        self.assertEqual(audit["terminal_page_limit50"], 4)
        self.assertEqual(audit["terminal_page_limit250"], 2)
        self.assertEqual(request.calls, [
            (50, 1), (50, 2), (50, 3), (50, 4),
            (250, 1), (250, 2)
        ])
        self.assertEqual(len(pages), 6)

    def test_detects_api_ignoring_page(self):
        request = Request([product(i) for i in range(65)], repeat_first_page=True)
        _, audit, _ = audit_collection(
            request, self.category(), max_pages=10, delay_seconds=0
        )
        self.assertFalse(audit["pagination_verified"])
        self.assertTrue(any("repetido" in err for err in audit["errors"]))

    def test_detects_mismatch_between_limits(self):
        request = Request([product(i) for i in range(12)], hide_last_control=True)
        _, audit, _ = audit_collection(
            request, self.category(), max_pages=10, delay_seconds=0
        )
        self.assertFalse(audit["pagination_verified"])
        self.assertFalse(audit["independent_match"])

    def test_detects_price_changed_between_scans(self):
        request = Request([product(i) for i in range(12)], changed_prices=True)
        _, audit, _ = audit_collection(
            request, self.category(), max_pages=10, delay_seconds=0
        )
        self.assertFalse(audit["pagination_verified"])
        self.assertTrue(any("SKU/precio/disponibilidad" in err for err in audit["errors"]))

    def test_short_first_page_still_verifies_page_two(self):
        request = Request([product(i) for i in range(12)])
        _, audit, _ = audit_collection(
            request, self.category(), max_pages=10, delay_seconds=0
        )
        self.assertTrue(audit["pagination_verified"])
        self.assertIn((50, 2), request.calls)
        self.assertIn((250, 2), request.calls)

    def test_limited_pages_cannot_be_certified(self):
        request = Request([product(i) for i in range(250)])
        _, audit, _ = audit_collection(
            request, self.category(), max_pages=2, delay_seconds=0
        )
        self.assertFalse(audit["pagination_verified"])
        self.assertTrue(any("página final vacía" in err for err in audit["errors"]))


if __name__ == "__main__":
    unittest.main()
