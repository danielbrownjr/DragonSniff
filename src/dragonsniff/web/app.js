"use strict";

const endpoints = ["/api/v2/info", "/api/v2/state", "/api/v2/health"];
const notice = document.querySelector("#notice");
const targetInput = document.querySelector("#target");
const endpointPayloads = new Map();
const payloadTools = window.DragonSniffPayload;
let requestInFlight = false;
let currentSnapshot = null;
let churnProfiles = {};
let captureProfiles = {};
let updateTimer = null;
let pendingAutomationReturn = null;

const pageCopy = {
  dashboard: ["DragonSniff", "Sniff out one Dragon, follow the smoke, and bag the raw evidence."],
  thermal: ["Thermal capture", "Run bounded state and health sampling with live chamber, target, PTC, and PID telemetry."],
  churn: ["Churn stress", "Exercise repeated SSE connection lifecycles and verify that the device settles cleanly."],
  evidence: ["Evidence", "Inspect the exact parsed and raw observations retained by this local session."],
  lab: ["Super Secret Squirrel Laboratory", "Expert display controls and the complete raw evidence surface."],
};

function pageFromLocation() {
  if (window.location.pathname === "/lab") return "lab";
  const candidate = window.location.hash.replace(/^#/, "");
  return candidate in pageCopy && candidate !== "lab" ? candidate : "dashboard";
}

function activatePage(page) {
  const selected = page in pageCopy ? page : "dashboard";
  document.body.dataset.page = selected;
  text("#pageTitle", pageCopy[selected][0]);
  text("#pageDescription", pageCopy[selected][1]);
  document.querySelectorAll(".tab[data-page]").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.page === selected));
  });
  document.title = selected === "dashboard"
    ? "DragonSniff"
    : `${pageCopy[selected][0]} · DragonSniff`;
}

function navigateToPage(page) {
  const destination = page === "dashboard" ? "/" : `/#${page}`;
  window.history.pushState(null, "", destination);
  activatePage(page);
}

function scheduleUpdates(interval) {
  if (updateTimer !== null) window.clearInterval(updateTimer);
  updateTimer = window.setInterval(update, interval);
}

function applyLabOptions() {
  const poll = Number(document.querySelector("#labPollInterval").value) || 1000;
  const openRaw = document.querySelector("#labOpenRaw").checked;
  const dense = document.querySelector("#labDense").checked;
  document.body.dataset.density = dense ? "dense" : "normal";
  document.querySelectorAll("[data-endpoint] details:nth-of-type(2)").forEach((details) => {
    details.open = openRaw;
  });
  localStorage.setItem("dragonsniff.lab.poll", String(poll));
  localStorage.setItem("dragonsniff.lab.openRaw", String(openRaw));
  localStorage.setItem("dragonsniff.lab.dense", String(dense));
  scheduleUpdates(poll);
}

function loadLabOptions() {
  document.querySelector("#labPollInterval").value = localStorage.getItem("dragonsniff.lab.poll") || "1000";
  document.querySelector("#labOpenRaw").checked = localStorage.getItem("dragonsniff.lab.openRaw") === "true";
  document.querySelector("#labDense").checked = localStorage.getItem("dragonsniff.lab.dense") === "true";
  applyLabOptions();
}

const churnFields = {
  cycles: "#churnCycles",
  observe_seconds: "#churnObserveSeconds",
  max_events: "#churnMaxEvents",
  delay_seconds: "#churnDelaySeconds",
};

const captureFields = {
  duration_seconds: "#captureDurationSeconds",
  state_interval_seconds: "#captureStateInterval",
  health_interval_seconds: "#captureHealthInterval",
};

function setChurnConfiguration(configuration) {
  Object.entries(churnFields).forEach(([name, selector]) => {
    document.querySelector(selector).value = configuration[name];
  });
}

function syncChurnProfiles(profiles, selectedProfile, configuration) {
  if (!profiles || Object.keys(churnProfiles).length) return;
  churnProfiles = profiles;
  const selector = document.querySelector("#churnProfile");
  Object.keys(profiles).forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    selector.append(option);
  });
  selector.value = selectedProfile in profiles ? selectedProfile : "Custom";
  setChurnConfiguration(
    payloadTools.churnProfileConfiguration(profiles, selectedProfile) || configuration,
  );
}

function setCaptureConfiguration(configuration) {
  Object.entries(captureFields).forEach(([name, selector]) => {
    document.querySelector(selector).value = configuration[name];
  });
}

