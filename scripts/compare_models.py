from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_candidate(payload: dict | None, source: str) -> dict | None:
    if not payload:
        return None
    if source == "baseline_summary":
        best = payload.get("best_model")
        if not best:
            return None
        return {
            "name": best.get("name", "baseline"),
            "model_type": best.get("model_type", "baseline"),
            "model_path": best.get("model_path"),
            "metrics": best.get("metrics", {}),
            "lookback_steps": best.get("lookback_steps"),
            "forecast_steps": best.get("forecast_steps"),
            "numeric_features": best.get("numeric_features"),
            "categorical_features": best.get("categorical_features"),
        }
    return payload


def main() -> None:
    settings = get_settings()
    artifact_dir = settings.artifact_dir_path
    baseline = normalize_candidate(load_json(artifact_dir / "baseline_summary.json"), "baseline_summary")
    lstm = normalize_candidate(load_json(artifact_dir / "lstm_summary.json"), "lstm_summary")

    candidates = [item for item in [baseline, lstm] if item]
    if not candidates:
        raise SystemExit("No model summaries found in artifacts directory.")

    best = min(
        candidates,
        key=lambda item: item.get("metrics", {}).get("valid", {}).get("mae", float("inf")),
    )
    settings.best_model_metadata_path.write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "selected": best.get("name"),
        "selected_model_type": best.get("model_type"),
        "valid_mae": best.get("metrics", {}).get("valid", {}).get("mae"),
        "test_mae": best.get("metrics", {}).get("test", {}).get("mae"),
        "candidates": [
            {
                "name": item.get("name"),
                "model_type": item.get("model_type"),
                "valid_mae": item.get("metrics", {}).get("valid", {}).get("mae"),
                "test_mae": item.get("metrics", {}).get("test", {}).get("mae"),
            }
            for item in candidates
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
