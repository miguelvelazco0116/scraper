import json
import time

import pytest

from scraper.retailers.soriana import SorianaDeferred, SorianaScraper


def test_soriana_circuit_reports_remaining_time(tmp_path):
    scraper = SorianaScraper(
        profile_dir=tmp_path,
        circuit_cooldown_seconds=600,
    )
    scraper.circuit_file.write_text(
        json.dumps(
            {
                "blocked_at_epoch": time.time(),
                "cooldown_seconds": 600,
            }
        ),
        encoding="utf-8",
    )

    remaining = scraper.circuit_remaining_seconds()
    assert 0 < remaining <= 600

    with pytest.raises(SorianaDeferred):
        scraper._assert_circuit_ready()


def test_soriana_circuit_expires(tmp_path):
    scraper = SorianaScraper(
        profile_dir=tmp_path,
        circuit_cooldown_seconds=600,
    )
    scraper.circuit_file.write_text(
        json.dumps(
            {
                "blocked_at_epoch": time.time() - 700,
                "cooldown_seconds": 600,
            }
        ),
        encoding="utf-8",
    )

    assert scraper.circuit_remaining_seconds() == 0
    scraper._assert_circuit_ready()


def test_soriana_clear_circuit(tmp_path):
    scraper = SorianaScraper(profile_dir=tmp_path)
    scraper.circuit_file.write_text("{}", encoding="utf-8")
    scraper._clear_circuit()
    assert not scraper.circuit_file.exists()
