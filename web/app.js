"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);

const clock = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
const formatTime = (epoch) => clock.format(new Date(epoch * 1000));
const minutesOf = (seconds) => Math.max(1, Math.round(seconds / 60));

const app = {
  stations: [],
  byLabel: new Map(),
  lines: [],
  closed: new Set(),
  status: null,
  statusAt: 0,
  hasResult: false,
};

function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

function svg(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  if (parent) parent.append(node);
  return node;
}

async function getJSON(url) {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

/* ---------- map ---------- */

const LAT_TOP = 40.95;
const LON_LEFT = -74.3;
const LON_SCALE = Math.cos((40.73 * Math.PI) / 180);
const UNITS = 1000;

const project = ([lat, lon]) => [(lon - LON_LEFT) * LON_SCALE * UNITS, (LAT_TOP - lat) * UNITS];
const pathOf = (points) =>
  points.map((point, i) => (i ? "L" : "M") + project(point).map((n) => n.toFixed(1)).join(" ")).join("");

const view = { root: $("map"), box: { x: 0, y: 0, w: 400, h: 400 }, home: [] };

function applyView() {
  const { x, y, w, h: height } = view.box;
  view.root.setAttribute("viewBox", `${x} ${y} ${w} ${height}`);
}

function fitPoints(points, pad = 0.14) {
  if (!points.length) return;
  const projected = points.map(project);
  const xs = projected.map((p) => p[0]);
  const ys = projected.map((p) => p[1]);
  const rect = view.root.getBoundingClientRect();
  const aspect = rect.width / Math.max(rect.height, 1);
  let w = Math.max(Math.max(...xs) - Math.min(...xs), 18) * (1 + pad * 2);
  let height = Math.max(Math.max(...ys) - Math.min(...ys), 18) * (1 + pad * 2);
  if (w / height > aspect) height = w / aspect;
  else w = height * aspect;
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
  const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  view.box = { x: cx - w / 2, y: cy - height / 2, w, h: height };
  applyView();
}

function zoomAt(clientX, clientY, factor) {
  const rect = view.root.getBoundingClientRect();
  const fx = (clientX - rect.left) / rect.width;
  const fy = (clientY - rect.top) / rect.height;
  const box = view.box;
  const px = box.x + fx * box.w;
  const py = box.y + fy * box.h;
  const w = Math.min(Math.max(box.w * factor, 8), 900);
  const height = (w * rect.height) / rect.width;
  view.box = { x: px - fx * w, y: py - fy * height, w, h: height };
  applyView();
}

function buildMap(data) {
  const root = view.root;
  root.replaceChildren();
  view.network = svg("g", { class: "network" }, root);
  view.lines = new Map();
  for (const route of data.routes) {
    const group = svg("g", {}, view.network);
    for (const line of route.lines) {
      svg("path", { d: pathOf(line), stroke: "#" + route.color, "stroke-width": 2.6 }, group);
    }
    view.lines.set(route.key, group);
  }
  const dots = svg("g", {}, root);
  for (const [, lat, lon] of data.stations) {
    const [x, y] = project([lat, lon]);
    svg("path", { class: "dot", d: `M${x.toFixed(1)} ${y.toFixed(1)}l0 0`, "stroke-width": 3 }, dots);
  }
  view.ghost = svg("g", { class: "ghost" }, root);
  view.route = svg("g", { class: "route" }, root);
  view.markers = svg("g", {}, root);

  root.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1 / 1.2 : 1.2);
  }, { passive: false });

  let drag = null;
  root.addEventListener("pointerdown", (event) => {
    drag = { x: event.clientX, y: event.clientY, box: { ...view.box } };
    root.setPointerCapture(event.pointerId);
    root.classList.add("dragging");
  });
  root.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const rect = root.getBoundingClientRect();
    view.box.x = drag.box.x - ((event.clientX - drag.x) * drag.box.w) / rect.width;
    view.box.y = drag.box.y - ((event.clientY - drag.y) * drag.box.h) / rect.height;
    applyView();
  });
  const release = () => {
    drag = null;
    root.classList.remove("dragging");
  };
  root.addEventListener("pointerup", release);
  root.addEventListener("pointercancel", release);

  const center = () => {
    const rect = root.getBoundingClientRect();
    return [rect.left + rect.width / 2, rect.top + rect.height / 2];
  };
  $("zin").addEventListener("click", () => zoomAt(...center(), 1 / 1.4));
  $("zout").addEventListener("click", () => zoomAt(...center(), 1.4));
  $("zfit").addEventListener("click", () => fitPoints(view.home, 0.04));
  window.addEventListener("resize", () => {
    const rect = root.getBoundingClientRect();
    view.box.h = (view.box.w * rect.height) / rect.width;
    applyView();
  });
}

