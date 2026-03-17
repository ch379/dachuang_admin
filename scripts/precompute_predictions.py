from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import PredictionResult
from app.service import _normalize_road_name, get_prediction_meta
from ml.dataset import load_prediction_frame
from ml.inference import predict_next_hour


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precompute and store traffic predictions in prediction_result.")
    parser.add_argument("--start", help="Start timestamp, format YYYY-MM-DD HH:MM[:SS]")
    parser.add_argument("--end", help="End timestamp, format YYYY-MM-DD HH:MM[:SS]")
    parser.add_argument("--road-name", help="Optional road name filter")
    parser.add_argument("--direction", help="Optional approach filter")
    parser.add_argument("--batch-size", type=int, default=3000, help="Flush batch size for inserts")
    parser.add_argument("--max-anchors", type=int, default=0, help="Optional max anchor timestamps per series, 0 means all")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()

    meta = get_prediction_meta()
    model_type = meta.get("model_type")
    if not model_type or model_type == "unavailable":
        raise SystemExit("Current prediction model is unavailable. Train/select a model first.")

    road_name = _normalize_road_name(args.road_name)
    frame = load_prediction_frame(site_name=road_name, approach=args.direction)
    if frame.empty:
        raise SystemExit("No source traffic data found for precomputation.")

    if args.start:
        frame = frame[frame["timestamp"] >= pd.Timestamp(args.start)]
    if args.end:
        frame = frame[frame["timestamp"] <= pd.Timestamp(args.end)]
    if frame.empty:
        raise SystemExit("No timestamps left after applying the selected range.")

    db = SessionLocal()
    batch: list[PredictionResult] = []
    stored_anchors = 0
    generated_rows = 0

    try:
        grouped = frame.groupby(["site_name", "approach"], sort=True)
        for (site_name, approach), group in grouped:
            timestamps = group["timestamp"].drop_duplicates().sort_values().tolist()
            if args.max_anchors and args.max_anchors > 0:
                timestamps = timestamps[-args.max_anchors :]

            existing_as_of = set(
                db.execute(
                    select(PredictionResult.as_of_time).where(
                        PredictionResult.site_name == site_name,
                        PredictionResult.approach == approach,
                        PredictionResult.model_type == model_type,
                    )
                ).scalars().all()
            )

            print(f"series={site_name}/{approach}, anchors={len(timestamps)}, existing={len(existing_as_of)}")

            for as_of in timestamps:
                anchor_time = as_of.to_pydatetime()
                if anchor_time in existing_as_of:
                    continue

                predicted = predict_next_hour(site_name=site_name, approach=approach, as_of=as_of.to_pydatetime())
                if not predicted:
                    continue

                db.execute(
                    delete(PredictionResult).where(
                        PredictionResult.site_name == site_name,
                        PredictionResult.approach == approach,
                        PredictionResult.model_type == model_type,
                        PredictionResult.as_of_time == anchor_time,
                    )
                )

                for target_time, count in predicted:
                    batch.append(
                        PredictionResult(
                            as_of_time=anchor_time,
                            target_time=target_time,
                            site_name=site_name,
                            approach=approach,
                            predicted_vehicle_count=int(count),
                            model_type=model_type,
                        )
                    )

                stored_anchors += 1
                generated_rows += len(predicted)
                existing_as_of.add(anchor_time)

                if len(batch) >= args.batch_size:
                    db.add_all(batch)
                    db.commit()
                    batch.clear()
                    print(f"stored anchors={stored_anchors}, rows={generated_rows}")

        if batch:
            db.add_all(batch)
            db.commit()

        print(f"done: model={model_type}, anchors={stored_anchors}, rows={generated_rows}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
