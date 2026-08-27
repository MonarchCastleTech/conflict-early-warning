"""Explainable multi-domain conflict precursor model.

The output is an analyst-triage signal, not a probability of conflict.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


PRECURSOR_BASKETS = {
    "force_posture": (
        "mobilization", "mobilisation", "reserve call-up", "troop buildup",
        "troop build-up", "military deployment", "combat readiness",
        "forward deployment", "airspace closure",
    ),
    "protective_action": (
        "embassy closure", "embassy evacuation", "leave immediately",
        "shelter in place", "civilian evacuation", "ordered departure",
        "travel warning", "border closure",
    ),
    "coercive_pressure": (
        "ultimatum", "blockade", "emergency meeting", "article 5",
        "collective defence", "collective defense", "strategic forces",
        "port closure", "maritime exclusion",
    ),
    "systems_disruption": (
        "internet shutdown", "communications blackout", "power outage",
        "gps jamming", "navigation interference", "cyberattack",
        "cyber attack", "undersea cable", "subsea cable",
    ),
}

NATO_POSTURE_TERMS = {
    "mobilization": 3.0,
    "mobilisation": 3.0,
    "article 5": 3.0,
    "forward defence": 2.5,
    "forward defense": 2.5,
    "reinforcement": 2.0,
    "force posture": 2.0,
    "combat readiness": 2.0,
    "defence plans": 1.8,
    "defense plans": 1.8,
    "collective defence": 1.5,
    "collective defense": 1.5,
    "deterrence": 1.0,
    "readiness": 1.0,
}

FRED_SERIES = {
    "wti_crude": {
        "id": "DCOILWTICO", "label": "WTI crude oil",
        "transform": "absolute_pct", "unit": "USD/barrel",
    },
    "vix": {
        "id": "VIXCLS", "label": "CBOE VIX",
        "transform": "positive_pct", "unit": "index",
    },
    "high_yield_spread": {
        "id": "BAMLH0A0HYM2", "label": "US high-yield spread",
        "transform": "positive_delta", "unit": "percentage points",
    },
}

WEIGHTS = {
    "narrative_pressure": 0.35,
    "nato_posture_shift": 0.30,
    "cross_market_dislocation": 0.35,
}


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def _robust_z(current, baseline):
    clean = [float(value) for value in baseline if math.isfinite(float(value))]
    if len(clean) < 4:
        return 0.0
    median = statistics.median(clean)
    mad = statistics.median(abs(value - median) for value in clean)
    if mad > 1e-9:
        return (current - median) / (1.4826 * mad)
    spread = statistics.pstdev(clean)
    return (current - median) / spread if spread > 1e-9 else 0.0


def _source_domain(event):
    domain = str(event.get("domain") or "").strip().lower()
    if domain and domain != "news.google.com":
        return domain
    host = urlparse(str(event.get("url") or event.get("link") or "")).hostname or ""
    return host.lower().removeprefix("www.") or domain


def _event_text(event):
    return " ".join(
        str(event.get(key) or "")
        for key in ("title", "description", "summary", "translated_title")
    ).lower()


def _parse_seen(value):
    text = str(value or "")
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _narrative_component(events):
    signals = []
    domains = set()
    matched_events = []
    for event in events:
        text = _event_text(event)
        domain = _source_domain(event)
        if domain:
            domains.add(domain)
        event_terms = []
        event_baskets = []
        for basket, terms in PRECURSOR_BASKETS.items():
            matches = sorted({term for term in terms if term in text})
            if matches:
                event_baskets.append(basket)
                event_terms.extend(matches)
        if event_terms:
            matched_events.append({
                "title": event.get("title") or "Untitled signal",
                "url": event.get("url") or event.get("link"),
                "source": domain or "unknown",
                "terms": sorted(set(event_terms)),
                "baskets": event_baskets,
            })

    for basket in PRECURSOR_BASKETS:
        rows = [row for row in matched_events if basket in row["baskets"]]
        sources = {row["source"] for row in rows if row["source"] != "unknown"}
        signals.append({
            "id": basket,
            "label": basket.replace("_", " ").title(),
            "event_count": len(rows),
            "independent_sources": len(sources),
            "cross_source_confirmed": len(sources) >= 2,
            "evidence": rows[:3],
        })

    total = max(len(events), 1)
    share = len(matched_events) / total
    confirmed = sum(1 for signal in signals if signal["cross_source_confirmed"])
    score = _clamp(share * 190 + confirmed * 10)
    return {
        "id": "narrative_pressure",
        "label": "Cross-source precursor language",
        "available": bool(events),
        "score": round(score, 1),
        "events_considered": len(events),
        "precursor_event_count": len(matched_events),
        "precursor_share": round(share, 4),
        "independent_sources": len(domains),
        "signals": signals,
    }


def _nato_component(articles, now):
    current_weight = 0.0
    current_count = 0
    weekly = {week: {"weight": 0.0, "count": 0} for week in range(1, 13)}
    evidence = []
    for article in articles:
        seen = _parse_seen(article.get("seendate") or article.get("date"))
        if not seen:
            continue
        age_days = max(0, (now - seen).days)
        text = _event_text(article)
        matches = sorted(term for term in NATO_POSTURE_TERMS if term in text)
        weight = sum(NATO_POSTURE_TERMS[term] for term in matches)
        if age_days < 7:
            current_count += 1
            current_weight += weight
            if matches:
                evidence.append({
                    "title": article.get("title") or "Untitled NATO signal",
                    "url": article.get("url") or article.get("link"),
                    "observed_at": seen.isoformat(),
                    "terms": matches,
                })
        else:
            week = age_days // 7
            if week in weekly:
                weekly[week]["count"] += 1
                weekly[week]["weight"] += weight

    current_rate = current_weight / max(current_count, 1)
    baseline = [
        row["weight"] / row["count"]
        for row in weekly.values() if row["count"] >= 2
    ]
    anomaly_z = _robust_z(current_rate, baseline)
    density_score = _clamp(current_rate * 18)
    anomaly_score = _clamp(max(0.0, anomaly_z) * 22)
    score = density_score if len(baseline) < 4 else 0.55 * density_score + 0.45 * anomaly_score
    return {
        "id": "nato_posture_shift",
        "label": "NATO official-language shift",
        "available": bool(articles),
        "score": round(_clamp(score), 1),
        "articles_considered": len(articles),
        "current_window_articles": current_count,
        "current_weighted_term_rate": round(current_rate, 3),
        "baseline_weeks": len(baseline),
        "anomaly_z": round(anomaly_z, 2),
        "evidence": evidence[:5],
        "query_scope": "Google News RSS results restricted to site:nato.int; 90-day window",
    }


def _cache_path(series_id):
    root = Path(os.path.expanduser("~")) / ".cache" / "conflict-early-warning" / "fred"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{series_id}.json"


def _fetch_fred_series(series_id, now):
    cache = _cache_path(series_id)
    start = (now.date() - timedelta(days=240)).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    try:
        response = requests.get(url, timeout=12, headers={"User-Agent": "conflict-early-warning/2.0"})
        response.raise_for_status()
        rows = []
        for row in csv.DictReader(io.StringIO(response.text)):
            raw = row.get(series_id)
            if not raw or raw == ".":
                continue
            rows.append({"date": row["observation_date"], "value": float(raw)})
        if len(rows) < 10:
            raise ValueError("insufficient FRED observations")
        cache.write_text(json.dumps({"fetched_at": now.isoformat(), "rows": rows}), encoding="utf-8")
        return rows, False
    except Exception:
        if cache.exists():
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                fetched = datetime.fromisoformat(str(payload.get("fetched_at", "")).replace("Z", "+00:00"))
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                age = now - fetched.astimezone(timezone.utc)
                if timedelta(0) <= age <= timedelta(hours=72) and len(payload.get("rows", [])) >= 10:
                    return payload["rows"], True
            except Exception:
                pass
        return [], False


def _market_indicator(key, spec, rows, cached):
    if len(rows) < 10:
        return {"id": key, "label": spec["label"], "available": False, "score": 0.0}
    values = [float(row["value"]) for row in rows]
    changes = []
    for index in range(5, len(values)):
        if spec["transform"].endswith("pct"):
            previous = values[index - 5]
            changes.append((values[index] / previous - 1) * 100 if previous else 0.0)
        else:
            changes.append(values[index] - values[index - 5])
    latest = changes[-1]
    anomaly_z = _robust_z(latest, changes[:-1])
    risk_z = abs(anomaly_z) if spec["transform"].startswith("absolute") else max(0.0, anomaly_z)
    return {
        "id": key,
        "label": spec["label"],
        "available": True,
        "score": round(_clamp((risk_z - 1.0) * 35), 1),
        "latest_value": round(values[-1], 4),
        "five_session_change": round(latest, 4),
        "change_kind": "percent" if spec["transform"].endswith("pct") else "absolute",
        "direction": "up" if latest > 0 else "down" if latest < 0 else "flat",
        "anomaly_z": round(anomaly_z, 2),
        "observed_at": rows[-1]["date"],
        "unit": spec["unit"],
        "cached": cached,
        "source_url": f"https://fred.stlouisfed.org/series/{spec['id']}",
    }


def _market_component(now):
    def load(item):
        key, spec = item
        rows, cached = _fetch_fred_series(spec["id"], now)
        return _market_indicator(key, spec, rows, cached)

    with ThreadPoolExecutor(max_workers=3) as executor:
        indicators = list(executor.map(load, FRED_SERIES.items()))
    available = [row for row in indicators if row["available"]]
    ranked = sorted((row["score"] for row in available), reverse=True)
    score = ranked[0] if len(ranked) == 1 else (0.65 * ranked[0] + 0.35 * ranked[1] if ranked else 0.0)
    return {
        "id": "cross_market_dislocation",
        "label": "Oil, volatility and credit dislocation",
        "available": bool(available),
        "score": round(score, 1),
        "series_available": len(available),
        "indicators": indicators,
    }


def _level(score):
    if score >= 75:
        return "SEVERE"
    if score >= 55:
        return "HEIGHTENED"
    if score >= 35:
        return "WATCH"
    return "ROUTINE"


def build_precursor_warning(events, nato_articles, previous=None, now=None):
    """Build a reproducible precursor snapshot from independent domains."""

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    components = [
        _narrative_component(events),
        _nato_component(nato_articles, now),
        _market_component(now),
    ]
    available = [component for component in components if component["available"]]
    denominator = sum(WEIGHTS[component["id"]] for component in available)
    base_score = (
        sum(component["score"] * WEIGHTS[component["id"]] for component in available) / denominator
        if denominator else 0.0
    )
    elevated = [component["id"] for component in available if component["score"] >= 35]
    concurrence_bonus = 5.0 if len(elevated) >= 2 else 0.0
    score = _clamp(base_score + concurrence_bonus)

    narrative = components[0]
    nato = components[1]
    market = components[2]
    confidence_score = 100 * (
        0.35 * (market["series_available"] / len(FRED_SERIES))
        + 0.30 * min(1.0, len(events) / 30)
        + 0.15 * min(1.0, narrative["independent_sources"] / 10)
        + 0.20 * min(1.0, nato["articles_considered"] / 20)
    )
    confidence = "HIGH" if confidence_score >= 75 else "MEDIUM" if confidence_score >= 45 else "LOW"

    reasons = {
        "narrative_pressure": "Precursor phrases are concentrated across independent public sources.",
        "nato_posture_shift": "Official NATO posture vocabulary is elevated against its recent weekly baseline.",
        "cross_market_dislocation": "Oil, volatility, or credit moved unusually against a robust recent baseline.",
    }
    alerts = [
        {
            "id": component["id"], "title": component["label"],
            "score": component["score"], "level": _level(component["score"]),
            "why": reasons[component["id"]],
        }
        for component in components if component["available"] and component["score"] >= 35
    ]
    alerts.sort(key=lambda row: row["score"], reverse=True)

    history = list((previous or {}).get("history", []))[-179:]
    history.append({
        "timestamp": now.isoformat(), "score": round(score, 1), "level": _level(score),
        "components": {component["id"]: component["score"] for component in components},
    })
    return {
        "issued_at": now.isoformat(),
        "horizon": "0-14 days",
        "classification": "precursor-anomaly-watch-not-event-probability",
        "score": round(score, 1),
        "level": _level(score),
        "confidence": confidence,
        "confidence_score": round(confidence_score, 1),
        "components": components,
        "concurrence": {
            "active": len(elevated) >= 2,
            "elevated_components": elevated,
            "score_bonus": concurrence_bonus,
        },
        "alerts": alerts,
        "history": history,
        "data_health": {
            "events_considered": len(events),
            "independent_sources": narrative["independent_sources"],
            "nato_articles_considered": nato["articles_considered"],
            "market_series_available": market["series_available"],
            "available_components": len(available),
        },
        "method": {
            "name": "Conflict precursor concurrence model v1",
            "aggregation": "availability-renormalized weighted mean plus disclosed 5-point concurrence bonus",
            "weights": WEIGHTS,
            "market_anomaly": "five-session change vs median/MAD baseline; 1.4826*MAD scale",
            "nato_anomaly": "current seven-day weighted term rate vs up to 12 prior weekly rates",
            "narrative_taxonomy": PRECURSOR_BASKETS,
            "nato_terms": NATO_POSTURE_TERMS,
            "warning": "An elevated reading is a triage signal, not proof that conflict will occur.",
        },
        "sources": [
            {"name": "NATO official pages via Google News RSS", "url": "https://www.nato.int/cps/en/natohq/news.htm"},
            {"name": "FRED", "url": "https://fred.stlouisfed.org/"},
            {"name": "GDELT DOC methodology", "url": "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/"},
            {"name": "ViEWS early-warning design", "url": "https://viewsforecasting.org/"},
        ],
    }
