from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_duero_storefront import published_count


class DueroStorefrontCountTests(unittest.TestCase):
    def test_heading_count(self):
        found, method, _ = published_count(
            "<html><h2>51 productos</h2><div>Cuidado Bucal</div></html>"
        )
        self.assertEqual(found, 51)
        self.assertEqual(method, "heading")

    def test_repeated_heading_count_is_safe(self):
        found, _, _ = published_count(
            "<h3><span>116 productos</span></h3>"
            "<h2>116 productos</h2>"
        )
        self.assertEqual(found, 116)

    def test_ambiguous_headers_rejected(self):
        found, method, _ = published_count(
            "<h2>51 productos</h2><h3>50 productos</h3>"
        )
        self.assertIsNone(found)
        self.assertEqual(method, "ambiguous_headings")

    def test_script_product_count_is_not_used(self):
        found, _, _ = published_count(
            "<script>var x='4146 productos';</script>"
            "<div><b>50 productos</b></div>"
        )
        self.assertEqual(found, 50)

    def test_unknown_total_is_not_invented(self):
        found, _, _ = published_count(
            "<html><body><div>Cuidado Bucal</div></body></html>"
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
