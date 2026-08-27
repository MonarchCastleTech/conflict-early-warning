# -*- coding: utf-8 -*-
"""Canonical project data pipeline. Identity and sources come from config.yaml."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_exchange_rates, fetch_google_news_rss, safe_fetch
from openrouter_llm import analyze_with_llm
from precursor_model import build_precursor_warning

SNAPSHOT_SIZE = 50
EVENT_LIMIT = 15


def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_previous():
    try:
        with open("data/output.json", "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def extract_live_data(config):
    live = {}
    query = config.get("news_query") or "geopolitical risk"
    print(f"[LIVE] News query: {query}")

    nato_query = (
        'site:nato.int (deterrence OR readiness OR reinforcement OR mobilization '
        'OR "collective defence" OR "Article 5" OR "force posture") when:90d'
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        news_future = executor.submit(safe_fetch, fetch_google_news_rss, query, SNAPSHOT_SIZE)
        nato_future = executor.submit(safe_fetch, fetch_google_news_rss, nato_query, 100)
        articles = news_future.result() or []
        nato_articles = nato_future.result() or []
    if articles:
        live["news_articles"] = articles[:SNAPSHOT_SIZE]
        print(f"  News RSS: {len(articles)} articles")
    if nato_articles:
        live["nato_articles"] = nato_articles[:100]
        print(f"  NATO official-page monitor: {len(nato_articles)} articles")

    if config.get("include_forex"):
        rates = safe_fetch(fetch_exchange_rates, "USD")
        if rates:
            live["exchange_rates"] = rates[:20]
            print(f"  Forex: {len(live['exchange_rates'])} rates")

    return live


def retain_previous(live, previous):
    notes = []
    previous_live = previous.get("live_data") or {}
    try:
        generated = datetime.fromisoformat(str(previous.get("meta", {}).get("generated", "")).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        previous_fresh = 0 <= (datetime.now(timezone.utc) - generated).total_seconds() <= 72 * 3600
    except ValueError:
        previous_fresh = False
    limits = {"news_articles": SNAPSHOT_SIZE, "nato_articles": 100}
    for key, limit in limits.items():
        if not live.get(key) and previous_fresh and previous_live.get(key):
            live[key] = previous_live[key][:limit]
            notes.append(f"{key} unavailable; retained a snapshot less than 72 hours old.")
            print(f"  {key}: retained {len(live[key])} items from previous run")
    return notes


def build_stats(articles, feeds):
    domains = len({a.get("domain") for a in articles if a.get("domain")})
    tones = [float(a.get("tone")) for a in articles if isinstance(a.get("tone"), (int, float))]
    mean_tone = sum(tones) / len(tones) if tones else 0.0
    tone_index = round(max(0, min(100, 50 + mean_tone * 5)))
    direction = "positive" if mean_tone > 0.2 else ("negative" if mean_tone < -0.2 else "neutral")
    return [
        {"label": "Articles Tracked", "value": str(len(articles)), "delta": "live" if articles else "none"},
        {"label": "News Domains", "value": str(domains), "delta": "deduplicated"},
        {"label": "Tone Index", "value": f"{tone_index}/100 ({direction})", "delta": "news scale"},
        {"label": "Live Feeds", "value": str(feeds), "delta": "connected"},
    ]


def main():
    config = load_config()
    project = (config.get("project") or {}).get("id", "unknown-project")
    title = (config.get("project") or {}).get("name", project)
    print(f"=== {title} pipeline ===")

    previous = load_previous()
    live = extract_live_data(config)
    notes = retain_previous(live, previous)

    articles = live.get("news_articles", [])
    nato_articles = live.get("nato_articles", [])
    fresh_news = bool(articles) and not any(note.startswith("news_articles") for note in notes)
    mode = "live" if fresh_news else ("partial" if articles else "unavailable")

    if not articles:
        print("[ERROR] No current or valid retained news snapshot; preserving last-good output")
        return False

    llm_summary = ""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key and articles:
        print("[LLM] Analyzing with OpenRouter...")
        llm_summary = analyze_with_llm(
            {
                "meta": {"project": project, "mode": mode},
                "events": articles[:5],
                "stats": build_stats(articles, len(live)),
            },
            config.get("openrouter"),
            api_key,
        )
        if llm_summary:
            print("[LLM] Summary received")

    output = {
        "meta": {
            "project": project,
            "generated": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "sources": [key for key, value in live.items() if value],
            "source_notes": notes,
            "version": "2.0.0",
        },
        "stats": build_stats(articles, len(live)),
        "live_data": live,
        "entities": [],
        "events": articles[:EVENT_LIMIT],
        "timeseries": [],
        "llm_summary": llm_summary,
        "early_warning": build_precursor_warning(
            articles,
            nato_articles,
            previous=previous.get("early_warning", {}),
        ),
    }

    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", "output.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)

    size = os.path.getsize(out_path)
    print(f"Done. {out_path} ({size} bytes) mode={mode} articles={len(articles)}")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if main() else 2)
