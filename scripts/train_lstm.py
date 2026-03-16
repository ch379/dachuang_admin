from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from ml.lstm_model import train_lstm_model


def main() -> None:
    settings = get_settings()
    artifact_dir = settings.artifact_dir_path
    artifact_dir.mkdir(parents=True, exist_ok=True)

    result = train_lstm_model(artifact_dir)
    summary = {
        "name": "lstm",
        "model_type": "lstm",
        "model_path": result["model_path"],
        "scaler_path": result["scaler_path"],
        "metrics": result["metrics"],
    }

    current_best = None
    if settings.best_model_metadata_path.exists():
        current_best = json.loads(settings.best_model_metadata_path.read_text(encoding="utf-8"))
    if current_best is None or result["metrics"]["valid"]["mae"] < current_best["metrics"]["valid"]["mae"]:
        settings.best_model_metadata_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    (artifact_dir / "lstm_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