function paintClosedLines() {
  const any = app.closed.size > 0;
  view.network.classList.toggle("dim", any);
  for (const [key, group] of view.lines) {
    const shut = app.closed.has(key);
    for (const path of group.children) path.classList.toggle("shut", shut);
    if (shut) view.network.append(group);
  }
}

function drawSteps(journey, layer, ghost) {
  for (const step of journey.steps) {
    if (step.type === "ride") {
      const d = pathOf(step.path);
      if (ghost) {
        svg("path", { d }, layer);
        continue;
      }
      svg("path", { d, stroke: "#fff", "stroke-width": 10 }, layer);
      svg("path", { d, stroke: "#" + step.color, "stroke-width": 6 }, layer);
    } else if (!ghost) {
      svg("path", { d: pathOf(step.path), stroke: "#1b2a44", "stroke-width": 3, "stroke-dasharray": "3 6" }, layer);
    }
  }
}

function marker(point, color) {
  const [x, y] = project(point);
  const d = `M${x.toFixed(1)} ${y.toFixed(1)}l0 0`;
  svg("path", { class: "marker", d, stroke: color, "stroke-width": 20, "stroke-linecap": "round" }, view.markers);
  svg("path", { class: "marker", d, stroke: "#fff", "stroke-width": 9, "stroke-linecap": "round" }, view.markers);
}

function drawJourneys(base, changed, data, refit) {
  view.ghost.replaceChildren();
  view.route.replaceChildren();
  view.markers.replaceChildren();
  if (!base) return;
  const shown = changed || base;
  if (changed) drawSteps(base, view.ghost, true);
  drawSteps(shown, view.route, false);
  const origin = [data.origin.lat, data.origin.lon];
  const target = [data.destination.lat, data.destination.lon];
  marker(origin, "#138a45");
  marker(target, "#0b1f3f");
  if (refit) {
    const points = [origin, target];
    for (const journey of [base, changed]) {
      if (journey) for (const step of journey.steps) points.push(...step.path);
    }
    fitPoints(points);
  }
}

/* ---------- stations and lines ---------- */

function buildStations(stations) {
  app.stations = stations;
  const list = $("station-list");
  list.replaceChildren();
  for (const station of stations) {
    app.byLabel.set(station.label, station);
    list.append(h("option", { value: station.label }));
  }
}

function resolveStation(text) {
  const raw = text.trim();
  if (!raw) return null;
  const exact = app.byLabel.get(raw);
  if (exact) return exact;
  const query = raw.toLowerCase();
  const named = app.stations.filter((s) => s.name.toLowerCase().split(" / ").includes(query));
  if (named.length === 1) return named[0];
  const partial = app.stations.filter((s) => s.label.toLowerCase().includes(query));
  return partial.length === 1 ? partial[0] : null;
}

function lineLabel(key) {
  const line = app.lines.find((item) => item.key === key);
  return line ? line.label : key;
}

function buildChips(lines) {
  app.lines = lines;
  const box = $("chips");
  box.replaceChildren();
  for (const line of lines) {
    const note = line.alert === "suspended" ? " (service suspended in part)" : line.alert ? " (active service alert)" : "";
    const chip = h(
      "button",
      {
        type: "button",
        class: "chip" + (line.label.length > 1 ? " wide" : ""),
        "aria-pressed": "false",
        title: line.name + note,
        style: `background:#${line.color};color:#${line.text_color}`,
        onclick: () => toggleLine(line.key),
      },
      line.label,
      line.alert ? h("span", { class: "warn warn-" + line.alert }) : null,
    );
    chip.dataset.key = line.key;
    box.append(chip);
  }
}

function syncChips() {
  for (const chip of $("chips").children) {
    chip.setAttribute("aria-pressed", app.closed.has(chip.dataset.key) ? "true" : "false");
  }
  $("clear").hidden = app.closed.size === 0;
  paintClosedLines();
}

function toggleLine(key) {
  if (app.closed.has(key)) app.closed.delete(key);
  else app.closed.add(key);
  syncChips();
  saveHash();
  if (selection()) run({ refit: false });
}

/* ---------- results ---------- */

function waitText(seconds) {
  return seconds < 45 ? "train due now" : `next train in ${Math.round(seconds / 60)} min`;
}

