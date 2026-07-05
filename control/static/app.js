let state = null;
let selectedMac = null;
let filter = "all";
let mapZoom = 1;
let pinchStartDistance = null;
let pinchStartZoom = 1;

const MAP_PADDING = 0.08;
const MIN_ZOOM = 1;
const MAX_ZOOM = 3;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(errorMessage(body.detail || response.statusText));
  }
  return response.json();
}

function lanterns() {
  return state?.lanterns || [];
}

function selectedLantern() {
  return lanterns().find((lantern) => lantern.mac === selectedMac) || lanterns()[0] || null;
}

function statusText(lantern) {
  if (lantern.status === "missing") return "missing";
  if (lantern.position === "Missing") return "needs position";
  return "alive";
}

function cssStatus(lantern) {
  if (lantern.status === "missing") return "missing";
  if (lantern.position === "Missing") return "unpositioned";
  return "";
}

function render() {
  if (!state) return;
  if (!selectedMac && lanterns().length) selectedMac = lanterns()[0].mac;

  $("#connection-status").textContent = state.conductor.connected ? "mock connected" : "disconnected";
  $("#field-count").textContent = `${state.summary.alive} / ${state.summary.total}`;
  $("#show-name").textContent = state.recipe.pattern;
  $("#attention-count").textContent = `${state.summary.attention} lights`;
  $("#sync-status").textContent = `sync ${state.conductor.sync}`;
  $("#table-sync-status").textContent = `sync ${state.conductor.sync}`;
  $("#brightness").value = state.recipe.brightness;
  $("#brightness-value").textContent = state.recipe.brightness;

  renderPatternControls();
  renderMap();
  renderRows();
  renderDetail();
  renderEvents();
}

function renderPatternControls() {
  $$("#pattern-picker button").forEach((button) => {
    button.classList.toggle("active", button.dataset.pattern === state.recipe.pattern);
  });
  const hue = String(state.recipe.params?.hue ?? "40");
  $$("#hue-picker button").forEach((button) => {
    button.classList.toggle("active", button.dataset.hue === hue);
  });
}

function renderMap() {
  const map = $("#map-content");
  $$(".node").forEach((node) => node.remove());
  lanterns().forEach((lantern, index) => {
    const button = document.createElement("button");
    button.className = `node ${cssStatus(lantern)}`;
    if (lantern.mac === selectedMac) button.classList.add("selected");
    button.dataset.mac = lantern.mac;
    button.type = "button";
    button.ariaLabel = lantern.label;
    const fallbackX = 0.18 + (index % 7) * 0.11;
    const fallbackY = 0.28 + Math.floor(index / 7) * 0.18;
    button.style.left = `${mapCoord(lantern.x ?? fallbackX) * 100}%`;
    button.style.top = `${mapCoord(lantern.y ?? fallbackY) * 100}%`;
    button.addEventListener("click", () => selectLantern(lantern.mac));
    map.appendChild(button);
  });
  renderMapZoom();
}

function renderRows() {
  const rows = lanterns().filter((lantern) => {
    if (filter === "attention") return lantern.attention !== "None";
    if (filter === "missing") return lantern.status === "missing";
    if (filter === "unpositioned") return lantern.position === "Missing";
    return true;
  });

  $("#lantern-rows").innerHTML = rows.map((lantern) => {
    const dotClass = lantern.status === "missing" ? "bad" : lantern.position === "Missing" ? "warn" : "";
    const attentionClass = lantern.attention === "None" ? "" : lantern.status === "missing" ? "bad" : "warn";
    return `<tr data-mac="${lantern.mac}" class="${lantern.mac === selectedMac ? "selected" : ""}">
      <td><strong>${escapeHtml(lantern.label)}</strong><br><span class="mono">${escapeHtml(lantern.mac)}</span></td>
      <td><span class="status"><span class="dot ${dotClass}"></span>${statusText(lantern)}</span></td>
      <td class="${lantern.status === "missing" ? "bad" : "ok"}">${escapeHtml(lantern.last_seen_label)}</td>
      <td class="${lantern.position === "Missing" ? "warn" : ""}">${escapeHtml(lantern.position)}</td>
      <td class="${attentionClass}">${escapeHtml(lantern.attention)}</td>
    </tr>`;
  }).join("");

  $$("#lantern-rows tr").forEach((row) => {
    row.addEventListener("click", () => selectLantern(row.dataset.mac));
  });
}

