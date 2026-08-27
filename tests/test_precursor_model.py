import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import precursor_model as model


def _article(title, days_ago=0, domain="example.org"):
    seen = datetime(2026, 8, 27, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return {
        "title": title,
        "url": f"https://{domain}/{days_ago}/{title[:8]}",
        "domain": domain,
        "seendate": seen.strftime("%Y%m%dT%H%M%SZ"),
    }


def test_three_domain_warning_is_explainable_and_not_a_probability(monkeypatch):
    monkeypatch.setattr(model, "_market_component", lambda now: {
        "id": "cross_market_dislocation",
        "label": "Oil, volatility and credit dislocation",
        "available": True,
        "score": 60.0,
        "series_available": 3,
        "indicators": [],
    })
    events = [
        _article("Embassy evacuation after military deployment", domain="one.example"),
        _article("Airspace closure and reserve call-up", domain="two.example"),
    ]
    nato = [_article("NATO reinforcement and combat readiness", days_ago=0, domain="nato.int")]
    for week in range(1, 9):
        nato.extend([
            _article("NATO meeting", days_ago=week * 7 + 1, domain="nato.int"),
            _article("NATO statement", days_ago=week * 7 + 2, domain="nato.int"),
        ])

    warning = model.build_precursor_warning(
        events, nato, now=datetime(2026, 8, 27, tzinfo=timezone.utc)
    )

    assert warning["classification"] == "precursor-anomaly-watch-not-event-probability"
    assert warning["horizon"] == "0-14 days"
    assert warning["method"]["weights"] == model.WEIGHTS
    assert {row["id"] for row in warning["components"]} == set(model.WEIGHTS)
    assert warning["concurrence"]["active"] is True
    assert warning["concurrence"]["score_bonus"] == 5.0


def test_missing_components_are_renormalized(monkeypatch):
    monkeypatch.setattr(model, "_market_component", lambda now: {
        "id": "cross_market_dislocation", "label": "market", "available": False,
        "score": 0.0, "series_available": 0, "indicators": [],
    })
    warning = model.build_precursor_warning(
        [_article("Embassy evacuation", domain="one.example")], [],
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    narrative = next(row for row in warning["components"] if row["id"] == "narrative_pressure")
    assert warning["score"] == narrative["score"]
    assert warning["data_health"]["available_components"] == 1


def test_fred_cache_older_than_72_hours_is_rejected(tmp_path, monkeypatch):
    cache = tmp_path / "fred.json"
    cache.write_text(json.dumps({
        "fetched_at": "2026-08-20T00:00:00+00:00",
        "rows": [{"date": f"2026-08-{day:02d}", "value": day} for day in range(1, 15)],
    }), encoding="utf-8")
    monkeypatch.setattr(model, "_cache_path", lambda series_id: cache)
    monkeypatch.setattr(model.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))

    rows, cached = model._fetch_fred_series(
        "DCOILWTICO", datetime(2026, 8, 27, tzinfo=timezone.utc)
    )
    assert rows == []
    assert cached is False
