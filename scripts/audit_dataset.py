from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NEW_DATA = ROOT / "data2.0/data_delivery_20260811/processed/06_dataset_13approach.csv"
DATABASE = ROOT / "traffic.db"
SITE_IDS = {
    "\u6052\u798f\u8def\u4ea4\u53c9\u53e3": "HF",
    "\u661f\u5cf0\u8def\u4ea4\u53c9\u53e3": "XF",
    "\u661f\u5cf0\u8def\u5eb7\u7530\u8def\u4ea4\u53c9\u53e3": "KT",
    "\u661f\u5149\u5927\u9053\u5317\u5ef6\u7533\u6bb5": "XG",
}
DIRECTIONS = {"E2W": "\u4e1c\u5411\u897f", "W2E": "\u897f\u5411\u4e1c", "N2S": "\u5317\u5411\u5357", "S2N": "\u5357\u5411\u5317"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit delivered traffic data against SQLite without writing")
    parser.add_argument("--new-data", type=Path, default=NEW_DATA)
    parser.add_argument("--database", type=Path, default=DATABASE)
    args = parser.parse_args()

    new = pd.read_csv(args.new_data, parse_dates=["timestamp"])
    old = pd.read_sql_query(
        "SELECT timestamp, site_name, approach, vehicle_count, avg_speed, weather, tmax, tmin FROM traffic_data",
        sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True),
        parse_dates=["timestamp"],
    )
    new["site"] = new["site_id"]
    new["direction_name"] = new["direction"].map(DIRECTIONS)
    old["site"] = old["site_name"].map({
        "\u6052\u798f\u8def": "HF", "\u661f\u5cf0\u8def\u4ea4\u53c9\u53e3": "XF",
        "\u661f\u5cf0\u8def\u5eb7\u7530\u8def\u4ea4\u53c9\u53e3": "KT", "\u661f\u5149\u5927\u9053\u5317\u5ef6\u7533\u6bb5": "XG",
    })
    old["direction_name"] = old["approach"]

    # The current database contains movement-level records. Aggregate them to
    # the same site/direction/five-minute grain as the delivered dataset.
    old_grouped = old.groupby(["timestamp", "site", "direction_name"], as_index=False).agg(
        old_flow=("vehicle_count", "sum"),
        old_speed=("avg_speed", "mean"),
        old_weather=("weather", "first"), old_tmax=("tmax", "first"), old_tmin=("tmin", "first"),
        source_rows=("vehicle_count", "size"),
    )
    merged = new.merge(old_grouped, on=["timestamp", "site", "direction_name"], how="outer", indicator=True)
    matched = merged[merged["_merge"] == "both"].copy()
    matched["flow_delta"] = matched["flow_5min"] - matched["old_flow"]
    matched["speed_delta"] = matched["avg_speed_site_kmh"] - matched["old_speed"]
    unmatched_new = merged[merged["_merge"] == "left_only"]
    unmatched_old = merged[merged["_merge"] == "right_only"]

    print("=== ??????????????????===")
    print(f"??? {len(new):,}??{new.site.nunique()}????{new.approach_id.nunique()}????{new.timestamp.min()} ? {new.timestamp.max()}")
    print(f"??????? {(new.observation_complete == 1).sum():,}/{len(new):,}???? {(new.observation_complete != 1).sum():,}????? {(new.data_valid_flag != 1).sum():,}???? {(new.flow_5min == 0).sum():,}")
    print(f"???? {len(old):,}???movement?????? {len(old_grouped):,}?????")
    print(f"??? {len(matched):,}????? {len(unmatched_new):,}?????? {len(unmatched_old):,}")
    if len(matched):
        print(f"??????? {int((matched.flow_delta == 0).sum()):,}/{len(matched):,}??????? {matched.flow_delta.abs().sum():,.0f}?????? {matched.flow_delta.abs().max():,.0f}")
        print(f"??????>0.1 km/h {int((matched.speed_delta.abs() > .1).sum()):,}???/?????? {int(((matched.weather != matched.old_weather) | (matched.tmax_c != matched.old_tmax) | (matched.tmin_c != matched.old_tmin)).sum()):,}")
        print("?????????")
        summary = matched.groupby(["site", "direction_name"], dropna=False).agg(
            matched_points=("timestamp", "size"), differing_points=("flow_delta", lambda x: int((x != 0).sum())),
            max_abs_flow_delta=("flow_delta", lambda x: int(x.abs().max())),
        )
        print(summary.to_string())
        samples = matched.loc[matched.flow_delta != 0, ["timestamp", "site", "direction_name", "flow_5min", "old_flow", "flow_delta"]].head(8)
        if not samples.empty:
            print("??????")
            print(samples.to_string(index=False))
    print("??????")
    print(f"mapping??? {(new.mapping_confirmed != 1).sum():,}??????? {int(new.flow_outlier_flag.sum()):,}??????? {int(new.speed_outlier_flag.sum()):,}")
    print("??????13????????????movement?traffic_data????????????")


if __name__ == "__main__":
    main()