function syncCaptureProfiles(profiles, selectedProfile, configuration) {
  if (!profiles || Object.keys(captureProfiles).length) return;
  captureProfiles = profiles;
  const selector = document.querySelector("#captureProfile");
  Object.keys(profiles).forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    selector.append(option);
  });
  selector.value = selectedProfile in profiles ? selectedProfile : "Custom";
  setCaptureConfiguration(
    payloadTools.captureProfileConfiguration(profiles, selectedProfile) || configuration,
  );
}

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

async function copyChurn(kind, button) {
  const churn = currentSnapshot?.churn;
  const value = kind === "summary"
    ? payloadTools.churnSummaryText(churn)
    : payloadTools.churnHealthText(churn);
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

async function copyCapture(button) {
  const value = payloadTools.captureSummaryText(currentSnapshot?.capture);
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

function renderChurn(snapshot) {
  const churn = snapshot.churn || {};
  syncChurnProfiles(churn.profiles, churn.profile, churn.configuration);
  const state = churn.state || "idle";
  const running = state === "running";
  const settling = state === "settling";
  const stopping = state === "stopping";
  const churnActive = running || settling || stopping;
  const captureActive = ["running", "stopping"].includes(snapshot.capture?.state);
  text("#churnState", state);
  document.querySelector("#churnState").dataset.status = state;
  text("#churnProgress", `${churn.current_cycle || 0} / ${churn.total_cycles || churn.configuration?.cycles || 0}`);
  text("#churnActive", `${churn.active_churn_connections || 0} / 1`);
  text("#churnSuccess", churn.successful_connections || 0);
  text("#churnRejected", churn.rejected_connections || 0);
  text("#churnTransportFailures", (churn.transport_failures || 0) + (churn.local_resource_failures || 0));
  text("#churnEvents", churn.events_observed || 0);
  text("#churnElapsed", `${((churn.elapsed_ms || 0) / 1000).toFixed(1)} s`);
  const settlement = churn.settlement || {};
  const settlementClients = settlement.baseline_sse_clients === null || settlement.baseline_sse_clients === undefined
    ? ""
    : ` (${settlement.latest_sse_clients ?? "?"} / baseline ${settlement.baseline_sse_clients})`;
  text("#churnSettlement", `${settlement.state || "not started"}${settlementClients}`);
  document.querySelector("#churnSettlement").dataset.status = settlement.state || "idle";
  const bootStatus = churn.boot_id_changed
    ? `changed: ${churn.initial_boot_id || "unknown"} -> ${churn.latest_boot_id || "unknown"}`
    : (churn.latest_boot_id || "not observed");
  text("#churnBoot", bootStatus);
  document.querySelector("#churnBoot").dataset.status = churn.boot_id_changed ? "error" : (churn.latest_boot_id ? "available" : "idle");
  const health = churn.latest_health;
  const healthSignal = churn.boot_id_changed
    ? "Important evidence: the observed boot ID changed during this run. No cause is inferred."
    : health?.status === 404
      ? "The health endpoint is unavailable; lifecycle exercise continues without optional health interpretation."
      : health
        ? `Observed optional health fields: ${Object.keys(health.observed || {}).join(", ") || "none"}. Settlement: ${settlement.state || "not started"}. Raw evidence is retained.`
        : "No health observation yet.";
  text("#churnHealthSignal", healthSignal);
  text("#churnHealth", churn.latest_health ? pretty(churn.latest_health) : "No health observation");
  text("#churnCyclesEvidence", churn.cycles?.length ? pretty(churn.cycles) : "No cycles recorded");

  const inputs = document.querySelectorAll("#churnForm input");
  inputs.forEach((input) => { input.disabled = churnActive; });
  document.querySelector("#churnProfile").disabled = churnActive;
  document.querySelector("#churnStartButton").disabled = churnActive || captureActive;
  document.querySelector("#churnStopButton").disabled = !running;
  document.querySelector("#copyChurnSummary").disabled = payloadTools.churnSummaryText(churn) === null;
  document.querySelector("#copyChurnHealth").disabled = payloadTools.churnHealthText(churn) === null;
}

function renderCapture(snapshot) {
  const capture = snapshot.capture || {};
  syncCaptureProfiles(capture.profiles, capture.profile, capture.configuration);
  const state = capture.state || "idle";
  const running = state === "running";
  const stopping = state === "stopping";
  const churnActive = ["running", "stopping"].includes(snapshot.churn?.state);
  text("#captureState", state);
  document.querySelector("#captureState").dataset.status = state;
  text("#captureSamples", capture.samples_completed || 0);
  text("#captureStateFailures", capture.state_failures || 0);
  text("#captureHealthFailures", capture.health_failures || 0);
  text("#captureEstimate", `${capture.recorder?.records || 0} / ${capture.estimated_records || 0}`);
  text("#captureElapsed", `${((capture.elapsed_ms || 0) / 1000).toFixed(1)} s`);
  const bootStatus = capture.boot_id_changed
    ? `changed: ${capture.initial_boot_id || "unknown"} -> ${capture.latest_boot_id || "unknown"}`
    : (capture.latest_boot_id || "not observed");
  text("#captureBoot", bootStatus);
  document.querySelector("#captureBoot").dataset.status = capture.boot_id_changed
    ? "error"
    : (capture.latest_boot_id ? "available" : "idle");
  text("#captureLatestState", capture.latest_state ? pretty(capture.latest_state) : "No state observation");
  text("#captureLatestHealth", capture.latest_health ? pretty(capture.latest_health) : "No health observation");
  renderThermals(capture.latest_state);

  document.querySelectorAll("#captureForm input").forEach((input) => {
    input.disabled = running || stopping;
  });
  document.querySelector("#captureProfile").disabled = running || stopping;
  document.querySelector("#captureStartButton").disabled = running || stopping || churnActive;
  document.querySelector("#captureStopButton").disabled = !running;
  document.querySelector("#copyCaptureSummary").disabled = payloadTools.captureSummaryText(capture) === null;
}

function renderThermals(latestState) {
  const thermal = payloadTools.thermalSnapshot(latestState);
  const temperature = (value) => value === null || value === undefined
    ? "—"
    : `${value.toFixed(1)} °C`;
  text("#thermalChamber", temperature(thermal?.chamber_c));
  text("#thermalTarget", temperature(thermal?.target_c));
  text("#thermalPtc", temperature(thermal?.ptc_c));

  const gauge = document.querySelector("#pidGauge");
  const arc = document.querySelector("#pidGaugeArc");
  const needle = document.querySelector("#pidGaugeNeedle");
  const duty = thermal?.duty_percent;
  if (duty === null || duty === undefined) {
    text("#pidOutput", "—");
    text("#pidDetail", "commanded duty unavailable");
    arc.style.strokeDasharray = "0 100";
    needle.style.transform = "rotate(-90deg)";
    gauge.removeAttribute("aria-valuenow");
    gauge.setAttribute("aria-label", "PID output unavailable");
    return;
  }

  const rounded = Number(duty.toFixed(1));
  text("#pidOutput", `${rounded.toFixed(1)}%`);
  arc.style.strokeDasharray = `${rounded} 100`;
  needle.style.transform = `rotate(${(rounded * 1.8) - 90}deg)`;
  gauge.setAttribute("aria-valuenow", String(rounded));
  gauge.setAttribute("aria-label", `PID output ${rounded.toFixed(1)} percent`);
  const output = thermal.output === null ? "output unknown" : thermal.output ? "output on" : "output off";
  const constraint = thermal.constraint ? thermal.constraint.replaceAll("_", " ") : "constraint unknown";
  text("#pidDetail", `commanded duty · ${output} · ${constraint}`);
}

function render(snapshot) {
  currentSnapshot = snapshot;
  const sse = snapshot.sse || {};
  const sseTiming = sse.details?.elapsed_ms === undefined ? "" : ` / ${sse.details.elapsed_ms.toFixed(1)} ms`;
  const globalState = snapshot.active_mode === "churn"
    ? (snapshot.churn?.state || "idle")
    : snapshot.active_mode === "capture"
      ? (snapshot.capture?.state || "idle")
      : (snapshot.session_state || "idle");
  text("#sessionBadge", globalState);
  document.querySelector("#sessionBadge").dataset.status = globalState;
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
  const stopping = snapshot.session_state === "stopping";
  const churnActive = ["running", "settling", "stopping"].includes(snapshot.churn?.state);
  const captureActive = ["running", "stopping"].includes(snapshot.capture?.state);
  const streamActive = !stopping && ["connecting", "open"].includes(sse.state);
  document.querySelector("#connectForm button[type='submit']").disabled = stopping || churnActive || captureActive;
  document.querySelector("#refreshButton").disabled = !active || stopping;
  document.querySelector("#reconnectButton").disabled = !active || stopping;
  document.querySelector("#stopEventsButton").disabled = !streamActive;
  document.querySelector("#stopButton").disabled = !active || stopping;
  renderChurn(snapshot);
  renderCapture(snapshot);
  if (pendingAutomationReturn && snapshot.active_mode === "observation") {
    const automated = pendingAutomationReturn === "capture" ? snapshot.capture : snapshot.churn;
    if (["completed", "cancelled", "failed"].includes(automated?.state)) {
      pendingAutomationReturn = null;
      notice.textContent = "Automated test finished. Live observation has resumed.";
      navigateToPage("dashboard");
    }
  }
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

async function act(path, body = {}, statusNode = notice) {
  statusNode.textContent = "Working...";
  try {
    const snapshot = await localRequest(path, {method: "POST", body: JSON.stringify(body)});
    render(snapshot);
    statusNode.textContent = "Request accepted. Live status will update below.";
    return snapshot;
  } catch (error) {
    statusNode.textContent = error.message;
    return null;
  }
}

async function startAutomatedTest(kind, path, body, statusNode) {
  pendingAutomationReturn = kind;
  const snapshot = await act(path, body, statusNode);
  if (!snapshot || snapshot.active_mode !== kind) pendingAutomationReturn = null;
}

if (window.location.protocol === "file:") {
  document.querySelector("#fileWarning").hidden = false;
  document.querySelector("main").hidden = true;
  document.querySelector("footer").hidden = true;
  document.querySelector("#sessionBadge").textContent = "service required";
} else {
  activatePage(pageFromLocation());
  document.querySelectorAll(".tab[data-page], [data-go-page]").forEach((control) => {
    control.addEventListener("click", () => navigateToPage(control.dataset.page || control.dataset.goPage));
  });
  window.addEventListener("popstate", () => activatePage(pageFromLocation()));
  ["#labPollInterval", "#labOpenRaw", "#labDense"].forEach((selector) => {
    document.querySelector(selector).addEventListener("change", applyLabOptions);
  });
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
  const churnNotice = document.querySelector("#churnNotice");
  const captureNotice = document.querySelector("#captureNotice");
  document.querySelector("#captureProfile").addEventListener("change", (event) => {
    const configuration = payloadTools.captureProfileConfiguration(
      captureProfiles,
      event.currentTarget.value,
    );
    if (configuration) setCaptureConfiguration(configuration);
  });
  document.querySelectorAll("#captureForm input").forEach((input) => {
    input.addEventListener("input", () => {
      document.querySelector("#captureProfile").value = "Custom";
    });
  });
  document.querySelector("#captureForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const configuration = {
      duration_seconds: Number(document.querySelector("#captureDurationSeconds").value),
      state_interval_seconds: Number(document.querySelector("#captureStateInterval").value),
      health_interval_seconds: Number(document.querySelector("#captureHealthInterval").value),
    };
    startAutomatedTest(
      "capture",
      "/local/v1/capture/start",
      {target: targetInput.value.trim(), configuration},
      captureNotice,
    );
  });
  document.querySelector("#captureStopButton").addEventListener("click", () => {
    act("/local/v1/capture/stop", {}, captureNotice);
  });
  document.querySelector("#copyCaptureSummary").addEventListener("click", (event) => {
    copyCapture(event.currentTarget);
  });
  document.querySelector("#churnProfile").addEventListener("change", (event) => {
    const configuration = payloadTools.churnProfileConfiguration(
      churnProfiles,
      event.currentTarget.value,
    );
    if (configuration) setChurnConfiguration(configuration);
  });
  document.querySelectorAll("#churnForm input").forEach((input) => {
    input.addEventListener("input", () => {
      document.querySelector("#churnProfile").value = "Custom";
    });
  });
  document.querySelector("#churnForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const configuration = {
      cycles: Number(document.querySelector("#churnCycles").value),
      observe_seconds: Number(document.querySelector("#churnObserveSeconds").value),
      max_events: Number(document.querySelector("#churnMaxEvents").value),
      delay_seconds: Number(document.querySelector("#churnDelaySeconds").value),
    };
    startAutomatedTest(
      "churn",
      "/local/v1/churn/start",
      {target: targetInput.value.trim(), configuration},
      churnNotice,
    );
  });
  document.querySelector("#churnStopButton").addEventListener("click", () => {
    act("/local/v1/churn/stop", {}, churnNotice);
  });
  document.querySelector("#copyChurnSummary").addEventListener("click", (event) => {
    copyChurn("summary", event.currentTarget);
  });
  document.querySelector("#copyChurnHealth").addEventListener("click", (event) => {
    copyChurn("health", event.currentTarget);
  });
  document.querySelectorAll("[data-endpoint] .copy-button").forEach((button) => {
    button.addEventListener("click", () => copyPayload(button));
  });

  targetInput.value = localStorage.getItem("dragonsniff.target") || "";
  update();
  loadLabOptions();
}