function bullet(step) {
  return h(
    "span",
    {
      class: "bullet" + (step.line.length > 1 ? " wide" : ""),
      style: `background:#${step.color};color:#${step.text_color}`,
      title: step.name,
    },
    step.line,
  );
}

function stepItem(step) {
  if (step.type === "ride") {
    const stops = `${step.stops} stop${step.stops === 1 ? "" : "s"}`;
    return h(
      "li",
      { class: "step" },
      bullet(step),
      h(
        "div",
        {},
        h("div", { class: "what" }, `${step.from} to ${step.to}`),
        h(
          "div",
          { class: "sub" },
          [waitText(step.wait), step.toward ? `toward ${step.toward}` : null, stops, formatTime(step.depart)]
            .filter(Boolean)
            .join(" · "),
          step.live ? h("span", { class: "live-tag" }, " · live") : null,
        ),
      ),
    );
  }
  const minutes = minutesOf(step.seconds);
  if (step.street) {
    return h(
      "li",
      { class: "step" },
      h("span", { class: "bullet walk", title: "Walk" }, "W"),
      h("div", {}, h("div", { class: "what" }, `Walk from ${step.from} to ${step.to}`), h("div", { class: "sub" }, `about ${minutes} min outside`)),
    );
  }
  const where = step.from === step.to ? `Transfer at ${step.from}` : `Transfer from ${step.from} to ${step.to}`;
  return h(
    "li",
    { class: "step" },
    h("span", { class: "bullet transfer", title: "Transfer" }, "T"),
    h("div", {}, h("div", { class: "what" }, where), h("div", { class: "sub" }, `${minutes} min between platforms`)),
  );
}

function alertsBlock(alerts) {
  if (!alerts.length) return null;
  return h(
    "details",
    { class: "alerts" },
    h("summary", {}, `Service alerts on these lines (${alerts.length})`),
    h(
      "ul",
      {},
      alerts.map((alert) =>
        h("li", {}, h("span", { class: "badge " + alert.severity }, `${alert.line} · ${alert.kind || "Alert"}`), alert.header),
      ),
    ),
  );
}

function strip(data, journey, tone) {
  if (tone === "closed") {
    const delta = data.delta_minutes;
    let text = `Recomputed in ${data.elapsed_ms} ms, same travel time`;
    if (delta > 0) text = `Recomputed in ${data.elapsed_ms} ms, ${delta} min longer`;
    if (delta < 0) text = `Recomputed in ${data.elapsed_ms} ms, ${-delta} min faster`;
    return h("div", { class: "strip " + (delta === 0 ? "same" : "closed") }, text);
  }
  const age = data.status.age;
  if (journey.source === "live") return h("div", { class: "strip live" }, `Built from a feed snapshot ${age} seconds old`);
  if (journey.source === "mixed") return h("div", { class: "strip schedule" }, "Part live, part scheduled: some lines are missing from the feed");
  return h("div", { class: "strip schedule" }, "Using the published timetable, no live data for this trip");
}

function journeyCard(data, journey, tone) {
  const first = (name) => name.split(" / ")[0];
  const title =
    tone === "closed"
      ? `The same trip with ${data.closed_lines.map(lineLabel).join(", ")} closed`
      : `${first(data.origin.name)} to ${first(data.destination.name)}`;
  return h(
    "article",
    { class: "card " + tone },
    h("h2", {}, title),
    h(
      "div",
      { class: "headline" },
      h("div", { class: "minutes" }, `${journey.minutes} min`),
      h(
        "div",
        { class: "meta" },
        `${journey.transfers} transfer${journey.transfers === 1 ? "" : "s"}, ${journey.stops} stops` +
          (journey.walk_minutes ? `, ${journey.walk_minutes} min walking` : ""),
        h("br"),
        `${formatTime(journey.depart)} to ${formatTime(journey.arrive)}`,
      ),
    ),
    h("ol", { class: "steps" }, journey.steps.map(stepItem)),
    strip(data, journey, tone),
    alertsBlock(journey.alerts),
    h("p", { class: "foot" }, `Searched ${journey.search.settled.toLocaleString()} platform states`),
  );
}

function showMessage(text) {
  const box = $("message");
  box.textContent = text;
  box.hidden = !text;
}

