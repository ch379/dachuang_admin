"""Measure observed associations and simulate a hypothetical diversion."""

import argparse
from pathlib import Path

import pandas as pd

from app.merge_scenario import DEFAULT_RECEIVER, SOURCE, summarize_scenario
from ml.dataset import DEFAULT_SPLIT
from scripts.train_versioned_model import load_frame


DEFAULT_STREAMS = Path("data2.0/data_delivery_20260811/processed/04_traffic_raw_long.csv")
DOWNSTREAM = {
    "主线车道 2–3（S01）": ["S01"],
    "主线车道 1（S02）": ["S02"],
    "北向南匝道（S03）": ["S03"],
    "主线全车道（S01+S02）": ["S01", "S02"],
}


def load_downstream(path):
    streams = pd.read_csv(
        path, usecols=["timestamp", "stream_id", "data_role", "vehicle_count"],
        parse_dates=["timestamp"],
    )
    streams = streams[streams.data_role.eq("downstream_reference")]
    counts = streams.pivot(index="timestamp", columns="stream_id", values="vehicle_count")
    return pd.DataFrame({
        name: counts[ids].sum(axis=1, min_count=len(ids))
        for name, ids in DOWNSTREAM.items()
    }).sort_index()


def remove_time_profile(frame):
    profile_keys = [frame.index.dayofweek >= 5, frame.index.hour, frame.index.minute]
    train = frame.loc[:DEFAULT_SPLIT.train_end]
    baseline = train.groupby([key[:len(train)] for key in profile_keys]).median()
    baseline = baseline.reindex(pd.MultiIndex.from_arrays(profile_keys))
    return frame - baseline.set_axis(frame.index)


def print_associations(upstream, downstream):
    approaches = upstream.loc[:, upstream.columns.get_level_values("approach") == "北向南"]
    approaches.columns = approaches.columns.get_level_values("site_name")
    residuals = remove_time_profile(approaches.join(downstream, how="outer").sort_index())
    test = residuals.loc[DEFAULT_SPLIT.valid_end + pd.Timedelta(minutes=5):DEFAULT_SPLIT.test_end]
    estimates = []
    for site in approaches:
        for point in downstream:
            shifted = pd.DataFrame({
                lag: test[site].shift(lag // 5, freq="5min") for lag in (0, 5, 10, 15, 20, 25, 30)
            }).join(test[point], how="inner").dropna()
            correlations = shifted.drop(columns=point).corrwith(shifted[point])
            estimates.append({
                "upstream": site, "downstream": point, "n": len(shifted),
                **{f"lag_{lag}": corr for lag, corr in correlations.items()},
            })
    print("去除工作日/周末分时中位数后的 Pearson 相关（上游领先分钟数）")
    print(pd.DataFrame(estimates).round(3).to_string(index=False))


def print_scenario(upstream, downstream):
    # Hypothetical alternate route and ramp share. No trajectories or intervention logs are used.
    start = pd.Timestamp("2025-10-20 08:00")
    times = pd.date_range(start, periods=12, freq="5min")
    ramp = "北向南匝道（S03）"
    ramp_share = 0.5
    lag = pd.Timedelta(minutes=5)
    baseline = pd.DataFrame({
        "source": upstream[SOURCE].reindex(times).to_numpy(),
        "receiver": upstream[DEFAULT_RECEIVER].reindex(times + lag).to_numpy(),
        "ramp": downstream[ramp].reindex(times + lag).to_numpy(),
    })
    if baseline.isna().any().any():
        raise ValueError("Scenario window is missing baseline observations.")

    reference = upstream.loc[:DEFAULT_SPLIT.train_end, DEFAULT_RECEIVER].quantile(0.8)
    print(f"情景模拟：源 08:00–08:55，接收方向和匝道延迟 5 分钟。接收方向历史 P80={reference:.1f} 辆/5分钟。")
    estimates = []
    for ratio in (0, 0.05, 0.10, 0.15):
        result = summarize_scenario(baseline.source, baseline.receiver, baseline.ramp, ratio, ramp_share, reference)
        estimates.append({
            "diversion": ratio, "source_total": result["source"]["after"],
            "receiver_total": result["receiver"]["after"], "ramp_total": result["ramp"]["after"],
            "receiver_peak": result["receiver_peak"]["after"],
            "receiver_buckets_at_p80": result["receiver_high"]["after"],
        })
    print(pd.DataFrame(estimates).round(2).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams", type=Path, default=DEFAULT_STREAMS)
    args = parser.parse_args()
    upstream = load_frame().pivot(index="timestamp", columns=["site_name", "approach"], values="vehicle_count")
    downstream = load_downstream(args.streams)
    print(downstream.count().to_string())
    print_associations(upstream, downstream)
    print_scenario(upstream, downstream)


if __name__ == "__main__":
    main()
