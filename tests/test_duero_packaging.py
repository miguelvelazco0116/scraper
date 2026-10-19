from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from duero_packaging import classify_pack, infer_from_exported_name
from test_duero_mayoreo import product_rows, pack_units


class DueroPackagingTests(unittest.TestCase):
    def check(self, title, variant, count, kind):
        result = classify_pack(title, variant)
        self.assertEqual((result.units, result.package_type), (count, kind))

    def test_variant_single_piece_beats_product_label(self):
        self.check("Cepillo Dental Oral B 4 Pzas", "1 Pieza", 1, "PIEZA")

    def test_explicit_exhibidor_twelve_pieces(self):
        self.check("Cepillo Colgate Premier Clean 12 Pzas", "Exhibidor de 12 Piezas",
                   12, "EXHIBIDOR")

    def test_explicit_exhibidor_six_pieces(self):
        self.check("Cepillo Pro Compact Pza", "Exhibidor de 6 Piezas",
                   6, "EXHIBIDOR")

    def test_variant_case_takes_precedence(self):
        self.check("Detergente 2 Kg", "Caja de 20 Piezas", 20, "CAJA")

    def test_product_contents_are_not_multiplied(self):
        self.check("Cepillo Dental Tira 4 Pzas", "Default Title", None, None)
        self.check("Pasta Dental 160 Ml", "Default Title", None, None)

    def test_named_package_in_title_with_default_variant(self):
        self.check("Paquete de 3 Unidades de Jabón", "Default Title", 3, "PAQUETE")

    def test_unrecognized_variant_is_not_fabricated(self):
        self.check("Caja de 12 Piezas", "Promoción especial", None, None)

    def test_exported_product_name_audit(self):
        value = "Cepillo Dental Colgate Premier Clean 12 Pzas - Exhibidor de 12 Piezas"
        outcome = infer_from_exported_name(value)
        self.assertEqual(outcome.units, 12)
        self.assertEqual(outcome.package_type, "EXHIBIDOR")
        self.assertEqual(pack_units("Pasta Dental 75 ml", "1 Pieza"), 1)

    def test_row_uses_variant_saleable_pack_not_product_quantity(self):
        category = SimpleNamespace(
            department="Perfumería", name="Cuidado bucal",
            subcategory="", sub_subcategory=None, id="cuidado-bucal"
        )
        product = {
            "title": "Cepillo Dental Oral B 4 Pzas",
            "handle": "cepillo-oral-b",
            "vendor": "Oral B",
            "variants": [{
                "id": 123, "title": "1 Pieza", "price": "100.00",
                "compare_at_price": None, "sku": "MERCHANT-123",
                "available": True,
            }]
        }
        rows = product_rows(product, category, "2026-10-19T12:00:00-06:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["units_per_package"], 1)
        self.assertEqual(rows[0]["package_type"], "PIEZA")
        self.assertEqual(rows[0]["price_per_unit"], 100)
        self.assertEqual(rows[0]["is_available"], 1)
        self.assertFalse(rows[0]["store_context_verified"])

    def test_no_phantom_box_from_product_title(self):
        category = SimpleNamespace(
            department="Perfumería", name="Cuidado bucal",
            subcategory="", sub_subcategory=None, id="cuidado-bucal"
        )
        product = {
            "title": "Cepillo dental caja de 12 Piezas",
            "handle": "cepillo-caja",
            "vendor": "M",
            "variants": [{
                "id": 123, "title": "1 Pieza", "price": "20",
                "compare_at_price": None, "sku": "M123",
                "available": False,
            }]
        }
        row = product_rows(product, category, "2026-10-19T12:00:00-06:00")[0]
        self.assertEqual(row["units_per_package"], 1)
        self.assertEqual(row["package_type"], "PIEZA")
        self.assertEqual(row["is_available"], 0)


if __name__ == "__main__":
    unittest.main()
