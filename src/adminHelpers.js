export const PIE_COLORS = ["#72e7ff", "#3fa9ff", "#77f2c4", "#ffb454", "#f97373"];
export const RISK_COLORS = {
  low: "#58d68d",
  medium: "#f8c14a",
  high: "#ff8a5b",
  critical: "#ff5f7a",
};

export const INTERVAL_OPTIONS = [
  { value: "auto", label: "自动" },
  { value: 10, label: "10 分钟" },
  { value: 20, label: "20 分钟" },
  { value: 30, label: "30 分钟" },
  { value: 60, label: "60 分钟" },
  { value: 120, label: "120 分钟" },
  { value: 180, label: "180 分钟" },
  { value: 240, label: "240 分钟" },
  { value: 360, label: "360 分钟" },
  { value: 720, label: "720 分钟" },
  { value: 1440, label: "1 天" },
];

export function toInputTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => `${part}`.padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

export function toApiTime(value) {
  return value ? value.replace("T", " ") : undefined;
}

export function addMinutes(value, minutes) {
  const date = new Date(value);
  return toInputTime(new Date(date.getTime() + minutes * 60000));
}

export function formatShortTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  return `${`${date.getHours()}`.padStart(2, "0")}:${`${date.getMinutes()}`.padStart(2, "0")}`;
}

export function formatRange(start, end) {
  if (!start || !end) return "请选择时间段";
  return `${start.replace("T", " ")} ~ ${end.replace("T", " ")}`;
}

export function average(values) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function safeDirection(value) {
  return value || "未指定";
}

export function riskLabel(level) {
  return { low: "低风险", medium: "中风险", high: "高风险", critical: "严重" }[level];
}

export function inferRisk(vehicleCount, avgSpeed) {
  if (vehicleCount >= 70 || avgSpeed <= 15) return "critical";
  if (vehicleCount >= 50 || avgSpeed <= 22) return "high";
  if (vehicleCount >= 28 || avgSpeed <= 30) return "medium";
  return "low";
}

export function chooseAutoInterval(startInput, endInput) {
  const totalMinutes = Math.max(10, Math.ceil((new Date(endInput) - new Date(startInput)) / 60000));
  const values = INTERVAL_OPTIONS.filter((item) => item.value !== "auto").map((item) => Number(item.value));
  return values.find((interval) => Math.ceil(totalMinutes / interval) <= 10) || 1440;
}

export function resolveInterval(intervalMode, start, end) {
  return intervalMode === "auto" ? chooseAutoInterval(start, end) : Number(intervalMode);
}

export function buildSiteStats(rows, options, directionMap) {
  return options.map((item) => {
    const direction = directionMap[item.site_name] || item.directions?.[0] || "";
    const exact = rows.filter((row) => row.site_name === item.site_name && safeDirection(row.approach) === direction);
    const source = exact.length ? exact : rows.filter((row) => row.site_name === item.site_name);
    const avgVehicle = Math.round(average(source.map((row) => Number(row.vehicle_count) || 0)));
    const avgSpeed = Number(average(source.map((row) => Number(row.avg_speed) || 0)).toFixed(1));
    return { siteName: item.site_name, direction, directions: item.directions || [], avgVehicle, avgSpeed, risk: inferRisk(avgVehicle, avgSpeed || 40) };
  });
}

export function buildMonitorRows(rows) {
  const grouped = new Map();
  rows.forEach((row) => {
    const key = `${row.site_name}|${safeDirection(row.approach)}`;
    const current = grouped.get(key) || { siteName: row.site_name, direction: safeDirection(row.approach), vehicles: [], speeds: [] };
    current.vehicles.push(Number(row.vehicle_count) || 0);
    current.speeds.push(Number(row.avg_speed) || 0);
    grouped.set(key, current);
  });
  return Array.from(grouped.values()).map((item) => {
    const avgVehicle = Math.round(average(item.vehicles));
    const avgSpeed = Number(average(item.speeds).toFixed(1));
    return { ...item, avgVehicle, avgSpeed, risk: inferRisk(avgVehicle, avgSpeed) };
  }).sort((a, b) => b.avgVehicle - a.avgVehicle);
}

export function buildPredictionStats(rows) {
  if (!rows.length) return { peak: 0, low: 0, avg: 0, peakTime: "--:--" };
  const values = rows.map((item) => Number(item.predicted_vehicle_count) || 0);
  const peak = Math.max(...values);
  return { peak, low: Math.min(...values), avg: Math.round(average(values)), peakTime: formatShortTime(rows.find((item) => (Number(item.predicted_vehicle_count) || 0) === peak)?.timestamp) };
}

export function buildHistorySeries(rows, siteName, direction, endTime) {
  const end = new Date(endTime);
  const start = new Date(end.getTime() - 3600000);
  const grouped = new Map();
  rows.filter((row) => row.site_name === siteName && safeDirection(row.approach) === direction).forEach((row) => {
    const ts = new Date(row.timestamp);
    if (ts < start || ts > end) return;
    ts.setMinutes(Math.floor(ts.getMinutes() / 10) * 10, 0, 0);
    const key = ts.toISOString();
    const current = grouped.get(key) || { timestamp: key, label: formatShortTime(ts), sum: 0, n: 0 };
    current.sum += Number(row.vehicle_count) || 0;
    current.n += 1;
    grouped.set(key, current);
  });
  return Array.from(grouped.values()).sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp)).map((item) => ({ timestamp: item.timestamp, label: item.label, history: Math.round(item.sum / item.n) }));
}

export function mergePredictionSeries(historyRows, predictionRows) {
  const merged = new Map();
  historyRows.forEach((item) => merged.set(item.timestamp, { ...item }));
  predictionRows.forEach((item) => {
    const timestamp = new Date(item.timestamp).toISOString();
    const current = merged.get(timestamp) || { timestamp, label: formatShortTime(item.timestamp) };
    current.predicted = Number(item.predicted_vehicle_count) || 0;
    merged.set(timestamp, current);
  });
  return Array.from(merged.values()).sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
}

export function buildDirectionPie(rows, siteName) {
  const grouped = new Map();
  rows.filter((row) => row.site_name === siteName).forEach((row) => {
    const direction = safeDirection(row.approach);
    grouped.set(direction, (grouped.get(direction) || 0) + (Number(row.vehicle_count) || 0));
  });
  return Array.from(grouped.entries()).map(([name, value]) => ({ name, value }));
}

export function axisDomain(values) {
  if (!values.length) return [0, 10];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [Math.max(0, min - 5), max + 5];
  const padding = Math.max(1, Math.round((max - min) * 0.18));
  return [Math.max(0, min - padding), max + padding];
}