function renderDetail() {
  const lantern = selectedLantern();
  if (!lantern) return;
  const isOk = lantern.status === "alive" && lantern.position !== "Missing";
  $("#detail-title").textContent = `${lantern.label} is ${statusText(lantern)}`;
  $("#detail-title").className = isOk ? "" : "warn";
  $("#detail-summary").textContent = detailSummary(lantern);
  $("#detail-tech").innerHTML = [
    `MAC ${escapeHtml(lantern.mac)} · x=${fmt(lantern.x)} y=${fmt(lantern.y)} · status=${escapeHtml(lantern.status)}`,
    `recipe=${escapeHtml(state.recipe.pattern)} bri=${state.recipe.brightness} · seq=${state.conductor.seq}`,
    `power E=${fmt(lantern.power.wh)}Wh avg=${fmt(lantern.power.avg_w)}W · last report=${escapeHtml(lantern.power.last_report_label || "none")}`,
  ].join("<br>");
}

function detailSummary(lantern) {
  if (lantern.status === "missing") {
    return `Last seen ${lantern.last_seen_label}. Use Identify after it returns, or Replace if this lantern is physically gone.`;
  }
  if (lantern.position === "Missing") {
    return `Last seen ${lantern.last_seen_label}. It is alive but has no table position yet.`;
  }
  return `Last seen ${lantern.last_seen_label}. Position is set. No action needed.`;
}

function renderEvents() {
  $("#event-log").innerHTML = (state.events || []).map((event) => {
    const time = new Date(event.ts * 1000).toLocaleTimeString();
    return `<div><span class="mono">${time}</span> ${escapeHtml(event.message)}</div>`;
  }).join("");
}

function selectLantern(mac) {
  selectedMac = mac;
  renderMap();
  renderRows();
  renderDetail();
}

function toast(message, danger = false) {
  const node = $("#toast");
  node.textContent = message;
  node.style.borderColor = danger ? "rgba(255,93,82,.55)" : "rgba(84,214,122,.42)";
  node.style.color = danger ? "var(--alert)" : "var(--live)";
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 1800);
}

function mapCoord(value) {
  return MAP_PADDING + value * (1 - MAP_PADDING * 2);
}

function setMapZoom(nextZoom) {
  mapZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
  renderMapZoom();
}

function renderMapZoom() {
  const content = $("#map-content");
  if (!content) return;
  content.style.transform = `scale(${mapZoom})`;
  const reset = $('[data-zoom="reset"]');
  if (reset) reset.textContent = `${mapZoom.toFixed(mapZoom === 1 ? 0 : 1)}x`;
}

function touchDistance(touches) {
  const dx = touches[0].clientX - touches[1].clientX;
  const dy = touches[0].clientY - touches[1].clientY;
  return Math.hypot(dx, dy);
}

async function refresh() {
  state = await api("/api/state");
  render();
}

