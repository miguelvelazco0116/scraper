from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scraper.retailers.chedraui import ChedrauiBlocked, ChedrauiStoreContextError
from test_chedraui import _scrape_category_with_retries


def _records(n: int) -> list[dict]:
    return [
        {
            "sku": f"sku-{i}",
            "url": f"https://www.chedraui.com.mx/item/{i}",
        }
        for i in range(n)
    ]


class MockScraperFactory:
    """Each instance represents a separate new browser/profile session."""

    def __init__(self, steps):
        self.steps = steps
        self.calls = 0
        self.constructed = []

    def __call__(self, **kwargs):
        self.constructed.append(kwargs)
        sequence_index = self.calls
        self.calls += 1
        kind, rows, published = self.steps[sequence_index]
        factory = self

        class FakeScraper:
            run_meta = {
                "displayed_category_products": published,
                "store_context_verified": kind == "success",
                "store_id": "232",
            }

            def scrape_category(self, category, location):
                if kind == "store_error":
                    raise ChedrauiStoreContextError("No fue posible verificar Polanco")
                if kind == "blocked":
                    raise ChedrauiBlocked("sitio bloqueó sesión")
                if kind == "other_error":
                    raise RuntimeError("unexpected")
                return rows

        return FakeScraper()


class RetryCategoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.diagnostics = Path(self.tmp.name)
        self.category = SimpleNamespace(
            id="higiene-bucal",
            url="https://www.chedraui.com.mx/cuidado-e-higiene-personal/higiene-bucal",
        )
        self.store = SimpleNamespace(id="chedraui-polanco", store_id="232")
        self.delays = []

    def run_fake(self, plan, *, retries=2, delay=0):
        factory = MockScraperFactory(plan)
        value = _scrape_category_with_retries(
            self.category, self.store, profile_dir=".chedraui_profile",
            max_pages=100, store_context_retries=retries,
            retry_delay_seconds=delay, scraper_factory=factory,
            sleep_fn=self.delays.append, diagnostics_dir=self.diagnostics,
        )
        return factory, value

    def test_first_store_failure_is_retried_in_new_session(self):
        factory, (scraper, rows, error, history) = self.run_fake([
            ("store_error", [], None),
            ("success", _records(297), 299),
        ], delay=2)
        self.assertEqual(factory.calls, 2)
        self.assertEqual(self.delays, [2])
        self.assertEqual(len(rows), 297)
        self.assertIsNone(error)
        self.assertTrue(scraper.run_meta["store_context_verified"])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["status"], "STORE_CONTEXT_ERROR")
        diagnostic = Path(history[0]["diagnostic"])
        self.assertTrue(diagnostic.is_file())
        payload = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(payload["store_id"], "232")
        self.assertEqual(payload["attempt"], 1)

    def test_three_failed_store_attempts_exhaust_the_limit(self):
        factory, (_, rows, error, history) = self.run_fake([
            ("store_error", [], None),
            ("store_error", [], None),
            ("store_error", [], None),
        ], delay=7)
        self.assertEqual(factory.calls, 3)
        self.assertEqual(self.delays, [7, 7])
        self.assertEqual(rows, [])
        self.assertTrue(error.startswith("STORE_CONTEXT_ERROR:"))
        self.assertEqual(len(history), 3)
        self.assertTrue(all(x["diagnostic"] for x in history))

    def test_blocked_never_retries(self):
        factory, (_, rows, error, history) = self.run_fake([
            ("blocked", [], None),
        ])
        self.assertEqual(factory.calls, 1)
        self.assertEqual(rows, [])
        self.assertTrue(error.startswith("BLOCKED:"))
        self.assertEqual(self.delays, [])
        self.assertEqual(len(history), 1)

    def test_unrelated_error_never_retries(self):
        factory, (_, _, error, _) = self.run_fake([
            ("other_error", [], None),
        ])
        self.assertEqual(factory.calls, 1)
        self.assertTrue(error.startswith("RuntimeError:"))

    def test_overcount_is_retried_but_not_truncated(self):
        factory, (_, rows, error, history) = self.run_fake([
            ("success", _records(386), 384),
            ("success", _records(383), 384),
        ], delay=2)
        self.assertEqual(factory.calls, 2)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 383)
        self.assertEqual(history[0]["status"], "CATALOG_COUNT_OVERFLOW")
        self.assertTrue(Path(history[0]["diagnostic"]).is_file())

    def test_persistent_overcount_is_left_partial_after_retry_limit(self):
        factory, (_, rows, error, history) = self.run_fake([
            ("success", _records(386), 384),
            ("success", _records(386), 384),
            ("success", _records(386), 384),
        ])
        self.assertEqual(factory.calls, 3)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 386)
        self.assertEqual(len(history), 3)
        self.assertTrue(all(x["status"] == "CATALOG_COUNT_OVERFLOW"
                            for x in history))

    def test_retry_can_be_disabled(self):
        factory, (_, rows, error, history) = self.run_fake([
            ("store_error", [], None),
        ], retries=0)
        self.assertEqual(factory.calls, 1)
        self.assertEqual(rows, [])
        self.assertTrue(error.startswith("STORE_CONTEXT_ERROR:"))
        self.assertEqual(len(history), 1)
        self.assertEqual(self.delays, [])

    def test_success_does_not_retry(self):
        factory, (_, rows, error, history) = self.run_fake([
            ("success", _records(298), 299),
        ])
        self.assertEqual(factory.calls, 1)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 298)
        self.assertEqual(len(history), 1)
        self.assertEqual(self.delays, [])


if __name__ == "__main__":
    unittest.main()
