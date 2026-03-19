import { average } from "./adminHelpers";

export function weatherSummary(rows, siteName, direction) {
  const grouped = new Map();
  rows
    .filter((row) => row.site_name === siteName && (!direction || row.approach === direction))
    .forEach((row) => {
      const key = row.weather || "未知";
      grouped.set(key, (grouped.get(key) || 0) + 1);
    });
  if (!grouped.size) return "天气待同步";
  return Array.from(grouped.entries()).sort((a, b) => b[1] - a[1])[0][0];
}

export function buildInterventions(summary, predictionStats) {
  if (!summary) return [];
  const items = [];
  if (summary.risk === "critical" || summary.risk === "high") {
    items.push({
      id: `${summary.siteName}-signal`,
      title: "延长目标方向绿灯时长",
      priority: summary.risk === "critical" ? "高" : "中",
      target: `${summary.siteName} · ${summary.direction}`,
      reason: "当前流量较高且车速下降，建议优先释放主方向通行压力。",
      effectFactor: summary.risk === "critical" ? 0.12 : 0.08,
      effectText: `预计峰值下降 ${summary.risk === "critical" ? 12 : 8}%`,
    });
  }
  if (predictionStats.peak >= 45) {
    items.push({
      id: `${summary.siteName}-split`,
      title: "提前分流相邻路口车流",
      priority: "高",
      target: `${summary.siteName} · ${summary.direction}`,
      reason: `未来一小时峰值预计出现在 ${predictionStats.peakTime}，建议提前进行分流。`,
      effectFactor: 0.06,
      effectText: "预计平均车速提升 6%",
    });
  }
  items.push({
    id: `${summary.siteName}-notify`,
    title: "向用户端推送避堵提醒",
    priority: "中",
    target: `${summary.siteName} · ${summary.direction}`,
    reason: "提前引导车辆错峰或绕行，降低瞬时汇入压力。",
    effectFactor: 0.05,
    effectText: "预计拥堵指数下降 5%",
  });
  return items.slice(0, 3);
}

export function buildEffectSeries(predictionRows, interventions) {
  const factor = interventions.reduce((sum, item) => sum + (item.effectFactor || 0), 0);
  return predictionRows.map((item, index) => {
    const before = Number(item.predicted_vehicle_count) || 0;
    const scale = Math.max(0.75, 1 - factor - index * 0.01);
    return { label: item.timestamp, before, after: Math.round(before * scale) };
  });
}

export function buildRiskReasons(summary, predictionStats) {
  if (!summary) return [];
  const reasons = [];
  if (summary.avgVehicle >= 60) reasons.push("车流量持续处于高位");
  if (summary.avgSpeed <= 20) reasons.push("平均车速明显下降");
  if (predictionStats.peak >= 48) reasons.push(`预计 ${predictionStats.peakTime} 出现峰值压力`);
  if (!reasons.length) reasons.push("当前运行状态基本平稳");
  return reasons.slice(0, 3);
}

export function congestionIndex(avgVehicle, avgSpeed) {
  return Math.max(1, Math.round((avgVehicle / Math.max(10, avgSpeed || 10)) * 12));
}

export function congestionAfter(effectSeries, fallback) {
  if (!effectSeries.length) return fallback;
  const before = average(effectSeries.map((item) => item.before));
  const after = average(effectSeries.map((item) => item.after));
  const ratio = before > 0 ? after / before : 1;
  return Math.max(1, Math.round(fallback * ratio));
}
