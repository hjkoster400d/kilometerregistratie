"""Shared test fixtures. Patches geocode.city_for globally to avoid network calls."""
import pytest


@pytest.fixture(autouse=True)
def _mock_geocode(monkeypatch):
    """Replace city_for with a deterministic fake for all tests."""

    async def _fake_city_for(lat, lon):
        if lat is None or lon is None:
            return None
        return f"TestCity({lat:.1f},{lon:.1f})"

    monkeypatch.setattr("app.geocode.city_for", _fake_city_for)
    # The tracker imports city_for by name, so patch its bound reference too.
    monkeypatch.setattr("app.tracker.city_for", _fake_city_for)
