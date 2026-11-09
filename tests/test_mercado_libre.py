from __future__ import annotations

import unittest

from scraper.retailers.mercado_libre import (
    MercadoLibreDataError,
    normalize_api_item,
    normalize_api_results,
)


def sample():
    return {
        "id": "MLM12345678",
        "title": "Pasta dental ejemplo",
        "price": 45.50,
        "currency_id": "MXN",
        "permalink": "https://articulo.mercadolibre.com.mx/MLM-12345678-test",
        "attributes": [{"id": "BRAND", "value_name": "Marca de prueba"}],
    }


class MercadoLibreTests(unittest.TestCase):
    def test_basic_normalization(self):
        row = normalize_api_item(sample(), category="Cuidado bucal", category_id="bucal")
        self.assertEqual(row["sku"], "MLM12345678")
        self.assertEqual(row["price_current"], 45.5)
        self.assertEqual(row["brand"], "Marca de prueba")
        self.assertIsNone(row["postal_code"])
        self.assertFalse(row["store_context_verified"])

    def test_reject_wrong_currency(self):
        item = sample()
        item["currency_id"] = "ARS"
        with self.assertRaises(MercadoLibreDataError):
            normalize_api_item(item, category="Bucal", category_id="bucal")

    def test_reject_unsafe_link(self):
        item = sample()
        item["permalink"] = "https://malicious.example/offer"
        with self.assertRaises(MercadoLibreDataError):
            normalize_api_item(item, category="Bucal", category_id="bucal")

    def test_reject_invalid_price(self):
        for value in [None, 0, -1, "NaN", "not a price"]:
            item = sample()
            item["price"] = value
            with self.subTest(value=value), self.assertRaises(MercadoLibreDataError):
                normalize_api_item(item, category="Bucal", category_id="bucal")

    def test_duplicates_reported_not_silently_merged(self):
        valid, rejected = normalize_api_results(
            [sample(), sample()], category="Bucal", category_id="bucal"
        )
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 1)


if __name__ == "__main__":
    unittest.main()
