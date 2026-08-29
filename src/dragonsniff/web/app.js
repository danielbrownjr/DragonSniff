"use strict";

const endpoints = ["/api/v2/info", "/api/v2/state", "/api/v2/health"];
const notice = document.querySelector("#notice");
const targetInput = document.querySelector("#target");
const endpointPayloads = new Map();
const payloadTools = window.DragonSniffPayload;
let requestInFlight = false;

function text(selector, value) {
  document.querySelector(selector).textContent = String(value);
}

function pretty(value, fallback = "No payload") {
  return value === undefined ? fallback : JSON.stringify(value, null, 2);
}

async function localRequest(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function renderEndpoint(path, result = {}) {
  const card = document.querySelector(`[data-endpoint="${path}"]`);
  endpointPayloads.set(path, result);
  const endpointState = card.querySelector(".endpoint-state");
  endpointState.textContent = result.state || "not requested";
  endpointState.dataset.status = result.state || "not_requested";
  const status = result.status === null || result.status === undefined ? "no HTTP status" : `HTTP ${result.status}`;
  const elapsed = result.elapsed_ms === undefined ? "no timing yet" : `${result.elapsed_ms.toFixed(1)} ms`;
  card.querySelector(".timing").textContent = `${status} / ${elapsed}${result.error ? ` / ${result.error}` : ""}`;
  card.querySelector(".parsed").textContent = pretty(result.parsed);
  card.querySelector(".raw").textContent = result.raw_payload || "No payload";
  card.querySelectorAll(".copy-button").forEach((button) => {
    button.disabled = payloadTools.payloadText(result, button.dataset.copyView) === null;
  });
}

function copyFeedback(button, message, failed = false) {
  const feedback = button.parentElement.querySelector(".copy-feedback");
  feedback.textContent = message;
  feedback.classList.toggle("is-error", failed);
  window.setTimeout(() => {
    if (feedback.textContent === message) feedback.textContent = "";
  }, 1800);
}

async function copyPayload(button) {
  const card = button.closest("[data-endpoint]");
  const value = payloadTools.payloadText(
    endpointPayloads.get(card.dataset.endpoint),
    button.dataset.copyView,
  );
  if (value === null) {
    copyFeedback(button, "Nothing to copy", true);
    return;
  }
  try {
    if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
    await navigator.clipboard.writeText(value);
    copyFeedback(button, "Copied");
  } catch (error) {
    copyFeedback(button, "Copy failed", true);
  }
}

function renderLimits(limits) {
  const list = document.querySelector("#limits");
  list.replaceChildren();
  Object.entries(limits || {}).forEach(([name, value]) => {
    const item = document.createElement("div");
    item.className = "limit";
    const term = document.createElement("dt");
    term.textContent = name.replaceAll("_", " ");
    const detail = document.createElement("dd");
    detail.textContent = String(value);
    item.append(term, detail);
    list.append(item);
  });
}

function renderTimeline(records) {
  const timeline = document.querySelector("#timeline");
  timeline.replaceChildren();
  [...(records || [])].reverse().slice(0, 50).forEach((record) => {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "timeline-heading";
    const kind = document.createElement("strong");
    kind.textContent = record.kind;
    const time = document.createElement("time");
    time.textContent = record.timestamp;
    heading.append(kind, time);
    const body = document.createElement("pre");
    const details = {...record};
    delete details.kind;
    delete details.timestamp;
    body.textContent = JSON.stringify(details, null, 2);
    item.append(heading, body);
    timeline.append(item);
  });
}

function render(snapshot) {
  const sse = snapshot.sse || {};
  const sseTiming = sse.details?.elapsed_ms === undefined ? "" : ` / ${sse.details.elapsed_ms.toFixed(1)} ms`;
  text("#sessionBadge", snapshot.session_state || "idle");
  text("#targetValue", snapshot.target || "not connected");
  text("#sseState", sse.state || "not connected");
  document.querySelector("#sseState").dataset.status = sse.state || "not_connected";
  text("#sseDetail", `${sse.state || "not connected"}${sseTiming}`);
  text("#eventCount", sse.events || 0);
  text("#recordCount", `${snapshot.recorder?.records || 0} / ${snapshot.recorder?.max_records || 0}`);
  endpoints.forEach((path) => renderEndpoint(path, snapshot.http?.[path]));
  renderLimits(snapshot.limits);
  renderTimeline(snapshot.recent_records);
  const event = sse.last_event;
  text("#eventParsed", event ? pretty(event.parsed, event.data || "No parsed data") : "No event");
  text("#eventRaw", event?.raw_payload || "No event");
  const active = !["idle", "stopped"].includes(snapshot.session_state);
  const streamActive = ["connecting", "open"].includes(sse.state);
  document.querySelector("#refreshButton").disabled = !active;
  document.querySelector("#reconnectButton").disabled = !active;
  document.querySelector("#stopEventsButton").disabled = !streamActive;
  document.querySelector("#stopButton").disabled = !active;
}

async function update() {
  if (requestInFlight) return;
  requestInFlight = true;
  try {
    render(await localRequest("/local/v1/session"));
  } catch (error) {
    notice.textContent = `Local service error: ${error.message}`;
  } finally {
    requestInFlight = false;
  }
}

async function act(path, body = {}) {
  notice.textContent = "Working...";
  try {
    const snapshot = await localRequest(path, {method: "POST", body: JSON.stringify(body)});
    render(snapshot);
    notice.textContent = "Request accepted. Live status will update below.";
  } catch (error) {
    notice.textContent = error.message;
  }
}

if (window.location.protocol === "file:") {
  document.querySelector("#fileWarning").hidden = false;
  document.querySelector("main").hidden = true;
  document.querySelector("footer").hidden = true;
  document.querySelector("#sessionBadge").textContent = "service required";
} else {
  document.querySelector("#connectForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const target = targetInput.value.trim();
    localStorage.setItem("dragonsniff.target", target);
    act("/local/v1/session/start", {target});
  });
  document.querySelector("#refreshButton").addEventListener("click", () => act("/local/v1/session/refresh"));
  document.querySelector("#reconnectButton").addEventListener("click", () => act("/local/v1/session/reconnect-events"));
  document.querySelector("#stopEventsButton").addEventListener("click", () => act("/local/v1/session/stop-events"));
  document.querySelector("#stopButton").addEventListener("click", () => act("/local/v1/session/stop"));
  document.querySelectorAll(".copy-button").forEach((button) => {
    button.addEventListener("click", () => copyPayload(button));
  });

  targetInput.value = localStorage.getItem("dragonsniff.target") || "";
  update();
  setInterval(update, 1000);
}
