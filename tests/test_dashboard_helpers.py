"""No network and no production master required."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from dashboard.azure_sql import connection_string, load_settings, save_settings
from dashboard.data import metrics_by_retailer, safe_log_tail, safe_cli_command

class DashboardHelpersTests(unittest.TestCase):
    def test_missing_optional_availability_is_not_a_failed_rate(self):
        rows = pd.DataFrame([{
            "retailer": "Farmacias San Pablo", "sku": "111",
            "price_current": 40, "url": "https://example.org/a",
            "is_available": None,
        }])
        m = metrics_by_retailer(rows, {4: {"Farmacias San Pablo": 4}})
        self.assertTrue(pd.isna(m.iloc[0]["disponibilidad_pct"]))
        self.assertEqual(m.iloc[0]["precio_pct"], 100)

    def test_duero_online_scope_does_not_fake_store_context(self):
        rows = pd.DataFrame([{
            "retailer": "Mayorista Duero", "sku": "42", "price_current": 12,
            "url": "https://example.org/", "store_context_verified": False,
            "is_available": 0,
        }])
        m = metrics_by_retailer(rows, {5: {"Mayorista Duero": 6}})
        self.assertTrue(bool(m.iloc[0]["alcance_online"]))
        self.assertEqual(m.iloc[0]["contexto_tienda_pct"], 0)
        self.assertEqual(m.iloc[0]["disponibilidad_pct"], 100)

    def test_azure_config_does_not_store_password(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            save_settings(root, {
                "server": "demo.database.windows.net", "database": "retail",
                "username": "readonly", "port": 1433,
                "authentication": "SQL Password", "password": "NEVER_SAVE",
            })
            saved = (root / "config/local/azure_sql.json").read_text()
            self.assertNotIn("NEVER_SAVE", saved)
            self.assertNotIn('"password"', saved)
            self.assertEqual(load_settings(root)["database"], "retail")

    def test_connection_string_injection_rejected(self):
        with self.assertRaises(ValueError):
            connection_string({
                "server": "demo.database.windows.net;TrustServerCertificate=yes",
                "database": "retail", "username": "readonly",
                "authentication": "SQL Password",
            }, "secret")

    def test_tls_is_mandatory(self):
        cnx = connection_string({
            "server": "demo.database.windows.net", "database": "retail",
            "username": "readonly", "authentication": "SQL Password",
        }, "x;secret}")
        self.assertIn("Encrypt=yes;TrustServerCertificate=no", cnx)
        self.assertIn("PWD={x;secret}}}", cnx)

    def test_redact_log_secrets(self):
        with TemporaryDirectory() as folder:
            log = Path(folder) / "example.log"
            log.write_text("ERROR token=ABC123 PWD=SECRET\n", encoding="utf-8")
            content = safe_log_tail(log)
            self.assertNotIn("ABC123", content)
            self.assertNotIn("SECRET", content)

    def test_cli_runner_allowlist(self):
        root = Path("C:/Proyectos/scraper")
        self.assertIn("test_heb.ps1", safe_cli_command(root, 5, "HEB"))
        self.assertIn("No hay runner", safe_cli_command(root, 2, "Soriana"))

if __name__ == "__main__":
    unittest.main()
