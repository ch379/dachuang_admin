from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import delete

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal, init_db
from app.models import TrafficData


def _clean_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value


def _to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append(
            {
                "timestamp": pd.to_datetime(row["timestamp"]).to_pydatetime(),
                "site_name": str(row["site_name"]).strip(),
                "approach": str(row["approach"]).strip(),
                "movement": str(row["movement"]).strip(),
                "lane_type": _clean_value(row.get("lane_type")),
                "vehicle_count": int(row["vehicle_count"]),
                "avg_speed": float(row["avg_speed"]),
                "weather": str(row["weather"]).strip(),
                "tmax": int(row["tmax"]),
                "tmin": int(row["tmin"]),
                "source_file": _clean_value(row.get("source_file")),
                "source_sheet": _clean_value(row.get("source_sheet")),
            }
        )
    return records


def import_excel(excel_path: Path, chunk_size: int, truncate: bool) -> None:
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    init_db()
    session = SessionLocal()
    try:
        if truncate:
            session.execute(delete(TrafficData))
            session.commit()

        df = pd.read_excel(excel_path)
        total = len(df)
        imported = 0
        for start in range(0, total, chunk_size):
            chunk = df.iloc[start : start + chunk_size]
            records = _to_records(chunk)
            session.bulk_insert_mappings(TrafficData, records)
            session.commit()
            imported += len(records)
            print(f"Imported {imported}/{total} rows...")

        print(f"Done. Total imported rows: {imported}")
    finally:
        session.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import traffic Excel data into database.")
    parser.add_argument(
        "--excel",
        default="traffic_turning_5min.xlsx",
        help="Path to source Excel file.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=20000,
        help="Chunk size for batch insert.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete existing data before importing.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    import_excel(excel_path=Path(args.excel), chunk_size=args.chunk_size, truncate=args.truncate)
