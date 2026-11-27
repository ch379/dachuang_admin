import { useEffect, useState } from "react";
import { fetchMergeScenario } from "./api/client";

const SOURCE_SITE = "恒福路交叉口";
const DEFAULT_RECEIVER = "星峰路—康田路交叉口|东向西";
const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

export default function MergeScenario({ options, timeLimit }) {
  const [start, setStart] = useState("2025-10-20T08:00");
  const [receiver, setReceiver] = useState(DEFAULT_RECEIVER);
  const [diversion, setDiversion] = useState("10");
  const [share, setShare] = useState("50");
  const [lag, setLag] = useState("5");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!options.length || !start || diversion === "" || share === "") return undefined;
    const controller = new AbortController();
    const [receiverSite, receiverDirection] = receiver.split("|");
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError("");
      setResult(null);
      try {
        const data = await fetchMergeScenario({
          start,
          receiver_site: receiverSite,
          receiver_direction: receiverDirection,
          diversion_percent: Number(diversion),
          ramp_share_percent: Number(share),
          lag_minutes: Number(lag),
        }, controller.signal);
        if (!controller.signal.aborted) setResult(data);
      } catch (requestError) {
        if (!controller.signal.aborted) {
          const detail = requestError.response?.data?.detail;
          setError(typeof detail === "string" ? detail : "请检查时段和参数后重试。");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [options.length, start, receiver, diversion, share, lag]);

  function change(setter) {
    return (event) => {
      setResult(null);
      setError("");
      setLoading(false);
      setter(event.target.value);
    };
  }

  return (
    <section className="merge-scenario" aria-labelledby="merge-scenario-title">
      <div className="panel-head">
        <h2 id="merge-scenario-title">合流区分流情景模拟</h2>
      </div>
      <p>以恒福路交叉口北向南的一小时实测车流为起点。接收方向和匝道使用按设定时延后移的实测时段。绕行方向、比例和时延均为情景假设。</p>
      <div className="merge-scenario__fields">
        <label>起始时间
          <input type="datetime-local" min={timeLimit.min} max={timeLimit.max} step="300" value={start} onChange={change(setStart)} />
        </label>
        <label>接收方向
          <select value={receiver} onChange={change(setReceiver)}>
            {options.filter((item) => item.site_name !== SOURCE_SITE).map((item) => (
              <optgroup key={item.site_name} label={item.site_name}>
                {item.directions.map((direction) => (
                  <option key={direction} value={`${item.site_name}|${direction}`}>{direction}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label>分流比例（%）
          <input type="number" min="0" max="100" value={diversion} onChange={change(setDiversion)} />
        </label>
        <label>分流车中原经匝道的比例（%）
          <input type="number" min="0" max="100" value={share} onChange={change(setShare)} />
        </label>
        <label>到达时延（分钟）
          <select value={lag} onChange={change(setLag)}>
            {[0, 5, 10, 15, 20, 25, 30].map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
      </div>
      {loading ? <p role="status">正在计算情景…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {result ? (
        <>
          <table>
            <caption>一小时车流量（辆）</caption>
            <thead><tr><th scope="col">观测方向</th><th scope="col">实测基准</th><th scope="col">模拟</th><th scope="col">变化</th></tr></thead>
            <tbody>
              {[
                ["恒福路北向南", result.source],
                ["接收方向", result.receiver],
                ["合流区北向南匝道", result.ramp],
              ].map(([name, counts]) => {
                const delta = counts.after - counts.before;
                return <tr key={name}><th scope="row">{name}</th><td>{number.format(counts.before)}</td><td>{number.format(counts.after)}</td><td>{delta > 0 ? "+" : ""}{number.format(delta)}</td></tr>;
              })}
            </tbody>
          </table>
          <p>接收方向峰值 {number.format(result.receiver_peak.before)} → {number.format(result.receiver_peak.after)} 辆／5 分钟。达到历史第 80 百分位（{number.format(result.receiver_threshold)} 辆／5 分钟）的区间数 {result.receiver_high.before} → {result.receiver_high.after}（共 12 个）。</p>
        </>
      ) : null}
    </section>
  );
}
