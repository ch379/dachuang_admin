import axios from "axios";

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || "http://127.0.0.1:8000";

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
});

const memoryCache = new Map();

function buildCacheKey(path, params) {
  return `${path}:${JSON.stringify(params || {})}`;
}

async function getCached(path, params, ttlMs = 5 * 60 * 1000) {
  const key = buildCacheKey(path, params);
  const now = Date.now();
  const cached = memoryCache.get(key);
  if (cached && now - cached.ts <= ttlMs) {
    return cached.value;
  }
  const { data } = await api.get(path, { params });
  memoryCache.set(key, { ts: now, value: data });
  return data;
}

export async function fetchIntersections() {
  const { data } = await api.get("/api/intersections");
  return data;
}

export async function fetchPublicConfig() {
  return getCached("/api/config/public", undefined, 30 * 60 * 1000);
}

export async function fetchTraffic(params) {
  const { data } = await api.get("/api/traffic", { params: { ...params, dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" } });
  return data;
}

export async function fetchAnalysis(params) {
  const { data } = await api.get("/api/analysis", { params: { ...params, dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" } });
  return data;
}

export async function fetchDataRange() {
  return getCached("/api/data-range", { dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" }, 30 * 60 * 1000);
}

export async function fetchDatasetVersions() {
  return getCached("/api/dataset-versions", undefined, 30 * 60 * 1000);
}

export async function fetchDirectionRoutes() {
  return getCached("/api/direction-routes", undefined, 30 * 60 * 1000);
}

export async function fetchDirectionFlows(params) {
  return getCached("/api/direction-flows", { ...params, dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" });
}

export async function fetchWeather(params) {
  const { data } = await api.get("/api/weather", { params });
  return data;
}

export async function fetchPrediction(params) {
  return getCached("/api/prediction", { ...params, dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" }, 5 * 60 * 1000);
}

export async function fetchPredictionMeta() {
  return getCached("/api/prediction/meta", { dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" }, 30 * 60 * 1000);
}

export async function fetchPredictionOptions() {
  return getCached("/api/prediction/options", { dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" }, 30 * 60 * 1000);
}

export async function fetchPredictionCompare(params) {
  return getCached("/api/prediction/compare", { ...params, dataset_version: localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20" }, 5 * 60 * 1000);
}

export async function postUserAlert(payload) {
  const { data } = await api.post("/api/user-alerts", payload);
  return data;
}
