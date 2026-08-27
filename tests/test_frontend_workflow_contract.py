from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")


def test_warning_runtime_and_methodology_are_public():
    for runtime_id in (
        "early-warning-score", "early-warning-level", "early-warning-confidence",
        "early-warning-horizon", "early-warning-issued", "early-warning-components",
        "early-warning-alerts", "early-warning-health",
    ):
        assert f'id="{runtime_id}"' in HTML
        assert runtime_id in JS
    assert "not conflict probability" in HTML
    assert "median/MAD" in HTML
    assert "ACLED" not in HTML
    assert "CHIRPS" not in HTML


def test_workflow_fails_loudly_and_retries_pages():
    assert "python -m pytest -q" in WORKFLOW
    assert "continue-on-error: true" in WORKFLOW
    assert WORKFLOW.count("actions/upload-pages-artifact") == 1
    assert WORKFLOW.count("actions/deploy-pages@v5") == 2
    assert "git pull --rebase -X theirs origin main" in WORKFLOW
    assert "~/.cache/conflict-early-warning" in WORKFLOW
    assert "|| true" not in WORKFLOW
    assert "|| echo" not in WORKFLOW
