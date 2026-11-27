from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "data2.0" / "data_delivery_20260811" / "processed"
OUTPUT = ROOT / "data2.0" / "核验后版本20260811"
EXCLUDED_STREAM = "S20"


def main() -> None:
    stream_path = DELIVERY / "05_traffic_clean_stream.csv"
    aggregate_path = DELIVERY / "06_dataset_13approach.csv"
    streams = pd.read_csv(stream_path)
    aggregate = pd.read_csv(aggregate_path)

    excluded = streams.loc[streams["stream_id"].eq(EXCLUDED_STREAM)].copy()
    if excluded.empty:
        raise SystemExit(f"Expected excluded stream {EXCLUDED_STREAM} was not found")
    if excluded["approach_id"].nunique() != 1 or excluded["approach_id"].iloc[0] != "KT_N2S":
        raise SystemExit("The exclusion scope changed; review before preparing a new dataset")

    filtered = streams.loc[~streams["stream_id"].eq(EXCLUDED_STREAM)].copy()
    target = aggregate.loc[aggregate["approach_id"].eq("KT_N2S")].copy()
    if len(target) != 14688:
        raise SystemExit("Unexpected KT_N2S aggregate row count")

    # Rebuild KT_N2S from the remaining confirmed stream S19. The delivered
    # direction aggregate includes S20, so it cannot be reused unchanged.
    confirmed = filtered.loc[filtered["approach_id"].eq("KT_N2S")].copy()
    if confirmed["stream_id"].nunique() != 1 or confirmed["stream_id"].iloc[0] != "S19":
        raise SystemExit("KT_N2S should contain only confirmed stream S19 after exclusion")
    confirmed = confirmed.set_index("timestamp").reindex(target["timestamp"])
    if confirmed["vehicle_count"].isna().any():
        raise SystemExit("S19 has missing timestamps; review before rebuilding KT_N2S")

    kt_mask = aggregate["approach_id"].eq("KT_N2S")
    aggregate.loc[kt_mask, "flow_partial"] = confirmed["vehicle_count"].to_numpy()
    aggregate.loc[kt_mask, "flow_5min"] = confirmed["vehicle_count"].to_numpy()
    aggregate.loc[kt_mask, "avg_speed_site_kmh"] = confirmed["avg_speed_kmh"].to_numpy()
    aggregate.loc[kt_mask, "speed_nunique"] = 1
    aggregate.loc[kt_mask, "expected_streams"] = 1
    aggregate.loc[kt_mask, "observed_streams"] = 1
    aggregate.loc[kt_mask, "coverage_ratio"] = 1.0
    aggregate.loc[kt_mask, "observation_complete"] = 1
    aggregate.loc[kt_mask, "mapping_confirmed"] = 1
    aggregate.loc[kt_mask, "flow_outlier_flag"] = confirmed["flow_outlier_flag"].to_numpy()
    aggregate.loc[kt_mask, "speed_outlier_flag"] = confirmed["speed_outlier_flag"].to_numpy()
    aggregate.loc[kt_mask, "data_valid_flag"] = confirmed["data_valid_flag"].to_numpy()

    OUTPUT.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(OUTPUT / "原始检测流_排除S20.csv", index=False, encoding="utf-8-sig")
    aggregate.to_csv(OUTPUT / "路口方向汇总_排除S20.csv", index=False, encoding="utf-8-sig")

    print(f"Source stream rows: {len(streams):,}")
    print(f"Excluded S20 rows (preserved in source): {len(excluded):,}")
    print(f"Filtered stream rows: {len(filtered):,}")
    print(f"Direction rows: {len(aggregate):,}; approaches: {aggregate['approach_id'].nunique()}")
    print(f"KT_N2S rebuilt from S19 rows: {len(confirmed):,}/{len(target):,}")
    print(f"Incomplete direction rows retained: {int((aggregate['observation_complete'] != 1).sum()):,}")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
