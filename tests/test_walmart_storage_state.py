import json

from scraper.retailers.walmart_storage_state import WalmartStorageStateScraper


def test_load_wrapped_storage_state(tmp_path):
    path = tmp_path / "walmart_session.json"
    path.write_text(
        json.dumps(
            {
                "storage_state": {
                    "cookies": [{"name": "store", "value": "2344", "domain": ".walmart.com.mx", "path": "/", "expires": -1, "httpOnly": False, "secure": True, "sameSite": "Lax"}],
                    "origins": [],
                },
                "session_storage": {"www.walmart.com.mx": {"postalCode": "11220"}},
            }
        ),
        encoding="utf-8",
    )

    scraper = WalmartStorageStateScraper(path)
    storage_state, session_storage = scraper._load_state()

    assert storage_state["cookies"][0]["value"] == "2344"
    assert session_storage["www.walmart.com.mx"]["postalCode"] == "11220"


def test_load_standard_playwright_storage_state(tmp_path):
    path = tmp_path / "state.json"
    payload = {"cookies": [], "origins": []}
    path.write_text(json.dumps(payload), encoding="utf-8")

    scraper = WalmartStorageStateScraper(path)
    storage_state, session_storage = scraper._load_state()

    assert storage_state == payload
    assert session_storage == {}