async function runAction(action) {
  const lantern = selectedLantern();
  if (!lantern && ["identify", "move", "replace", "forget"].includes(action)) return;
  try {
    if (action === "details") {
      $("#detail-sheet").classList.toggle("show-details");
      return;
    }
    if (action === "identify") {
      const ack = await api(`/api/lanterns/${encodeURIComponent(lantern.mac)}/identify`, { method: "POST" });
      toast(ack.message);
      return;
    }
    if (action === "move") {
      const x = Number(prompt("x position, 0.0 to 1.0", fmt(lantern.x)));
      const y = Number(prompt("y position, 0.0 to 1.0", fmt(lantern.y)));
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const ack = await api(`/api/lanterns/${encodeURIComponent(lantern.mac)}/assign`, {
        method: "POST",
        body: JSON.stringify({ x, y }),
      });
      toast(ack.message);
      await refresh();
      return;
    }
    if (action === "replace") {
      toast("replace flow will pick a spare lantern next", true);
      return;
    }
    if (action === "forget") {
      if (!confirm(`Forget position for ${lantern.label}?`)) return;
      const ack = await api(`/api/lanterns/${encodeURIComponent(lantern.mac)}/forget`, { method: "POST" });
      toast(ack.message);
      await refresh();
      return;
    }
    if (action === "broadcast") {
      const pattern = $("#pattern-picker button.active")?.dataset.pattern || state.recipe.pattern;
      const hue = Number($("#hue-picker button.active")?.dataset.hue || state.recipe.params?.hue || 40);
      const brightness = Number($("#brightness").value);
      const ack = await api("/api/show/recipe", {
        method: "POST",
        body: JSON.stringify({ pattern, brightness, params: { hue, saturation: 100 } }),
      });
      toast(ack.message);
      await refresh();
      return;
    }
    if (action === "blackout") {
      if (!confirm("Broadcast blackout brightness 0?")) return;
      const ack = await api("/api/show/blackout", { method: "POST" });
      toast(ack.message, true);
      await refresh();
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function connectWebSocket() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${window.location.host}/ws`);
  ws.addEventListener("open", () => {
    $("#connection-status").textContent = "mock connected";
  });
  ws.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.state) {
      state = data.state;
      render();
    }
  });
  ws.addEventListener("close", () => {
    $("#connection-status").textContent = "reconnecting";
    window.setTimeout(connectWebSocket, 1500);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function errorMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const location = Array.isArray(item.loc) ? item.loc.join(".") : "request";
      return `${location}: ${item.msg || "invalid value"}`;
    }).join("; ");
  }
  if (detail && typeof detail === "object") {
    return detail.message || detail.error || JSON.stringify(detail);
  }
  return String(detail);
}

function fmt(value) {
  return value === null || value === undefined ? "-" : Number(value).toFixed(2);
}

$$(".tabs button").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tabs button").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    $$(".view").forEach((view) => view.classList.remove("active"));
    $(`#view-${tab.dataset.view}`).classList.add("active");
  });
});

$$(".filters .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $$(".filters .chip").forEach((item) => item.classList.remove("active"));
    chip.classList.add("active");
    filter = chip.dataset.filter;
    renderRows();
  });
});

document.addEventListener("click", (event) => {
  const action = event.target.dataset.action;
  if (action) runAction(action);
  const zoom = event.target.dataset.zoom;
  if (zoom === "in") setMapZoom(mapZoom + 0.25);
  if (zoom === "out") setMapZoom(mapZoom - 0.25);
  if (zoom === "reset") setMapZoom(1);
});

$("#brightness").addEventListener("input", (event) => {
  $("#brightness-value").textContent = event.target.value;
});

$("#pattern-picker").addEventListener("click", (event) => {
  if (event.target.dataset.pattern) {
    $$("#pattern-picker button").forEach((item) => item.classList.remove("active"));
    event.target.classList.add("active");
  }
});

$("#hue-picker").addEventListener("click", (event) => {
  if (event.target.dataset.hue) {
    $$("#hue-picker button").forEach((item) => item.classList.remove("active"));
    event.target.classList.add("active");
  }
});

$("#map").addEventListener("wheel", (event) => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  setMapZoom(mapZoom + (event.deltaY < 0 ? 0.12 : -0.12));
}, { passive: false });

$("#map").addEventListener("touchstart", (event) => {
  if (event.touches.length !== 2) return;
  pinchStartDistance = touchDistance(event.touches);
  pinchStartZoom = mapZoom;
}, { passive: true });

$("#map").addEventListener("touchmove", (event) => {
  if (event.touches.length !== 2 || !pinchStartDistance) return;
  event.preventDefault();
  setMapZoom(pinchStartZoom * (touchDistance(event.touches) / pinchStartDistance));
}, { passive: false });

$("#map").addEventListener("touchend", (event) => {
  if (event.touches.length < 2) pinchStartDistance = null;
}, { passive: true });

refresh().then(connectWebSocket).catch((error) => toast(error.message, true));
