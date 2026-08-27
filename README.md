# Conflict Early Warning System

[![Pages](https://github.com/MonarchCastleTech/conflict-early-warning/actions/workflows/pipeline.yml/badge.svg)](https://github.com/MonarchCastleTech/conflict-early-warning/actions/workflows/pipeline.yml)

Open-source indicators for monitoring changes in conflict risk.

**Live dashboard:** https://monarchcastletech.github.io/conflict-early-warning/

## Run locally

```bash
python -m pip install -r requirements.txt
python pipeline/conflict_early_warning_pipeline.py
python -m http.server 8000
```

Open `http://localhost:8000`. Direct `file://` access cannot fetch `data/output.json` in modern browsers.

## Automation

GitHub Actions refreshes public data every six hours and deploys the static dashboard to GitHub Pages. AI briefs are optional: configure `OPENROUTER_API_KEY` as a repository Actions secret. Without it, core collection and dashboard deployment remain available.

## Data notice

Source availability varies. The dashboard identifies its generation time and operating mode in `data/output.json`. Treat indicators as decision-support signals, not verified ground truth.

## Precursor methodology

The 0–14 day early-warning desk is a separate triage ensemble, not a probability forecast:

- 35% cross-source precursor language: force posture, protective action, coercive pressure, and systems disruption.
- 30% NATO official-language shift: weighted terminology in the latest seven days versus up to twelve prior weeks.
- 35% cross-market dislocation: five-session changes in WTI crude, VIX, and US high-yield spreads versus median/MAD baselines.

Unavailable components are excluded and remaining weights are renormalized. Two independently elevated components add a disclosed five-point concurrence bonus. FRED cache entries expire after 72 hours; retained news snapshots also expire after 72 hours. `confidence` measures data coverage only. Component scores, evidence, health, weights, terms, source links, and rolling history are published in `data/output.json` for audit and reproduction.

## Brand

Part of Monarch Castle Technologies. See [BRAND.md](BRAND.md) for approved asset use.
