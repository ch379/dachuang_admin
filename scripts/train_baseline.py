from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from ml.baselines import evaluate_naive_baselines, train_hist_gradient_baseline


def main() -> None:
    settings = get_settings()
    artifact_dir = settings.artifact_dir_path
    artifact_dir.mkdir(parents=True, exist_ok=True)

    naive_metrics = evaluate_naive_baselines()
    hgb_result = train_hist_gradient_baseline(artifact_dir)

    candidates = [
        {
            "name": "naive",
            "model_type": "naive",
            "valid_mae": naive_metrics["naive"]["valid"]["mae"],
            "metrics": naive_metrics["naive"],
            "model_path": None,
        },
        {
            "name": "seasonal_naive",
            "model_type": "seasonal_naive",
            "valid_mae": naive_metrics["seasonal_naive"]["valid"]["mae"],
            "metrics": naive_metrics["seasonal_naive"],
            "model_path": None,
        },
        {
            "name": "hist_gradient_boosting",
            "model_type": "hist_gradient_boosting",
            "valid_mae": hgb_result["metrics"]["valid"]["mae"],
            "metrics": {
                "valid": hgb_result["metrics"]["valid"],
                "test": hgb_result["metrics"]["test"],
            },
            "model_path": hgb_result["model_path"],
            "metadata_path": hgb_result["metadata_path"],
        },
    ]
    best = min(candidates, key=lambda item: item["valid_mae"])
    best_metadata = {
        "name": best["name"],
        "model_type": best["model_type"],
        "model_path": best.get("model_path"),
        "metrics": best["metrics"],
    }
    if best.get("metadata_path"):
        payload = json.loads(Path(best["metadata_path"]).read_text(encoding="utf-8"))
        best_metadata.update(payload)
        best_metadata["model_path"] = best["model_path"]

    summary = {
        "best_model": best_metadata,
        "all_metrics": {
            "naive": naive_metrics["naive"],
            "seasonal_naive": naive_metrics["seasonal_naive"],
            "hist_gradient_boosting": {
                "valid": hgb_result["metrics"]["valid"],
                "test": hgb_result["metrics"]["test"],
            },
        },
    }
    settings.best_model_metadata_path.write_text(json.dumps(best_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (artifact_dir / "baseline_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