function render(data, refit) {
  const box = $("results");
  box.replaceChildren();
  showMessage("");
  if (!data.base) {
    box.append(h("article", { class: "card" }, h("h2", {}, "No route found"), h("p", { class: "meta" }, "These stations are not connected on the current network. The Staten Island Railway, for one, does not join the subway.")));
    drawJourneys(null, null, data, false);
    return;
  }
  box.append(journeyCard(data, data.base, "base"));
  if (data.closed_lines.length) {
    if (data.closed) {
      box.append(journeyCard(data, data.closed, "closed"));
    } else {
      const names = data.closed_lines.map(lineLabel).join(", ");
      box.append(
        h("article", { class: "card closed" }, h("h2", {}, `The same trip with ${names} closed`), h("p", { class: "meta" }, "No route exists without those lines, even with walking between stations.")),
      );
    }
  }
  drawJourneys(data.base, data.closed, data, refit);
}

function selection() {
  const from = resolveStation($("from").value);
  const to = resolveStation($("to").value);
  return from && to ? { from, to } : null;
}

async function run({ refit }) {
  const picked = selection();
  if (!picked) {
    showMessage("Choose both stations from the suggestions list. If a name is shared by several stations, pick the one with your lines.");
    return;
  }
  if (picked.from.id === picked.to.id) {
    showMessage("Pick two different stations.");
    return;
  }
  const params = new URLSearchParams({ from: picked.from.id, to: picked.to.id });
  if (app.closed.size) params.set("closed", [...app.closed].join(","));
  if ($("time").value) params.set("time", $("time").value);
  $("go").disabled = true;
  try {
    render(await getJSON("/api/route?" + params), refit);
    app.hasResult = true;
    saveHash();
  } catch (error) {
    showMessage(error.message);
  } finally {
    $("go").disabled = false;
  }
}

/* ---------- status, hash, boot ---------- */

async function pollStatus() {
  try {
    app.status = await getJSON("/api/status");
    app.statusAt = Date.now();
  } catch (error) {
    app.status = null;
  }
  paintStatus();
}

function paintStatus() {
  const pill = $("status");
  const status = app.status;
  if (!status) {
    pill.className = "pill pill-wait";
    pill.textContent = "Server not reachable";
  } else if (status.mode === "live") {
    const age = status.age + Math.floor((Date.now() - app.statusAt) / 1000);
    const feeds = Object.values(status.feeds);
    pill.className = "pill pill-live";
    pill.textContent = `LIVE · feed ${age} s old · ${feeds.filter((f) => f.ok).length}/${feeds.length} feeds`;
  } else {
    pill.className = "pill pill-schedule";
    pill.textContent = status.live_enabled ? "SCHEDULE ONLY · live feeds unreachable" : "SCHEDULE ONLY · started offline";
  }
}

function saveHash() {
  const picked = selection();
  const parts = [];
  if (picked) parts.push("from=" + encodeURIComponent(picked.from.id), "to=" + encodeURIComponent(picked.to.id));
  if (app.closed.size) parts.push("closed=" + [...app.closed].join(","));
  if ($("time").value) parts.push("time=" + $("time").value);
  history.replaceState(null, "", parts.length ? "#" + parts.join("&") : location.pathname);
}

function restoreHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const byId = new Map(app.stations.map((s) => [s.id, s]));
  if (byId.has(params.get("from"))) $("from").value = byId.get(params.get("from")).label;
  if (byId.has(params.get("to"))) $("to").value = byId.get(params.get("to")).label;
  for (const key of (params.get("closed") || "").split(",")) {
    if (app.lines.some((line) => line.key === key)) app.closed.add(key);
  }
  if (params.get("time")) $("time").value = params.get("time");
  syncChips();
  if (selection()) run({ refit: true });
}

async function boot() {
  const [stations, lines, mapData] = await Promise.all([getJSON("/api/stations"), getJSON("/api/lines"), getJSON("/api/map")]);
  buildStations(stations);
  buildChips(lines);
  buildMap(mapData);
  view.home = stations.filter((s) => !(s.routes.length === 1 && s.routes[0] === "SIR")).map((s) => [s.lat, s.lon]);
  fitPoints(view.home, 0.04);

  $("plan").addEventListener("submit", (event) => {
    event.preventDefault();
    run({ refit: true });
  });
  $("swap").addEventListener("click", () => {
    const from = $("from").value;
    $("from").value = $("to").value;
    $("to").value = from;
    if (selection()) run({ refit: true });
  });
  $("clear").addEventListener("click", () => {
    app.closed.clear();
    syncChips();
    saveHash();
    if (selection()) run({ refit: false });
  });

  restoreHash();
  pollStatus();
  setInterval(pollStatus, 10000);
  setInterval(paintStatus, 1000);
  setInterval(() => {
    if (app.hasResult && !$("time").value && selection()) run({ refit: false });
  }, 30000);
}

boot().catch((error) => {
  showMessage("Could not load the network: " + error.message);
});
