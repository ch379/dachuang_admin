from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "traffic.db"
DATA_DIR = ROOT / "data2.0" / "核验后版本20260811"
VERSION = "新版_20260811_排除S20"

STREAM_COLUMNS = [
    "timestamp", "site_id", "site_name", "approach_id", "direction", "stream_id",
    "lane_group", "movement_group", "vehicle_scope", "vehicle_count", "avg_speed_kmh",
    "weather", "tmax_c", "tmin_c", "source_worksheet", "source_folder", "source_file",
    "original_row", "include_in_model", "data_role", "mapping_confirmed", "mapping_note",
    "flow_outlier_flag", "speed_outlier_flag", "zero_flow_flag",
    "source_metadata_missing_flag", "data_valid_flag",
]
APPROACH_COLUMNS = [
    "timestamp", "site_id", "site_name", "approach_id", "direction", "expected_streams",
    "observed_streams", "coverage_ratio", "observation_complete", "flow_partial", "flow_5min",
    "avg_speed_site_kmh", "speed_nunique", "weather", "tmax_c", "tmin_c",
    "mapping_confirmed", "flow_outlier_flag", "speed_outlier_flag", "data_valid_flag",
]


def rows_from_csv(path: Path, columns: list[str]):
    frame = pd.read_csv(path, usecols=columns)
    frame = frame.astype(object).where(pd.notna(frame), None)
    return [tuple(row) for row in frame.itertuples(index=False, name=None)]


def main() -> None:
    stream_path = DATA_DIR / "原始检测流_排除S20.csv"
    approach_path = DATA_DIR / "路口方向汇总_排除S20.csv"
    backup_path = DATA_DIR / "traffic_db_import前备份.sqlite"

    stream_rows = rows_from_csv(stream_path, STREAM_COLUMNS)
    approach_rows = rows_from_csv(approach_path, APPROACH_COLUMNS)

    with sqlite3.connect(DATABASE) as db:
        legacy_count_before = db.execute("SELECT COUNT(*) FROM traffic_data").fetchone()[0]
        # Keep a restorable copy of the existing database before adding versioned tables.
        if not backup_path.exists():
            with sqlite3.connect(backup_path) as backup:
                db.backup(backup)

        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """CREATE TABLE IF NOT EXISTS traffic_dataset_version (
                    version_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    source_description TEXT NOT NULL,
                    raw_stream_rows INTEGER NOT NULL,
                    approach_rows INTEGER NOT NULL,
                    excluded_streams TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS traffic_stream_observation (
                    dataset_version TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    site_id TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    approach_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    lane_group TEXT,
                    movement_group TEXT,
                    vehicle_scope TEXT,
                    vehicle_count INTEGER,
                    avg_speed_kmh REAL,
                    weather TEXT,
                    tmax_c REAL,
                    tmin_c REAL,
                    source_worksheet TEXT,
                    source_folder TEXT,
                    source_file TEXT,
                    original_row INTEGER,
                    include_in_model INTEGER,
                    data_role TEXT,
                    mapping_confirmed INTEGER,
                    mapping_note TEXT,
                    flow_outlier_flag INTEGER,
                    speed_outlier_flag INTEGER,
                    zero_flow_flag INTEGER,
                    source_metadata_missing_flag INTEGER,
                    data_valid_flag INTEGER,
                    PRIMARY KEY (dataset_version, stream_id, timestamp)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS traffic_approach_observation (
                    dataset_version TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    site_id TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    approach_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    expected_streams INTEGER,
                    observed_streams INTEGER,
                    coverage_ratio REAL,
                    observation_complete INTEGER,
                    flow_partial REAL,
                    flow_5min REAL,
                    avg_speed_site_kmh REAL,
                    speed_nunique INTEGER,
                    weather TEXT,
                    tmax_c REAL,
                    tmin_c REAL,
                    mapping_confirmed INTEGER,
                    flow_outlier_flag INTEGER,
                    speed_outlier_flag INTEGER,
                    data_valid_flag INTEGER,
                    PRIMARY KEY (dataset_version, approach_id, timestamp)
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_stream_version_site_time "
                "ON traffic_stream_observation(dataset_version, site_id, approach_id, timestamp)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_approach_version_site_time "
                "ON traffic_approach_observation(dataset_version, site_id, approach_id, timestamp)"
            )
            db.execute(
                """INSERT OR IGNORE INTO traffic_dataset_version
                   (version_id, display_name, source_description, raw_stream_rows,
                    approach_rows, excluded_streams, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "旧版", "旧版交通数据", "既有 traffic_data 表；导入前原样保留",
                    legacy_count_before, 0, "", datetime.now().isoformat(timespec="seconds"),
                ),
            )
            existing = db.execute(
                "SELECT raw_stream_rows, approach_rows FROM traffic_dataset_version WHERE version_id = ?",
                (VERSION,),
            ).fetchone()
            if existing:
                counts = db.execute(
                    "SELECT (SELECT COUNT(*) FROM traffic_stream_observation WHERE dataset_version=?), "
                    "(SELECT COUNT(*) FROM traffic_approach_observation WHERE dataset_version=?)",
                    (VERSION, VERSION),
                ).fetchone()
                if counts != tuple(existing):
                    raise RuntimeError("A partial version import already exists; refusing to overwrite it")
                db.rollback()
                print(f"Version {VERSION} already imported; no changes made.")
                return

            now = datetime.now().isoformat(timespec="seconds")
            db.execute(
                "INSERT INTO traffic_dataset_version VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    VERSION, "新版交通数据（排除S20）",
                    "2026-08-11交付数据；排除康田路北向南S20疑似电动车检测流；保留逐流和方向汇总两种粒度",
                    len(stream_rows), len(approach_rows), "S20", now,
                ),
            )
            stream_marks = ",".join(["?"] * (len(STREAM_COLUMNS) + 1))
            stream_names = ",".join(["dataset_version"] + STREAM_COLUMNS)
            db.executemany(
                f"INSERT INTO traffic_stream_observation ({stream_names}) VALUES ({stream_marks})",
                [(VERSION, *row) for row in stream_rows],
            )
            approach_marks = ",".join(["?"] * (len(APPROACH_COLUMNS) + 1))
            approach_names = ",".join(["dataset_version"] + APPROACH_COLUMNS)
            db.executemany(
                f"INSERT INTO traffic_approach_observation ({approach_names}) VALUES ({approach_marks})",
                [(VERSION, *row) for row in approach_rows],
            )
            legacy_count_after = db.execute("SELECT COUNT(*) FROM traffic_data").fetchone()[0]
            if legacy_count_after != legacy_count_before:
                raise RuntimeError("Legacy traffic_data changed during version import")
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(f"Imported version: {VERSION}")
    print(f"Stream rows: {len(stream_rows):,}; approach rows: {len(approach_rows):,}")
    print(f"Legacy rows unchanged: {legacy_count_before:,}")
    print(f"Pre-import backup: {backup_path}")


if __name__ == "__main__":
    main()
