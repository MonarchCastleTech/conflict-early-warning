import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import conflict_early_warning_pipeline as pipeline


def test_retained_news_snapshot_expires_after_72_hours():
    previous = {
        "meta": {"generated": (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()},
        "live_data": {"news_articles": [{"title": "stale"}]},
    }
    live = {}
    assert pipeline.retain_previous(live, previous) == []
    assert live == {}


def test_generated_snapshot_contains_warning_contract():
    data = json.loads((ROOT / "data" / "output.json").read_text(encoding="utf-8"))
    assert "early_warning" in data
    warning = data["early_warning"]
    assert warning["classification"] == "precursor-anomaly-watch-not-event-probability"
    assert warning["horizon"] == "0-14 days"
    assert len(warning["components"]) == 3


def test_main_reports_no_valid_snapshot_as_failure(monkeypatch):
    monkeypatch.setattr(pipeline, "load_config", lambda: {"project": {"id": "test", "name": "test"}})
    monkeypatch.setattr(pipeline, "load_previous", lambda: {})
    monkeypatch.setattr(pipeline, "extract_live_data", lambda config: {})
    assert pipeline.main() is False
