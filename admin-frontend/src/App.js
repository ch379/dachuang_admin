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
  fetchDirectionRoutes,
  fetchPrediction,
  fetchPredictionCompare,
  fetchPredictionMeta,
  fetchPredictionOptions,
  fetchPublicConfig,
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
  riskLabel,
  toApiTime,
  toInputTime,
} from "./adminHelpers";
import {
  buildEffectSeries,
  buildInterventions,
  buildRiskReasons,
  congestionAfter,
  congestionIndex,
  weatherSummary,
} from "./adminModel";
import "./App.css";

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
  return Array.from({ length: 42 }, (_, index) => {
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

function App() {
  const [amapKey, setAmapKey] = useState("");
  const [intersections, setIntersections] = useState([]);
  const [routes, setRoutes] = useState([]);
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
  const [trafficRows, setTrafficRows] = useState([]);
  const [predictionRows, setPredictionRows] = useState([]);
  const [predictionCompare, setPredictionCompare] = useState(null);
  const [popupOpen, setPopupOpen] = useState(false);
  const [popupIntervalMode, setPopupIntervalMode] = useState("auto");
  const [popupAnalysis, setPopupAnalysis] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [executedActions, setExecutedActions] = useState({});
  const [actionLogs, setActionLogs] = useState([]);

  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const AMapRef = useRef(null);
  const overlaysRef = useRef([]);
  const timeWrapRef = useRef(null);

  useEffect(() => {
    async function bootstrap() {
      setLoading(true);
      try {
        const [config, range, routeData, options, meta] = await Promise.all([
          fetchPublicConfig(),
          fetchDataRange(),
          fetchDirectionRoutes(),
          fetchPredictionOptions(),
          fetchPredictionMeta(),
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
        setTimeLimit({ min, max });
        setStartInput(initialRange.start);
        setEndInput(initialRange.end);
        setAppliedRange(initialRange);
        setPickerMonth(monthKey(initialRange.start));
        setSiteDirections(directionMap);
        setSelectedSite(options?.[0]?.site_name || config.intersections?.[0]?.site_name || "");
      } catch (bootstrapError) {
        setError(`初始化失败：${bootstrapError.message}`);
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
      setLoading(true);
      try {
        const [trafficData, predictionData, compareData] = await Promise.all([
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
          fetchPredictionCompare({
            time_range: toApiTime(appliedRange.end),
            road_name: selectedSite,
            direction: selectedDirection,
          }),
        ]);
        setTrafficRows(trafficData || []);
        setPredictionRows(predictionData || []);
        setPredictionCompare(compareData);
      } catch (loadError) {
        setError(`主数据加载失败：${loadError.message}`);
      } finally {
        setLoading(false);
      }
    }

    loadMainData();
  }, [appliedRange, selectedSite, selectedDirection]);

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
  const selectedSummary = useMemo(
    () => siteStats.find((item) => item.siteName === selectedSite) || siteStats[0],
    [siteStats, selectedSite]
  );
  const monitorRows = useMemo(() => buildMonitorRows(trafficRows), [trafficRows]);
  const predictionStats = useMemo(() => buildPredictionStats(predictionRows), [predictionRows]);
  const predictionChartData = useMemo(() => {
    const historyRows = buildHistorySeries(trafficRows, selectedSite, selectedDirection, appliedRange.end);
    return mergePredictionSeries(historyRows, predictionRows);
  }, [trafficRows, selectedSite, selectedDirection, appliedRange.end, predictionRows]);
  const interventions = useMemo(
    () => buildInterventions(selectedSummary, predictionStats),
    [selectedSummary, predictionStats]
  );
  const effectSeries = useMemo(
    () =>
      buildEffectSeries(predictionRows, interventions).map((item) => ({
        ...item,
        label: formatShortTime(item.label),
      })),
    [predictionRows, interventions]
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
    () => weatherSummary(trafficRows, selectedSite, selectedDirection),
    [trafficRows, selectedSite, selectedDirection]
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
    if (!mapContainerRef.current || !intersections.length || !amapKey) return;
    let canceled = false;

    async function initMap() {
      if (mapRef.current) return;
      try {
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
      } catch (mapError) {
        setError(`地图加载失败：${mapError.message}`);
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
    const riskMap = new Map(siteStats.map((item) => [item.siteName, item.risk]));

    intersections.forEach((site) => {
      const risk = riskMap.get(site.site_name) || "low";
      const marker = new AMap.Marker({
        position: [site.longitude, site.latitude],
        anchor: "bottom-center",
        content: `
          <div class="map-marker ${selectedSite === site.site_name ? "active" : ""}">
            <div class="map-marker__label">${site.site_name}</div>
            <div class="map-node">
              <span class="map-node__ring" style="border-color:${RISK_COLORS[risk]}"></span>
              <span class="map-node__core" style="background:${RISK_COLORS[risk]}"></span>
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
      const risk = riskMap.get(route.site_name) || "low";
      const highlighted = route.site_name === selectedSite && route.direction === selectedDirection;
      const polyline = new AMap.Polyline({
        path: route.path.map((point) => [point.longitude, point.latitude]),
        strokeColor: highlighted ? "#73e7ff" : RISK_COLORS[risk],
        strokeWeight: highlighted ? 6 : 4,
        strokeOpacity: highlighted ? 0.96 : 0.55,
        lineCap: "round",
      });
      polyline.setMap(mapRef.current);
      overlaysRef.current.push(polyline);
    });
  }, [intersections, routes, siteStats, selectedSite, selectedDirection]);

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
    setActionLogs((prev) => [
      {
        id: `${action.id}-${Date.now()}`,
        time: formatShortTime(new Date()),
        status: "执行中",
        text: `${action.title} / ${action.target}`,
      },
      ...prev,
    ].slice(0, 6));

    window.setTimeout(() => {
      setExecutedActions((prev) => ({ ...prev, [action.id]: "已下发" }));
      setActionLogs((prev) => [
        {
          id: `${action.id}-done-${Date.now()}`,
          time: formatShortTime(new Date()),
          status: "已下发",
          text: `${action.title} / ${action.target}`,
        },
        ...prev,
      ].slice(0, 6));
    }, 800);
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
                <span>终止时间</span>
                <div className={`time-display ${activeTimeField === "end" ? "active" : ""}`}>
                  <strong>{toDisplayValue(endInput)}</strong>
                  <button
                    type="button"
                    className="time-display__icon"
                    aria-label="选择终止时间"
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
        <aside className="side-panel">
          <div className="panel-frame">
            <div className="panel-head">
              <h2>干预建议</h2>
              <span>{selectedSummary?.siteName || "未选中路口"}</span>
            </div>
            <div className="intervention-list">
              {interventions.map((item) => (
                <article key={item.id} className="action-card">
                  <div className="action-card__title">
                    <strong>{item.title}</strong>
                    <b>{item.priority}</b>
                  </div>
                  <span>{item.target}</span>
                  <p>{item.reason}</p>
                  <div className="action-card__foot">
                    <small>{item.effectText}</small>
                    <button type="button" onClick={() => runAction(item)}>
                      {executedActions[item.id] || "执行"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>

          <div className="panel-frame">
            <div className="panel-head">
              <h2>执行状态</h2>
              <span>最近 6 条</span>
            </div>
            <div className="log-list">
              {actionLogs.length ? (
                actionLogs.map((item) => (
                  <article key={item.id} className="log-card">
                    <div>
                      <span>{item.time}</span>
                      <strong>{item.status}</strong>
                    </div>
                    <p>{item.text}</p>
                  </article>
                ))
              ) : (
                <div className="empty-card">暂无执行日志</div>
              )}
            </div>
          </div>
        </aside>

        <section className="center-column">
          <div className="map-frame">
            <div ref={mapContainerRef} className="map-canvas" />

            {popupOpen ? (
              <div className="detail-popup">
                <div className="detail-popup__head">
                  <div>
                    <span>{selectedSite}</span>
                    <strong>{selectedDirection}</strong>
                  </div>
                  <button type="button" onClick={() => setPopupOpen(false)}>
                    关闭
                  </button>
                </div>

                <div className="detail-popup__metrics">
                  <div>
                    <span>平均车流量</span>
                    <strong>{selectedSummary?.avgVehicle || 0}</strong>
                  </div>
                  <div>
                    <span>平均车速</span>
                    <strong>{selectedSummary?.avgSpeed || 0} km/h</strong>
                  </div>
                </div>

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

                <div className="detail-charts">
                  <div className="mini-chart">
                    <span>车流量</span>
                    <ResponsiveContainer width="100%" height={136}>
                      <BarChart data={popupAnalysis}>
                        <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                        <XAxis dataKey="bucket_start" tickFormatter={formatShortTime} tick={{ fill: "#8da6bb", fontSize: 10 }} />
                        <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} domain={popupVehicleDomain} />
                        <Tooltip labelFormatter={(value) => formatShortTime(value)} />
                        <Bar dataKey="vehicle_count" fill="#5ecdfc" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>

                  <div className="mini-chart">
                    <span>车速</span>
                    <ResponsiveContainer width="100%" height={136}>
                      <LineChart data={popupAnalysis}>
                        <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                        <XAxis dataKey="bucket_start" tickFormatter={formatShortTime} tick={{ fill: "#8da6bb", fontSize: 10 }} />
                        <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} domain={popupSpeedDomain} />
                        <Tooltip labelFormatter={(value) => formatShortTime(value)} />
                        <Line type="monotone" dataKey="avg_speed" stroke="#7bf4cf" strokeWidth={2.2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="wheel-block">
                  <span>方向车流分布</span>
                  <div className="wheel-layout">
                    <ResponsiveContainer width="46%" height={132}>
                      <PieChart>
                        <Pie data={directionPieData} dataKey="value" nameKey="name" innerRadius={26} outerRadius={50}>
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
          </div>

          <div className="effect-frame">
            <div className="panel-head">
              <h2>干预结果预测</h2>
              <span>地图下方附属预测区</span>
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
              <LineChart data={effectSeries}>
                <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: "#8da6bb", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} />
                <Tooltip />
                <Line type="monotone" dataKey="before" stroke="#ff7d7d" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="after" stroke="#6ef2c2" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
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
            <div className="prediction-plot">
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={predictionChartData} margin={{ top: 8, right: 10, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="predictionFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#5ecdfc" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#5ecdfc" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#8da6bb", fontSize: 10 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="history" stroke="#4f8cf7" strokeWidth={2} dot={false} />
                  <Area type="monotone" dataKey="predicted" stroke="#8f9dff" fill="url(#predictionFill)" strokeWidth={2.3} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="prediction-legend">
              <span><i className="legend-line history" /> 当前已有数据</span>
              <span><i className="legend-line forecast" /> 未来 1 小时预测</span>
            </div>

            <div className="prediction-grid">
              <div>
                <span>峰值</span>
                <strong>{predictionStats.peak}</strong>
              </div>
              <div>
                <span>低值</span>
                <strong>{predictionStats.low}</strong>
              </div>
              <div>
                <span>平均值</span>
                <strong>{predictionStats.avg}</strong>
              </div>
              <div>
                <span>天气</span>
                <strong>{weather}</strong>
              </div>
              <div>
                <span>误差百分比</span>
                <strong>{predictionCompare?.mape ? `${predictionCompare.mape}%` : "--"}</strong>
              </div>
              <div>
                <span>误差 MAE</span>
                <strong>{predictionCompare?.mae ?? "--"}</strong>
              </div>
            </div>

            <div className="reason-list">
              {reasons.map((item) => (
                <div key={item} className="reason-tag">
                  {item}
                </div>
              ))}
            </div>

            <div className="model-card">
              <span>模型信息</span>
              <strong>{predictionMeta?.model_type || "unavailable"}</strong>
              <p>当前预测基于历史交通流、速度、天气与时序特征生成未来一小时车流结果，默认按 10 分钟粒度展示。</p>
              <small>
                测试集 MAE：{predictionMeta?.test_mae ?? "--"} · RMSE：{predictionMeta?.test_rmse ?? "--"}
              </small>
            </div>
          </div>

          <div className="panel-frame">
            <div className="panel-head">
              <h2>重点监测</h2>
              <span>当前时间段</span>
            </div>
            <div className="monitor-mini-list">
              {monitorRows.slice(0, 5).map((item) => (
                <button
                  key={`${item.siteName}-${item.direction}`}
                  type="button"
                  className="mini-monitor"
                  onClick={() => {
                    setSelectedSite(item.siteName);
                    setSiteDirections((prev) => ({ ...prev, [item.siteName]: item.direction }));
                  }}
                >
                  <div>
                    <strong>{item.siteName}</strong>
                    <span>{item.direction}</span>
                  </div>
                  <div>
                    <em style={{ color: RISK_COLORS[item.risk] }}>{riskLabel(item.risk)}</em>
                    <span>{item.avgVehicle}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </aside>
      </section>

      {error ? <div className="status-toast status-toast--error">{error}</div> : null}
      {!error && loading ? <div className="status-toast">数据同步中...</div> : null}
    </div>
  );
}

export default App;
