import { useEffect, useMemo, useRef, useState } from "react";
import AMapLoader from "@amap/amap-jsapi-loader";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchAnalysis,
  fetchDataRange,
  fetchDatasetVersions,
  fetchDirectionRoutes,
  fetchDirectionFlows,
  fetchPrediction,
  fetchPredictionCompare,
  fetchPredictionMeta,
  fetchPredictionOptions,
  fetchPublicConfig,
  postUserAlert,
  fetchTraffic,
} from "./api/client";
import {
  INTERVAL_OPTIONS,
  PIE_COLORS,
  RISK_COLORS,
  addMinutes,
  axisDomain,
  buildDirectionPie,
  buildHistorySeries,
  buildMonitorRows,
  buildPredictionStats,
  buildSiteStats,
  formatRange,
  formatShortTime,
  mergePredictionSeries,
  resolveInterval,
  toApiTime,
  toInputTime,
} from "./adminHelpers";
import {
  buildEffectSeries,
  buildInterventionTargets,
  buildInterventions,
  buildRiskReasons,
  congestionAfter,
  congestionIndex,
} from "./adminModel";
import "./App.css";
import { FLOW_COLORS, flowDescription } from "./trafficMap";

const MAP_DEFAULT = { center: [106.506145, 29.639573], zoom: 16.7 };
const DEFAULT_START = "2025-09-02T00:00";
const DEFAULT_END = "2025-09-02T01:00";
const HOUR_OPTIONS = Array.from({ length: 24 }, (_, index) => `${index}`.padStart(2, "0"));
const MINUTE_OPTIONS = Array.from({ length: 12 }, (_, index) => `${index * 5}`.padStart(2, "0"));
const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function clampInitialRange(min, max) {
  const minDate = new Date(min);
  const maxDate = new Date(max);
  const preferredStart = new Date(DEFAULT_START);
  const preferredEnd = new Date(DEFAULT_END);

  if (
    !Number.isNaN(preferredStart.getTime()) &&
    !Number.isNaN(preferredEnd.getTime()) &&
    preferredStart >= minDate &&
    preferredEnd <= maxDate
  ) {
    return { start: DEFAULT_START, end: DEFAULT_END };
  }

  const fallbackStart = min || DEFAULT_START;
  return {
    start: fallbackStart,
    end: addMinutes(fallbackStart, 60),
  };
}

function parseInputParts(value) {
  if (!value || !value.includes("T")) {
    return { date: "", hour: "00", minute: "00" };
  }
  const [date, time] = value.split("T");
  const [hour = "00", minute = "00"] = time.split(":");
  return { date, hour, minute };
}

function toDisplayValue(value) {
  if (!value) return "--/--/-- --:--";
  const [date, time] = value.split("T");
  if (!date || !time) return value;
  return `${date.replaceAll("-", "/")} ${time}`;
}

function monthKey(value) {
  if (!value) return "";
  return value.slice(0, 7);
}

function shiftMonth(value, step) {
  const [year, month] = value.split("-").map(Number);
  const next = new Date(year, month - 1 + step, 1);
  return `${next.getFullYear()}-${`${next.getMonth() + 1}`.padStart(2, "0")}`;
}

function buildCalendar(monthValue) {
  const [year, month] = monthValue.split("-").map(Number);
  const firstDay = new Date(year, month - 1, 1);
  const startOffset = (firstDay.getDay() + 6) % 7;
  const firstCell = new Date(year, month - 1, 1 - startOffset);
  return Array.from({ length: 35 }, (_, index) => {
    const cell = new Date(firstCell);
    cell.setDate(firstCell.getDate() + index);
    const value = `${cell.getFullYear()}-${`${cell.getMonth() + 1}`.padStart(2, "0")}-${`${cell.getDate()}`.padStart(2, "0")}`;
    return {
      value,
      day: cell.getDate(),
      inMonth: cell.getMonth() === month - 1,
    };
  });
}

function withinLimits(value, min, max) {
  if (!value) return false;
  const current = new Date(value);
  if (Number.isNaN(current.getTime())) return false;
  if (min && current < new Date(min)) return false;
  if (max && current > new Date(max)) return false;
  return true;
}

function weatherIcon(weather) {
  const text = `${weather || ""}`;
  if (text.includes("雨")) return "🌧";
  if (text.includes("云") || text.includes("阴")) return "☁";
  if (text.includes("晴")) return "☀";
  if (text.includes("雾")) return "🌫";
  return "⛅";
}

function buildWeatherBadge(rows, siteName, direction) {
  const filtered = rows
    .filter((row) => row.site_name === siteName && (!direction || row.approach === direction))
    .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

  if (!filtered.length) {
    return { iconKey: "", headline: "天气待同步", breakdown: "暂无有效天气数据" };
  }

  const counts = new Map();
  filtered.forEach((row) => {
    const key = row.weather || "未知";
    counts.set(key, (counts.get(key) || 0) + 1);
  });

  const firstWeather = filtered[0].weather || "未知";
  const lastWeather = filtered[filtered.length - 1].weather || firstWeather;
  const sorted = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  const breakdown = sorted
    .slice(0, 3)
    .map(([name, count]) => `${name} ${Math.round((count / filtered.length) * 100)}%`)
    .join(" / ");

  return {
    iconKey: lastWeather,
    headline: firstWeather === lastWeather ? firstWeather : `${firstWeather}转${lastWeather}`,
    breakdown,
  };
}

function PredictionTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  const history = payload.find((item) => item.dataKey === "history" && item.value != null);
  const predicted = payload.find((item) => item.dataKey === "predicted" && item.value != null);

  if (!history && !predicted) return null;

  return (
    <div className="prediction-tooltip">
      <div className="prediction-tooltip__time">{label}</div>
      {predicted ? <div className="prediction-tooltip__row">预计：{predicted.value} 辆车</div> : null}
      {history ? <div className="prediction-tooltip__row">历史：{history.value} 辆车</div> : null}
    </div>
  );
}

function PopupChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  const point = payload.find((item) => item.value != null);
  if (!point) return null;

  const labelMap = {
    vehicle_count: "车流量",
    avg_speed: "车速",
  };
  const unitMap = {
    vehicle_count: "辆",
    avg_speed: "km/h",
  };

  return (
    <div className="prediction-tooltip">
      <div className="prediction-tooltip__time">{formatShortTime(label)}</div>
      <div className="prediction-tooltip__row">
        {labelMap[point.dataKey] || "数值"}：{point.value} {unitMap[point.dataKey] || ""}
      </div>
    </div>
  );
}

function EffectChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  const before = payload.find((item) => item.dataKey === "before" && item.value != null);
  const after = payload.find((item) => item.dataKey === "after" && item.value != null);
  if (!before && !after) return null;

  return (
    <div className="prediction-tooltip">
      <div className="prediction-tooltip__time">{label}</div>
      {before ? <div className="prediction-tooltip__row">干预前预测：{before.value} 辆车</div> : null}
      {after ? <div className="prediction-tooltip__row">干预后预测：{after.value} 辆车</div> : null}
    </div>
  );
}

function App() {
  const [amapKey, setAmapKey] = useState("");
  const [intersections, setIntersections] = useState([]);
  const [routes, setRoutes] = useState([]);
  const [directionFlows, setDirectionFlows] = useState([]);
  const [mapReady, setMapReady] = useState(false);
  const [predictionOptions, setPredictionOptions] = useState([]);
  const [predictionMeta, setPredictionMeta] = useState(null);
  const [timeLimit, setTimeLimit] = useState({ min: "", max: "" });
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [appliedRange, setAppliedRange] = useState({ start: "", end: "" });
  const [timeOpen, setTimeOpen] = useState(false);
  const [activeTimeField, setActiveTimeField] = useState("start");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerMonth, setPickerMonth] = useState("2025-09");
  const [siteDirections, setSiteDirections] = useState({});
  const [selectedSite, setSelectedSite] = useState("");
  const [datasetVersions, setDatasetVersions] = useState([]);
  const [datasetVersion, setDatasetVersion] = useState(localStorage.getItem("trafficDatasetVersion") || "新版_20260811_排除S20");
  const [trafficRows, setTrafficRows] = useState([]);
  const [predictionRows, setPredictionRows] = useState([]);
  const [predictionCompare, setPredictionCompare] = useState(null);
  const [popupOpen, setPopupOpen] = useState(false);
  const [popupIntervalMode, setPopupIntervalMode] = useState("auto");
  const [popupAnalysis, setPopupAnalysis] = useState([]);
  const [loading, setLoading] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [error, setError] = useState("");
  const [executedActions, setExecutedActions] = useState({});
  const [selectedInterventionTargetId, setSelectedInterventionTargetId] = useState("");
  const [loadedPredictionContext, setLoadedPredictionContext] = useState({
    end: "",
    site: "",
    direction: "",
  });

  const mapContainerRef = useRef(null);
  const popupRef = useRef(null);
  const mapRef = useRef(null);
  const AMapRef = useRef(null);
  const overlaysRef = useRef([]);
  const timeWrapRef = useRef(null);
  const mainRequestRef = useRef(0);
  const compareRequestRef = useRef(0);
  const [popupBeam, setPopupBeam] = useState(null);

  useEffect(() => {
    async function bootstrap() {
      setLoading(true);
      setError("");
      try {
        const [config, range, routeData, options, meta, versions] = await Promise.all([
          fetchPublicConfig(),
          fetchDataRange(),
          fetchDirectionRoutes(),
          fetchPredictionOptions(),
          fetchPredictionMeta(),
          fetchDatasetVersions(),
        ]);

        const min = toInputTime(range.min_timestamp);
        const max = toInputTime(range.max_timestamp);
        const initialRange = clampInitialRange(min, max);
        const directionMap = {};
        (options || []).forEach((item) => {
          directionMap[item.site_name] = item.directions?.[0] || "";
        });

        setAmapKey(config.amap_api_key || "");
        setIntersections(config.intersections || []);
        setRoutes(routeData || []);
        setPredictionOptions(options || []);
        setPredictionMeta(meta);
        setDatasetVersions(versions || []);
        setTimeLimit({ min, max });
        setStartInput(initialRange.start);
        setEndInput(initialRange.end);
        setAppliedRange(initialRange);
        setPickerMonth(monthKey(initialRange.start));
        setSiteDirections(directionMap);
        setSelectedSite(options?.[0]?.site_name || config.intersections?.[0]?.site_name || "");
      } catch (bootstrapError) {
        setError(`鍒濆鍖栧け璐ワ細${bootstrapError.message}`);
      } finally {
        setLoading(false);
      }
    }

    bootstrap();
  }, []);

  const selectedDirection = useMemo(() => siteDirections[selectedSite] || "", [siteDirections, selectedSite]);

  useEffect(() => {
    if (!appliedRange.start || !appliedRange.end || !selectedSite || !selectedDirection) return;

    async function loadMainData() {
      const requestId = mainRequestRef.current + 1;
      mainRequestRef.current = requestId;
      setLoading(true);
      setError("");
      try {
        const [trafficData, predictionData, flowData] = await Promise.all([
          fetchTraffic({
            start_time: toApiTime(appliedRange.start),
            end_time: toApiTime(appliedRange.end),
            limit: 5000,
          }),
          fetchPrediction({
            time_range: toApiTime(appliedRange.end),
            road_name: selectedSite,
            direction: selectedDirection,
          }),
          fetchDirectionFlows({
            start_time: toApiTime(appliedRange.start),
            end_time: toApiTime(appliedRange.end),
          }),
        ]);
        if (requestId !== mainRequestRef.current) return;
        setTrafficRows(trafficData || []);
        setDirectionFlows(flowData || []);
        setPredictionRows(predictionData || []);
        setLoadedPredictionContext({
          end: appliedRange.end,
          site: selectedSite,
          direction: selectedDirection,
        });
      } catch (loadError) {
        if (requestId !== mainRequestRef.current) return;
        setError(`涓绘暟鎹姞杞藉け璐ワ細${loadError.message}`);
      } finally {
        if (requestId === mainRequestRef.current) {
          setLoading(false);
        }
      }
    }

    loadMainData();
  }, [appliedRange, selectedSite, selectedDirection]);

  useEffect(() => {
    if (!appliedRange.end || !selectedSite || !selectedDirection) return;

    async function loadCompareData() {
      const requestId = compareRequestRef.current + 1;
      compareRequestRef.current = requestId;
      setCompareLoading(true);
      setPredictionCompare(null);
      try {
        const compareData = await fetchPredictionCompare({
          time_range: toApiTime(appliedRange.end),
          road_name: selectedSite,
          direction: selectedDirection,
        });
        if (requestId !== compareRequestRef.current) return;
        setPredictionCompare(compareData);
      } catch {
        if (requestId !== compareRequestRef.current) return;
        setPredictionCompare(null);
      } finally {
        if (requestId === compareRequestRef.current) {
          setCompareLoading(false);
        }
      }
    }

    loadCompareData();
  }, [appliedRange.end, selectedSite, selectedDirection]);

  useEffect(() => {
    if (!popupOpen || !selectedSite || !selectedDirection || !appliedRange.start || !appliedRange.end) return;

    async function loadPopupData() {
      try {
        const data = await fetchAnalysis({
          start_time: toApiTime(appliedRange.start),
          end_time: toApiTime(appliedRange.end),
          interval_minutes: resolveInterval(popupIntervalMode, appliedRange.start, appliedRange.end),
          road_name: selectedSite,
          direction: selectedDirection,
        });
        setPopupAnalysis(data || []);
      } catch {
        setPopupAnalysis([]);
      }
    }

    loadPopupData();
  }, [popupOpen, popupIntervalMode, appliedRange, selectedSite, selectedDirection]);

  const siteStats = useMemo(
    () => buildSiteStats(trafficRows, predictionOptions, siteDirections).slice(0, 4),
    [trafficRows, predictionOptions, siteDirections]
  );
  const monitorRows = useMemo(() => buildMonitorRows(trafficRows), [trafficRows]);
  const interventionTargets = useMemo(() => buildInterventionTargets(monitorRows), [monitorRows]);
  const selectedInterventionTarget = useMemo(
    () => interventionTargets.find((item) => item.id === selectedInterventionTargetId) || null,
    [interventionTargets, selectedInterventionTargetId]
  );
  const selectedSummary = useMemo(
    () => siteStats.find((item) => item.siteName === selectedSite) || siteStats[0],
    [siteStats, selectedSite]
  );
  const predictionStats = useMemo(() => buildPredictionStats(predictionRows), [predictionRows]);
  const predictionChartData = useMemo(() => {
    if (!loadedPredictionContext.end || !loadedPredictionContext.site || !loadedPredictionContext.direction) {
      return [];
    }
    const historyRows = buildHistorySeries(
      trafficRows,
      loadedPredictionContext.site,
      loadedPredictionContext.direction,
      loadedPredictionContext.end
    );
    return mergePredictionSeries(historyRows, predictionRows);
  }, [trafficRows, predictionRows, loadedPredictionContext]);
  const interventions = useMemo(
    () => buildInterventions(selectedInterventionTarget ? [selectedInterventionTarget] : []),
    [selectedInterventionTarget]
  );
  const allInterventionEffects = useMemo(
    () => [...(interventions.agencyActions || []), ...(interventions.userMessages || [])],
    [interventions]
  );
  const appliedInterventionEffects = useMemo(
    () => allInterventionEffects.filter((item) => executedActions[item.id]),
    [allInterventionEffects, executedActions]
  );
  const activeInterventionTargetId = selectedInterventionTargetId;
  const effectSeries = useMemo(
    () =>
      buildEffectSeries(predictionRows, appliedInterventionEffects).map((item) => ({
        ...item,
        label: formatShortTime(item.label),
      })),
    [predictionRows, appliedInterventionEffects]
  );
  const reasons = useMemo(
    () => buildRiskReasons(selectedSummary, predictionStats),
    [selectedSummary, predictionStats]
  );
  const directionPieData = useMemo(
    () => buildDirectionPie(trafficRows, selectedSite),
    [trafficRows, selectedSite]
  );
  const popupVehicleDomain = useMemo(
    () => axisDomain((popupAnalysis || []).map((item) => Number(item.vehicle_count) || 0)),
    [popupAnalysis]
  );
  const popupSpeedDomain = useMemo(
    () => axisDomain((popupAnalysis || []).map((item) => Number(item.avg_speed) || 0)),
    [popupAnalysis]
  );
  const weather = useMemo(
    () => buildWeatherBadge(trafficRows, loadedPredictionContext.site, loadedPredictionContext.direction),
    [trafficRows, loadedPredictionContext]
  );
  const congestionBefore = useMemo(
    () => congestionIndex(selectedSummary?.avgVehicle || 0, selectedSummary?.avgSpeed || 40),
    [selectedSummary]
  );
  const congestionAfterValue = useMemo(
    () => congestionAfter(effectSeries, congestionBefore),
    [effectSeries, congestionBefore]
  );

  useEffect(() => {
    setExecutedActions({});
    setSelectedInterventionTargetId("");
  }, [appliedRange.start, appliedRange.end]);

  useEffect(() => {
    if (!selectedInterventionTargetId) return;
    if (!interventionTargets.some((item) => item.id === selectedInterventionTargetId)) {
      setSelectedInterventionTargetId("");
    }
  }, [interventionTargets, selectedInterventionTargetId]);

  useEffect(() => {
    if (!mapContainerRef.current || !intersections.length || !amapKey) return;
    let canceled = false;

    async function initMap() {
      if (mapRef.current) return;
      try {
        const securityJsCode = process.env.REACT_APP_AMAP_SECURITY_JS_CODE;
        if (securityJsCode) {
          window._AMapSecurityConfig = { ...window._AMapSecurityConfig, securityJsCode };
        }
        const AMap = await AMapLoader.load({
          key: amapKey,
          version: "2.0",
          plugins: ["AMap.Scale"],
        });
        if (canceled) return;
        AMapRef.current = AMap;
        const center = intersections.length
          ? [
              intersections.reduce((sum, item) => sum + item.longitude, 0) / intersections.length,
              intersections.reduce((sum, item) => sum + item.latitude, 0) / intersections.length,
            ]
          : MAP_DEFAULT.center;
        const map = new AMap.Map(mapContainerRef.current, {
          center,
          zoom: MAP_DEFAULT.zoom,
          mapStyle: "amap://styles/darkblue",
          viewMode: "2D",
          terrain: false,
          resizeEnable: true,
          showBuildingBlock: false,
        });
        map.addControl(new AMap.Scale());
        mapRef.current = map;
        setMapReady(true);
      } catch (mapError) {
        setError(`鍦板浘鍔犺浇澶辫触锛?{mapError.message}`);
      }
    }

    initMap();
    return () => {
      canceled = true;
    };
  }, [amapKey, intersections]);

  useEffect(() => {
    if (!mapRef.current || !AMapRef.current) return;

    overlaysRef.current.forEach((item) => item.setMap(null));
    overlaysRef.current = [];

    const AMap = AMapRef.current;
    const flowMap = new Map(directionFlows.map((item) => [`${item.site_name}|${item.direction}`, item]));

    intersections.forEach((site) => {
      const flow = flowMap.get(`${site.site_name}|${siteDirections[site.site_name]}`);
      const color = FLOW_COLORS[flow?.level || "unknown"];
      const marker = new AMap.Marker({
        position: [site.longitude, site.latitude],
        anchor: "bottom-center",
        content: `
          <div class="map-marker ${selectedSite === site.site_name ? "active" : ""}">
            <div class="map-marker__label">${site.site_name}</div>
            <div class="map-node">
              <span class="map-node__core" style="background:${color}"></span>
            </div>
          </div>
        `,
      });
      marker.on("click", () => {
        setSelectedSite(site.site_name);
        setPopupOpen(true);
      });
      marker.setMap(mapRef.current);
      overlaysRef.current.push(marker);
    });

    routes.forEach((route) => {
      if (route.path.length < 2) return;
      const flow = flowMap.get(`${route.site_name}|${route.direction}`);
      const highlighted = route.site_name === selectedSite && route.direction === selectedDirection;
      const polyline = new AMap.Polyline({
        path: route.path.map(({ longitude, latitude }) => [longitude, latitude]),
        strokeColor: FLOW_COLORS[flow?.level || "unknown"],
        strokeWeight: 4,
        strokeOpacity: flow ? 0.96 : 0.55,
        lineCap: "round",
        lineJoin: "round",
        showDir: flow?.average_count > 0,
        zIndex: highlighted ? 65 : 60,
        extData: { site: route.site_name, direction: route.direction, flow },
      });
      polyline.on("click", () => {
        setSelectedSite(route.site_name);
        setSiteDirections((previous) => ({ ...previous, [route.site_name]: route.direction }));
        setPopupOpen(true);
      });
      polyline.on("mouseover", () => {
        mapContainerRef.current.title = `${route.site_name} · ${route.direction}\n${flowDescription(flow)}`;
      });
      polyline.on("mouseout", () => { mapContainerRef.current.title = ""; });
      polyline.setMap(mapRef.current);
      overlaysRef.current.push(polyline);
    });
  }, [intersections, routes, directionFlows, siteDirections, selectedSite, selectedDirection, mapReady]);

  useEffect(() => {
    if (!popupOpen || !selectedSite || !mapRef.current || !popupRef.current || !mapContainerRef.current) {
      setPopupBeam(null);
      return;
    }

    function updatePopupBeam() {
      const site = intersections.find((item) => item.site_name === selectedSite);
      if (!site || !mapRef.current || !popupRef.current || !mapContainerRef.current) return;

      const pixel = mapRef.current.lngLatToContainer([site.longitude, site.latitude]);
      const mapRect = mapContainerRef.current.getBoundingClientRect();
      const popupEl = popupRef.current;
      const popupRect = popupEl.getBoundingClientRect();
      const startX = mapRect.left + pixel.x;
      const startY = mapRect.top + pixel.y - 13;
      const elbowX = popupRect.left - 54;
      const elbowY = popupRect.top + popupRect.height - 86;
      const targetX = popupRect.left + 4;
      const targetY = elbowY;

      setPopupBeam({
        startX,
        startY,
        elbowX,
        elbowY,
        targetX,
        targetY,
      });
    }

    const timer = window.setTimeout(updatePopupBeam, 30);
    const handleResize = () => updatePopupBeam();
    window.addEventListener("resize", handleResize);

    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", handleResize);
    };
  }, [popupOpen, selectedSite, intersections, selectedDirection, popupAnalysis]);

  useEffect(() => {
    if (!mapRef.current || !mapRef.current.setStatus) return;
    mapRef.current.setStatus({
      dragEnable: !popupOpen,
      zoomEnable: !popupOpen,
      doubleClickZoom: !popupOpen,
      keyboardEnable: !popupOpen,
      scrollWheel: !popupOpen,
    });
  }, [popupOpen]);

  useEffect(() => {
    if (!timeOpen) return undefined;

    function handlePointerDown(event) {
      if (timeWrapRef.current && !timeWrapRef.current.contains(event.target)) {
        setStartInput(appliedRange.start);
        setEndInput(appliedRange.end);
        setPickerOpen(false);
        setTimeOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [timeOpen, appliedRange]);

  const startParts = useMemo(() => parseInputParts(startInput), [startInput]);
  const endParts = useMemo(() => parseInputParts(endInput), [endInput]);
  const activeParts = activeTimeField === "start" ? startParts : endParts;
  const calendarDays = useMemo(() => buildCalendar(pickerMonth || monthKey(startInput || DEFAULT_START)), [pickerMonth, startInput]);
  const minEnd = useMemo(() => (startInput ? addMinutes(startInput, 60) : timeLimit.min), [startInput, timeLimit.min]);

  function updateStart(nextStart) {
    if (!withinLimits(nextStart, timeLimit.min, timeLimit.max)) return;
    const nextEnd = addMinutes(nextStart, 60);
    setStartInput(nextStart);
    setEndInput(withinLimits(nextEnd, timeLimit.min, timeLimit.max) ? nextEnd : endInput);
    setPickerMonth(monthKey(nextStart));
  }

  function updateEnd(nextEnd) {
    if (!withinLimits(nextEnd, minEnd, timeLimit.max)) return;
    setEndInput(nextEnd);
    setPickerMonth(monthKey(nextEnd));
  }

  function updateActiveDate(nextDate) {
    if (!nextDate) return;
    if (activeTimeField === "start") {
      updateStart(`${nextDate}T${startParts.hour}:${startParts.minute}`);
      return;
    }
    updateEnd(`${nextDate}T${endParts.hour}:${endParts.minute}`);
  }

  function updateActiveHour(nextHour) {
    if (activeTimeField === "start") {
      updateStart(`${startParts.date}T${nextHour}:${startParts.minute}`);
      return;
    }
    updateEnd(`${endParts.date}T${nextHour}:${endParts.minute}`);
  }

  function updateActiveMinute(nextMinute) {
    if (activeTimeField === "start") {
      updateStart(`${startParts.date}T${startParts.hour}:${nextMinute}`);
      return;
    }
    updateEnd(`${endParts.date}T${endParts.hour}:${nextMinute}`);
  }

  function confirmRange() {
    if (!startInput || !endInput) return;
    if (new Date(endInput) - new Date(startInput) < 3600000) return;
    setAppliedRange({ start: startInput, end: endInput });
    setPickerOpen(false);
    setTimeOpen(false);
  }

  function runAction(action) {
    setExecutedActions((prev) => ({ ...prev, [action.id]: "执行中" }));
    window.setTimeout(async () => {
      try {
        if (action.message) {
          await postUserAlert({
            title: `${action.siteName || selectedSite || "当前路口"}出行提醒`,
            message: action.message,
            site_name: action.siteName || selectedSite || null,
            direction: action.direction || selectedDirection || null,
            start_time: appliedRange.start ? toApiTime(appliedRange.start) : null,
            end_time: appliedRange.end ? toApiTime(appliedRange.end) : null,
            action_label: action.actionLabel || "推送",
          });
        }
        setExecutedActions((prev) => ({ ...prev, [action.id]: action.message ? "已推送" : "已下发" }));
      } catch (runError) {
        setExecutedActions((prev) => ({ ...prev, [action.id]: "失败" }));
        setError(`消息下发失败：${runError.message}`);
      }
    }, 800);
  }

  function focusInterventionTarget(target) {
    if (!target?.siteName || !target?.direction) return;
    setSelectedInterventionTargetId(target.id);
    setSelectedSite(target.siteName);
    setSiteDirections((prev) => ({ ...prev, [target.siteName]: target.direction }));
    setPopupOpen(false);
  }

  function resetMapView() {
    if (!mapRef.current) return;
    mapRef.current.setZoomAndCenter(MAP_DEFAULT.zoom, MAP_DEFAULT.center);
  }

  return (
    <div className="admin-shell">
      <header className="top-header">
        <div className="brand">
          <div className="brand__icon" />
          <h1>智能交通干预系统</h1>
        </div>
        <div className="header-line" />
      </header>

      <section className="top-control-row">
        <label className="dataset-version-control" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span>数据版本</span>
          <select value={datasetVersion} onChange={(event) => { const value = event.target.value; setDatasetVersion(value); localStorage.setItem("trafficDatasetVersion", value); window.location.reload(); }}>
            {datasetVersions.map((item) => <option key={item.version_id} value={item.version_id}>{item.display_name}</option>)}
          </select>
        </label>
        <div className="road-cards">
          {siteStats.map((item) => (
            <div
              key={item.siteName}
              className={`road-card ${selectedSite === item.siteName ? "active" : ""}`}
              onClick={() => setSelectedSite(item.siteName)}
              role="button"
              tabIndex={0}
            >
              <div className="road-card__top">
                <span>{item.siteName}</span>
                <select
                  value={item.direction}
                  onChange={(event) =>
                    setSiteDirections((prev) => ({ ...prev, [item.siteName]: event.target.value }))
                  }
                  onClick={(event) => event.stopPropagation()}
                >
                  {item.directions.map((direction) => (
                    <option key={direction} value={direction}>
                      {direction}
                    </option>
                  ))}
                </select>
              </div>
              <div className="road-card__value">{item.avgVehicle || 0}</div>
              <div className="road-card__meta">
                <span>平均车辆</span>
                <i style={{ backgroundColor: RISK_COLORS[item.risk] }} />
              </div>
            </div>
          ))}
        </div>

        <div className="time-wrap" ref={timeWrapRef}>
          <button
            type="button"
            className="time-trigger"
            onClick={() => {
              if (!timeOpen) {
                setStartInput(appliedRange.start);
                setEndInput(appliedRange.end);
                setActiveTimeField("start");
                setPickerMonth(monthKey(appliedRange.start));
                setPickerOpen(false);
              }
              setTimeOpen((value) => !value);
            }}
          >
            <strong>{formatRange(appliedRange.start, appliedRange.end)}</strong>
            <div className="time-trigger__icon">
              <span />
              <span />
              <span />
            </div>
          </button>

          {timeOpen ? (
            <div className={`time-panel ${pickerOpen ? "expanded" : ""}`}>
              <label>
                <span>起始时间</span>
                <div className={`time-display ${activeTimeField === "start" ? "active" : ""}`}>
                  <strong>{toDisplayValue(startInput)}</strong>
                  <button
                    type="button"
                    className="time-display__icon"
                    aria-label="选择起始时间"
                    onClick={() => {
                      setActiveTimeField("start");
                      setPickerMonth(monthKey(startInput));
                      setPickerOpen((value) => (activeTimeField === "start" ? !value : true));
                    }}
                  />
                </div>
              </label>
              <label>
                <span>结束时间</span>
                <div className={`time-display ${activeTimeField === "end" ? "active" : ""}`}>
                  <strong>{toDisplayValue(endInput)}</strong>
                  <button
                    type="button"
                    className="time-display__icon"
                    aria-label="选择结束时间"
                    onClick={() => {
                      setActiveTimeField("end");
                      setPickerMonth(monthKey(endInput));
                      setPickerOpen((value) => (activeTimeField === "end" ? !value : true));
                    }}
                  />
                </div>
              </label>
              {pickerOpen ? (
                <div className={`picker-shell picker-shell--${activeTimeField}`}>
                  <div className="picker-shell__inner">
                    <div className="picker-calendar">
                      <div className="picker-calendar__head">
                        <button
                          type="button"
                          className="picker-nav"
                          onClick={() => setPickerMonth((value) => shiftMonth(value, -1))}
                        >
                          ‹
                        </button>
                        <strong>{pickerMonth.replace("-", "年")}月</strong>
                        <button
                          type="button"
                          className="picker-nav"
                          onClick={() => setPickerMonth((value) => shiftMonth(value, 1))}
                        >
                          ›
                        </button>
                      </div>
                      <div className="picker-weekdays">
                        {WEEKDAYS.map((day) => (
                          <span key={day}>{day}</span>
                        ))}
                      </div>
                      <div className="picker-days">
                        {calendarDays.map((day) => {
                          const parts = activeTimeField === "start" ? startParts : endParts;
                          const candidate = `${day.value}T${parts.hour}:${parts.minute}`;
                          const disabled =
                            !day.inMonth ||
                            !withinLimits(
                              candidate,
                              activeTimeField === "start" ? timeLimit.min : minEnd,
                              timeLimit.max
                            );
                          const selected = day.value === parts.date;
                          return (
                            <button
                              key={day.value}
                              type="button"
                              className={`picker-day ${selected ? "active" : ""}`}
                              disabled={disabled}
                              onClick={() => updateActiveDate(day.value)}
                            >
                              {day.day}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <div className="picker-columns">
                      <div className="picker-column">
                        <div className="picker-column__title">时</div>
                        <div className="picker-column__list">
                          {HOUR_OPTIONS.map((hour) => {
                            const candidate = `${activeParts.date}T${hour}:${activeParts.minute}`;
                            const disabled = !withinLimits(
                              candidate,
                              activeTimeField === "start" ? timeLimit.min : minEnd,
                              timeLimit.max
                            );
                            return (
                              <button
                                key={hour}
                                type="button"
                                className={`picker-chip ${activeParts.hour === hour ? "active" : ""}`}
                                disabled={disabled}
                                onClick={() => updateActiveHour(hour)}
                              >
                                {hour}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                      <div className="picker-column">
                        <div className="picker-column__title">分</div>
                        <div className="picker-column__list">
                          {MINUTE_OPTIONS.map((minute) => {
                            const candidate = `${activeParts.date}T${activeParts.hour}:${minute}`;
                            const disabled = !withinLimits(
                              candidate,
                              activeTimeField === "start" ? timeLimit.min : minEnd,
                              timeLimit.max
                            );
                            return (
                              <button
                                key={minute}
                                type="button"
                                className={`picker-chip ${activeParts.minute === minute ? "active" : ""}`}
                                disabled={disabled}
                                onClick={() => updateActiveMinute(minute)}
                              >
                                {minute}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="picker-shell__foot">
                    <button
                      type="button"
                      className="picker-confirm"
                      onClick={() => setPickerOpen(false)}
                    >
                      确定
                    </button>
                  </div>
                </div>
              ) : null}
              <button type="button" className="time-apply" onClick={confirmRange}>
                确定
              </button>
            </div>
          ) : null}
        </div>
      </section>

      <section className="main-grid">
        <aside className="side-panel side-panel--intervention">
          <div className="panel-frame">
            <div className="panel-head">
              <h2>干预建议</h2>
            </div>
            <div className="intervention-focus">
              <div className="intervention-focus__head">
                <strong>重点干预对象</strong>
                <span>当前时间段内交通压力较大的路口及其方向</span>
              </div>
              {interventionTargets.length ? (
                <div className="intervention-focus__list">
                  {interventionTargets.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={`focus-chip ${activeInterventionTargetId === item.id ? "is-active" : ""}`}
                      onClick={() => focusInterventionTarget(item)}
                    >
                      <strong>{item.siteName}</strong>
                      <span>{item.direction}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="empty-card">当前时段暂无明显干预对象</div>
              )}
            </div>

            {selectedInterventionTarget ? (
              <div className="intervention-grid">
                <div className="intervention-section">
                  <div className="intervention-list intervention-list--framed">
                    <div className="intervention-list__title">面向交通管理部门</div>
                    {interventions.agencyActions.length ? (
                      interventions.agencyActions.map((item) => (
                        <article key={item.id} className="action-row">
                          <div className="action-row__content">
                            <i className="action-row__dot" />
                            <div className="action-row__copy">
                              <p>{item.reason}</p>
                              <small>{item.effectText}</small>
                            </div>
                          </div>
                          <div className="action-row__side">
                            <button type="button" onClick={() => runAction(item)}>
                              {executedActions[item.id] || item.actionLabel || "执行"}
                            </button>
                          </div>
                        </article>
                      ))
                    ) : (
                      <div className="empty-card">当前时段暂无管理部门干预建议</div>
                    )}
                  </div>
                </div>

                <div className="intervention-section">
                  <div className="intervention-list intervention-list--framed">
                    <div className="intervention-list__title">面向用户推送</div>
                    {interventions.userMessages.length ? (
                      interventions.userMessages.map((item) => (
                        <article key={item.id} className="action-row action-row--message">
                          <div className="action-row__content">
                            <i className="action-row__dot" />
                            <div className="action-row__copy">
                              <p>{item.message}</p>
                              <small>{item.effectText}</small>
                            </div>
                          </div>
                          <div className="action-row__side">
                            <button type="button" onClick={() => runAction(item)}>
                              {executedActions[item.id] || item.actionLabel || "推送"}
                            </button>
                          </div>
                        </article>
                      ))
                    ) : (
                      <div className="empty-card">当前时段暂无可推送的用户消息</div>
                    )}
                  </div>
                </div>
              </div>
            ) : null}

            <div className="intervention-effect">
              <div className="panel-head intervention-effect__head">
                <h2>干预结果预测</h2>
                <span>未来 1 小时干预前后对比</span>
              </div>
              <div className="effect-summary">
                <article>
                  <span>执行策略数</span>
                  <strong>{Object.keys(executedActions).length}</strong>
                </article>
                <article>
                  <span>拥堵指数前</span>
                  <strong>{congestionBefore}</strong>
                </article>
                <article>
                  <span>拥堵指数后</span>
                  <strong>{congestionAfterValue}</strong>
                </article>
              </div>
              <ResponsiveContainer width="100%" height={150}>
                <LineChart
                  key={`${Object.keys(executedActions).sort().join("|")}-${effectSeries.length}`}
                  data={effectSeries}
                  margin={{ top: 8, right: 28, left: -18, bottom: 0 }}
                >
                  <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <Tooltip content={<EffectChartTooltip />} cursor={{ stroke: "rgba(114, 231, 255, 0.24)", strokeWidth: 1 }} />
                  <Line
                    type="monotone"
                    dataKey="before"
                    stroke="#ff6b6b"
                    strokeWidth={2.2}
                    dot={false}
                    isAnimationActive={false}
                  />
                  {appliedInterventionEffects.length ? (
                    <Line
                      type="monotone"
                      dataKey="after"
                      stroke="#57efb2"
                      strokeWidth={2.4}
                      dot={false}
                      isAnimationActive
                      animationDuration={420}
                    />
                  ) : null}
                </LineChart>
              </ResponsiveContainer>
              <div className="prediction-legend intervention-effect__legend">
                <span><i className="legend-line intervention-before" /> 红线：干预前预测</span>
                {appliedInterventionEffects.length ? (
                  <span><i className="legend-line intervention-after" /> 绿线：干预后预测</span>
                ) : null}
              </div>
            </div>
          </div>
        </aside>

        <section className="center-column">
          <div className="map-frame">
            <button type="button" className="map-reset-button" onClick={resetMapView} aria-label="回到默认视角">
              <span className="map-reset-button__icon">◎</span>
            </button>
            <div ref={mapContainerRef} className="map-canvas" />
            <div className="traffic-legend" title="按本方向历史5分钟车流的50%和80%分位分级，统计所选时段平均车流。">
              <strong>时段车流</strong>
              <span><i style={{ background: FLOW_COLORS.low }} />较低</span>
              <span><i style={{ background: FLOW_COLORS.medium }} />中等</span>
              <span><i style={{ background: FLOW_COLORS.high }} />较高</span>
              <span><i style={{ background: FLOW_COLORS.unknown }} />无数据</span>
              <small>各方向历史车流分级</small>
            </div>
          </div>
        </section>

        <aside className="side-panel">
          <div className="panel-frame">
            <div className="panel-head">
              <h2>预测预警</h2>
              <div className="prediction-controls">
                <select
                  value={selectedDirection}
                  onChange={(event) =>
                    setSiteDirections((prev) => ({ ...prev, [selectedSite]: event.target.value }))
                  }
                >
                  {(predictionOptions.find((item) => item.site_name === selectedSite)?.directions || []).map((direction) => (
                    <option key={direction} value={direction}>
                      {direction}
                    </option>
                  ))}
                </select>
                <select
                  value={selectedSite}
                  onChange={(event) => {
                    const nextSite = event.target.value;
                    setSelectedSite(nextSite);
                    const nextDirections = predictionOptions.find((item) => item.site_name === nextSite)?.directions || [];
                    if (nextDirections.length) {
                      setSiteDirections((prev) => ({ ...prev, [nextSite]: prev[nextSite] || nextDirections[0] }));
                    }
                  }}
                >
                  {predictionOptions.map((item) => (
                    <option key={item.site_name} value={item.site_name}>
                      {item.site_name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="prediction-plot prediction-plot--fresh">
              <div className="prediction-weather-badge">
                <span className="prediction-weather-icon">{weatherIcon(weather.iconKey || weather.headline)}</span>
                <div className="prediction-weather-copy">
                  <strong>{weather.headline}</strong>
                  <span>{weather.breakdown}</span>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart
                  key={`${loadedPredictionContext.site}-${loadedPredictionContext.direction}-${loadedPredictionContext.end}-${predictionChartData.length}`}
                  data={predictionChartData}
                  margin={{ top: 8, right: 10, left: -18, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="predictionFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#5ecdfc" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#5ecdfc" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <Tooltip content={<PredictionTooltip />} cursor={{ stroke: "rgba(114, 231, 255, 0.28)", strokeWidth: 1 }} />
                  <Line type="monotone" dataKey="history" stroke="#4f8cf7" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Area type="monotone" dataKey="predicted" stroke="none" fill="url(#predictionFill)" isAnimationActive={false} />
                  <Line
                    type="monotone"
                    dataKey="predicted"
                    stroke="#8f9dff"
                    strokeWidth={2.3}
                    dot={false}
                    isAnimationActive
                    animationDuration={700}
                    animationEasing="ease-out"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="prediction-legend">
              <span><i className="legend-line history" /> 当前已有数据</span>
              <span><i className="legend-line forecast" /> 未来 1 小时预测</span>
            </div>

            <div className="prediction-grid">
              <div className="prediction-grid__card prediction-grid__card--metric">
                <span>峰值</span>
                <strong>{predictionStats.peak}</strong>
              </div>
              <div className="prediction-grid__card prediction-grid__card--metric">
                <span>低值</span>
                <strong>{predictionStats.low}</strong>
              </div>
              <div className="prediction-grid__card prediction-grid__card--metric">
                <span>平均值</span>
                <strong>{predictionStats.avg}</strong>
              </div>
            </div>
            <div className="prediction-backtest-label">该时间点回看误差</div>
            <div className="prediction-grid prediction-grid--secondary">
              <div className="prediction-grid__card prediction-grid__card--wide">
                <span>误差百分比</span>
                <strong>{compareLoading ? "计算中..." : predictionCompare?.mape ? `${predictionCompare.mape}%` : "--"}</strong>
              </div>
              <div className="prediction-grid__card prediction-grid__card--wide">
                <span>误差 MAE</span>
                <strong>{compareLoading ? "计算中..." : predictionCompare?.mae ?? "--"}</strong>
              </div>
            </div>

            <div className="reason-list">
              {reasons.map((item) => (
                <div key={item} className="reason-tag">
                  {item}
                </div>
              ))}
            </div>

            <div className="model-note">
              <span className="model-note__label">提示</span>
              <span className="model-note__text">
                当前预测基于历史交通流、速度、天气与时序特征生成未来一小时车流结果，按 10 分钟粒度展示。
                模型：{predictionMeta?.model_type || "unavailable"}，测试集 MAE {predictionMeta?.test_mae ?? "--"}，RMSE {predictionMeta?.test_rmse ?? "--"}。
              </span>
            </div>
          </div>
        </aside>
      </section>
      {popupOpen && popupBeam ? (
        <svg className="popup-beam-overlay" aria-hidden="true">
          <defs>
            <linearGradient id="popupBeamStroke" x1="0%" x2="100%" y1="0%" y2="0%">
              <stop offset="0%" stopColor="rgba(115,231,255,0.95)" />
              <stop offset="100%" stopColor="rgba(115,231,255,0.18)" />
            </linearGradient>
          </defs>
          <circle className="popup-beam-overlay__node" cx={popupBeam.startX} cy={popupBeam.startY} r="5" />
          <circle className="popup-beam-overlay__target" cx={popupBeam.targetX} cy={popupBeam.targetY} r="4" />
          <path
            className="popup-beam-overlay__path"
            d={`M ${popupBeam.startX} ${popupBeam.startY} L ${popupBeam.elbowX} ${popupBeam.elbowY} L ${popupBeam.targetX} ${popupBeam.targetY}`}
            pathLength="1"
          />
          <path
            className="popup-beam-overlay__signal"
            d={`M ${popupBeam.startX} ${popupBeam.startY} L ${popupBeam.elbowX} ${popupBeam.elbowY} L ${popupBeam.targetX} ${popupBeam.targetY}`}
            pathLength="1"
          />
        </svg>
      ) : null}

      {popupOpen ? (
        <div className="detail-popup" ref={popupRef}>
          <div className="detail-popup__head">
            <div className="detail-popup__title">
              <strong>{selectedSite}</strong>
            </div>
            <button type="button" className="detail-popup__close" aria-label="关闭详情" onClick={() => setPopupOpen(false)}>
              ×
            </button>
          </div>

          <div className="detail-popup__toolbar">
            <label className="popup-control">
              <span>方向</span>
              <select
                value={selectedDirection}
                onChange={(event) =>
                  setSiteDirections((prev) => ({ ...prev, [selectedSite]: event.target.value }))
                }
              >
                {(predictionOptions.find((item) => item.site_name === selectedSite)?.directions || []).map((direction) => (
                  <option key={direction} value={direction}>
                    {direction}
                  </option>
                ))}
              </select>
            </label>
            <label className="popup-control">
              <span>时间颗粒度</span>
              <select value={popupIntervalMode} onChange={(event) => setPopupIntervalMode(event.target.value)}>
                {INTERVAL_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="detail-charts">
            <div className="mini-chart">
              <div className="mini-chart__head">
                <span>车流量</span>
              </div>
              <ResponsiveContainer width="100%" height={136}>
                <BarChart data={popupAnalysis} margin={{ top: 4, right: 24, left: -28, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                  <XAxis dataKey="bucket_start" tickFormatter={formatShortTime} tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} domain={popupVehicleDomain} />
                  <Tooltip content={<PopupChartTooltip />} cursor={{ fill: "rgba(114, 231, 255, 0.08)" }} />
                  <Bar dataKey="vehicle_count" fill="#5ecdfc" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              <div className="mini-chart__meta">
                <span className="mini-chart__dot" />
                <span>平均车流量 {selectedSummary?.avgVehicle || 0} 辆</span>
              </div>
            </div>

            <div className="mini-chart">
              <div className="mini-chart__head">
                <span>车速</span>
              </div>
              <ResponsiveContainer width="100%" height={136}>
                <LineChart data={popupAnalysis} margin={{ top: 4, right: 24, left: -28, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                  <XAxis dataKey="bucket_start" tickFormatter={formatShortTime} tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} domain={popupSpeedDomain} />
                  <Tooltip content={<PopupChartTooltip />} cursor={{ stroke: "rgba(114, 231, 255, 0.24)", strokeWidth: 1 }} />
                  <Line type="monotone" dataKey="avg_speed" stroke="#7bf4cf" strokeWidth={2.2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
              <div className="mini-chart__meta">
                <span className="mini-chart__dot" />
                <span>平均车速 {selectedSummary?.avgSpeed || 0} km/h</span>
              </div>
            </div>
          </div>

          <div className="wheel-block">
            <span>方向车流分布</span>
            <div className="wheel-layout">
              <ResponsiveContainer width="62%" height={124}>
                <PieChart>
                  <Pie data={directionPieData} dataKey="value" nameKey="name" innerRadius={24} outerRadius={46} cx="66%" cy="50%">
                    {directionPieData.map((item, index) => (
                      <Cell key={item.name} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
              <div className="wheel-legend">
                {directionPieData.map((item, index) => (
                  <div key={item.name}>
                    <i style={{ backgroundColor: PIE_COLORS[index % PIE_COLORS.length] }} />
                    <span>{item.name}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {error ? <div className="status-toast status-toast--error">{error}</div> : null}
      {!error && loading ? <div className="status-toast">数据同步中...</div> : null}
    </div>
  );
}

export default App;
