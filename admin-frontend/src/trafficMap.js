export const FLOW_COLORS = { low: "#28bd7b", medium: "#f5c249", high: "#ef6255", unknown: "#8191a2" };

export function flowDescription(flow) {
  if (!flow) return "所选时段暂无该方向数据";
  const labels = { low: "较低", medium: "中等", high: "较高" };
  return `时段平均 ${flow.average_count.toFixed(1)} 辆/5分钟 · 车流${labels[flow.level]}\n历史分级界限 ${flow.medium_threshold.toFixed(0)} / ${flow.high_threshold.toFixed(0)} 辆/5分钟`;
}
