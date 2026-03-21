import { average } from "./adminHelpers";

export function buildInterventionTargets(rows) {
  if (!rows?.length) return [];

  const riskScore = { critical: 4, high: 3, medium: 2, low: 1 };
  const sorted = rows
    .slice()
    .sort((a, b) => {
      const scoreDiff = (riskScore[b.risk] || 0) - (riskScore[a.risk] || 0);
      if (scoreDiff !== 0) return scoreDiff;
      return (b.avgVehicle || 0) - (a.avgVehicle || 0);
    });

  const preferred = sorted
    .filter((item) => (riskScore[item.risk] || 0) >= 2)
    .slice(0, 4);

  const source = preferred.length ? preferred : sorted.slice(0, 1);

  return source.map((item) => ({
    ...item,
    id: `${item.siteName}-${item.direction}`,
    target: `${item.siteName} ${item.direction}`,
    reason:
      item.risk === "critical"
        ? "拥堵压力较高，建议立即介入处理"
        : item.risk === "high"
          ? "交通压力持续上升，建议优先关注该方向"
          : "交通波动明显，建议纳入预防性干预范围",
  }));
}

function signalExtensionSeconds(target, index) {
  if (target.risk === "critical") return 18;
  if (target.risk === "high") return index === 0 ? 14 : 10;
  return 6;
}

function diversionRatio(target) {
  if (target.risk === "critical") return 20;
  if (target.risk === "high") return 14;
  return 8;
}

export function buildInterventions(targets) {
  if (!targets?.length) {
    return { agencyActions: [], userMessages: [] };
  }

  const agencyActions = targets.slice(0, 3).map((target, index) => {
    const extensionSeconds = signalExtensionSeconds(target, index);
    const diversion = diversionRatio(target);
    const vehicleBase = Math.max(1, target.avgVehicle || 0);
    const speedBase = Number(target.avgSpeed || 0).toFixed(1);

    return {
      id: `${target.id}-agency-${index}`,
      target: target.target,
      reason:
        index === 0
          ? `该方向平均车流约 ${vehicleBase} 辆，建议将 ${target.direction} 方向绿灯临时增加 ${extensionSeconds} 秒，并同步压缩次方向 ${Math.max(4, Math.round(extensionSeconds * 0.6))} 秒。`
          : index === 1
            ? `该方向平均车速约 ${speedBase} km/h，建议启动周边路口联动分流，预计提前引导约 ${diversion}% 车流绕行相邻通道。`
            : `建议安排 1 组交警在 ${target.direction} 进口道现场疏导，连续 ${15 + index * 5} 分钟优先保障直行通行。`,
      effectFactor: target.risk === "critical" ? 0.18 : target.risk === "high" ? 0.12 : 0.08,
      effectText:
        index === 0
          ? `预计该方向排队长度下降 ${target.risk === "critical" ? 15 : target.risk === "high" ? 10 : 6}%`
          : index === 1
            ? `预计进入拥堵路段车辆减少 ${diversion}%`
            : "预计现场通行恢复速度提升 10%",
      actionLabel: "执行",
    };
  });

  const userMessages = targets.slice(0, 3).map((target, index) => {
    const vehicleBase = Math.max(1, target.avgVehicle || 0);

    return {
      id: `${target.id}-user-${index}`,
      target: target.target,
      siteName: target.siteName,
      direction: target.direction,
      message:
        index === 0
          ? `${target.siteName}${target.direction}方向预计持续拥堵，当前预计车流约 ${vehicleBase} 辆，建议提前 10 至 15 分钟出发。`
          : index === 1
            ? `${target.siteName}${target.direction}方向通行压力较高，建议改走周边平行道路，避开当前路口。`
            : `${target.siteName}${target.direction}方向车速下降明显，如非必要请暂缓进入该路段并优先选择公共出行。`,
      effectFactor: index === 0 ? 0.08 : 0.1,
      effectText: index === 0 ? "预计削减瞬时汇入压力 8%" : "预计降低进入拥堵路段车辆 10%",
      actionLabel: "推送",
    };
  });

  return { agencyActions, userMessages };
}

export function buildEffectSeries(predictionRows, interventions) {
  const factor = interventions.reduce((sum, item) => sum + (item.effectFactor || 0), 0);
  return predictionRows.map((item, index) => {
    const before = Number(item.predicted_vehicle_count) || 0;
    const scale = Math.max(0.58, 1 - factor * 1.15 - index * 0.015);
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
