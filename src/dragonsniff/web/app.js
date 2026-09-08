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
let historyRequestInFlight = false;
let annotationRequestInFlight = false;
const pendingAnnotationKey = "dragonsniff.pendingCaptureAnnotation";

const pageCopy = {
  dashboard: ["DragonSniff", "Sniff out one Dragon, follow the smoke, and bag the raw evidence."],
  thermal: ["Thermal capture", "Run bounded state and health sampling with live chamber, target, PTC, and PID telemetry."],
  churn: ["Churn stress", "Exercise repeated SSE connection lifecycles and verify that the device settles cleanly."],
  history: ["Session history", "Review and download durable evidence from completed or interrupted runs."],
  evidence: ["Evidence", "Inspect the exact parsed and raw observations retained by this local session."],
  lab: ["Super Secret Squirrel Laboratory", "Expert display controls and the complete raw evidence surface."],
};

function pageFromLocation() {
  const candidate = window.location.hash.replace(/^#/, "");
  return payloadTools.resolvePage(candidate, window.location.pathname === "/lab");
}

function activatePage(page) {
  const selected = payloadTools.resolvePage(page, window.location.pathname === "/lab");
  document.body.dataset.page = selected;
  text("#pageTitle", pageCopy[selected][0]);
  text("#pageDescription", pageCopy[selected][1]);
  document.querySelectorAll(".tab[data-page]").forEach((tab) => {
    if (tab.dataset.page === selected) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  document.querySelectorAll("[data-page-content]").forEach((section) => {
    section.hidden = !section.dataset.pageContent.split(/\s+/).includes(selected);
  });
  document.title = selected === "dashboard"
    ? "DragonSniff"
    : `${pageCopy[selected][0]} · DragonSniff`;
  if (selected === "history" && window.location.protocol !== "file:") updateHistory();
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

function applyLabOptions(persist = true) {
  const poll = Number(document.querySelector("#labPollInterval").value) || 1000;
  const openRaw = document.querySelector("#labOpenRaw").checked;
  const dense = document.querySelector("#labDense").checked;
  document.body.dataset.density = dense ? "dense" : "normal";
  document.querySelectorAll('[data-copy-view="raw"]').forEach((button) => {
    button.closest("details").open = openRaw;
  });
  if (persist) {
    localStorage.setItem("dragonsniff.lab.poll", String(poll));
    localStorage.setItem("dragonsniff.lab.openRaw", String(openRaw));
    localStorage.setItem("dragonsniff.lab.dense", String(dense));
  }
  scheduleUpdates(poll);
}

function loadLabOptions() {
  document.querySelector("#labPollInterval").value = localStorage.getItem("dragonsniff.lab.poll") || "1000";
  document.querySelector("#labOpenRaw").checked = localStorage.getItem("dragonsniff.lab.openRaw") === "true";
  document.querySelector("#labDense").checked = localStorage.getItem("dragonsniff.lab.dense") === "true";
  applyLabOptions(false);
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
  selector.value = Object.hasOwn(profiles, selectedProfile) ? selectedProfile : "Custom";
  setChurnConfiguration(
    payloadTools.churnProfileConfiguration(profiles, selectedProfile) || configuration,
  );
}

function setCaptureConfiguration(configuration) {
  Object.entries(captureFields).forEach(([name, selector]) => {
    document.querySelector(selector).value = configuration[name];
  });
  updateCaptureBudget();
}

function captureConfigurationFromForm() {
  return {
    duration_seconds: Number(document.querySelector("#captureDurationSeconds").value),
    state_interval_seconds: Number(document.querySelector("#captureStateInterval").value),
    health_interval_seconds: Number(document.querySelector("#captureHealthInterval").value),
  };
}

function updateCaptureBudget() {
  const configuredMaximum = Number(currentSnapshot?.capture?.bounds?.max_estimated_records);
  const state = payloadTools.captureBudgetState(
    captureConfigurationFromForm(),
    Number.isFinite(configuredMaximum) ? configuredMaximum : payloadTools.MAX_ESTIMATED_RECORDS,
  );
  const {estimate, maximum, allowed} = state;
  const budget = document.querySelector("#captureBudget");
  budget.dataset.status = allowed ? "available" : "error";
  const message = estimate === null
    ? "Enter positive schedule values to preview retained evidence."
    : !allowed
      ? `Estimated retained records: ${estimate.toLocaleString()} / ${maximum.toLocaleString()} — shorten the duration or increase an interval.`
      : `Estimated retained records: ${estimate.toLocaleString()} / ${maximum.toLocaleString()} — schedule fits the evidence budget.`;
  if (budget.textContent !== message) budget.textContent = message;

  const captureActive = ["running", "stopping"].includes(currentSnapshot?.capture?.state);
  const churnActive = ["running", "settling", "stopping"].includes(currentSnapshot?.churn?.state);
  document.querySelector("#captureStartButton").disabled = (
    captureActive || churnActive || !allowed
  );
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
  selector.value = Object.hasOwn(profiles, selectedProfile) ? selectedProfile : "Custom";
  setCaptureConfiguration(
    payloadTools.captureProfileConfiguration(profiles, selectedProfile) || configuration,
  );
}

function text(selector, value) {
  document.querySelector(selector).textContent = String(value);
}

function showNotice(node, message, status = "idle") {
  node.textContent = message;
  node.dataset.status = status;
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
    const error = new Error(payload.message || payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.code = payload.error;
    throw error;
  }
  return payload;
}

function annotationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function annotationCorrelationFromForm() {
  const offsetText = document.querySelector("#annotationKnownOffset").value;
  return {
    instrument: document.querySelector("#annotationInstrument").value,
    file_reference: document.querySelector("#annotationFileReference").value,
    clock_sync_method: document.querySelector("#annotationClockSync").value,
    known_offset_ms: offsetText === "" ? null : Number(offsetText),
  };
}

function storedPendingAnnotation() {
  const encoded = sessionStorage.getItem(pendingAnnotationKey);
  if (encoded === null) return null;
  try {
    const value = JSON.parse(encoded);
    return value && typeof value === "object" ? value : null;
  } catch (error) {
    sessionStorage.removeItem(pendingAnnotationKey);
    return null;
  }
}

function clearPendingAnnotation(annotationId) {
  const pending = storedPendingAnnotation();
  if (pending?.annotation_id === annotationId) sessionStorage.removeItem(pendingAnnotationKey);
}

function setAnnotationControls(capture) {
  const running = capture?.state === "running";
  const limitReached = (capture?.annotation_count || 0) >= (capture?.annotation_limit || 1000);
  const busy = annotationRequestInFlight || storedPendingAnnotation() !== null;
  const unavailable = !running || busy || limitReached;
  document.querySelectorAll("#annotationQuickPicks button").forEach((button) => {
    button.disabled = unavailable;
  });
  document.querySelectorAll("#annotationForm input, #annotationForm textarea").forEach((control) => {
    control.disabled = busy;
  });
  document.querySelector("#annotationNoteButton").disabled = unavailable || document.querySelector("#annotationNote").value.trim().length === 0;
}

function renderAnnotation(capture) {
  const count = capture?.annotation_count || 0;
  const limit = capture?.annotation_limit || 1000;
  text("#annotationCount", `${count} / ${limit}`);
  const latest = capture?.last_annotation;
  text(
    "#annotationLatest",
    latest
      ? `Latest: ${payloadTools.annotationMarkerLabel(latest.marker)} at +${Number(latest.capture_relative_ms).toFixed(1)} ms (${latest.timestamp})${latest.note ? ` — ${latest.note}` : ""}`
      : "No operator marker recorded in this capture.",
  );
  setAnnotationControls(capture);
}

async function postAnnotation(request, recovering = false) {
  if (annotationRequestInFlight) return;
  annotationRequestInFlight = true;
  sessionStorage.setItem(pendingAnnotationKey, JSON.stringify(request));
  setAnnotationControls(currentSnapshot?.capture);
  const annotationNotice = document.querySelector("#annotationNotice");
  showNotice(annotationNotice, recovering ? "Confirming interrupted marker..." : "Recording marker...", "requesting");
  try {
    const result = await localRequest("/local/v1/capture/annotations", {
      method: "POST",
      body: JSON.stringify(request),
    });
    clearPendingAnnotation(request.annotation_id);
    render(result.snapshot);
    const relative = Number(result.annotation.capture_relative_ms).toFixed(1);
    showNotice(
      annotationNotice,
      `${result.created ? "Recorded" : "Confirmed already recorded"}: ${payloadTools.annotationMarkerLabel(result.annotation.marker)} at +${relative} ms.`,
      "available",
    );
    document.querySelector("#annotationNote").value = "";
  } catch (error) {
    if (Number.isInteger(error.status)) {
      clearPendingAnnotation(request.annotation_id);
      showNotice(annotationNotice, `Not recorded: ${error.message}`, "error");
    } else {
      showNotice(
        annotationNotice,
        "Marker outcome is not confirmed yet. DragonSniff will retry the same annotation ID when the local service reconnects.",
        "error",
      );
    }
  } finally {
    annotationRequestInFlight = false;
    setAnnotationControls(currentSnapshot?.capture);
  }
}

function submitAnnotation(marker) {
  if (annotationRequestInFlight || storedPendingAnnotation() !== null) return;
  const note = document.querySelector("#annotationNote").value;
  const request = payloadTools.annotationRequest(
    currentSnapshot?.capture,
    annotationId(),
    marker,
    note,
    document.querySelector("#annotationOperator").value,
    annotationCorrelationFromForm(),
  );
  if (request === null) {
    showNotice(document.querySelector("#annotationNotice"), "Capture must be running and freeform notes cannot be blank.", "error");
    return;
  }
  postAnnotation(request);
}

function recoverPendingAnnotation() {
  const pending = storedPendingAnnotation();
  if (pending === null || annotationRequestInFlight) return;
  postAnnotation(pending, true);
}

function renderAnnotationQuickPicks() {
  const container = document.querySelector("#annotationQuickPicks");
  payloadTools.QUICK_ANNOTATION_MARKERS.forEach((marker) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.marker = marker;
    button.textContent = payloadTools.annotationMarkerLabel(marker);
    button.addEventListener("click", () => submitAnnotation(marker));
    container.append(button);
  });
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

function renderHistory(sessions) {
  const rows = document.querySelector("#historyRows");
  rows.replaceChildren();
  if (!sessions.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "No persistent sessions have been recorded.";
    row.append(cell);
    rows.append(row);
    return;
  }
  sessions.forEach((session) => {
    const row = document.createElement("tr");
    const values = [
      new Date(session.created_at).toLocaleString(),
      session.kind,
      session.target,
      session.status,
      session.records,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      if (index === 3) cell.dataset.status = session.status;
      row.append(cell);
    });
    const action = document.createElement("td");
    const link = document.createElement("a");
    link.className = "button-link";
    link.href = `/local/v1/history/${session.session_id}/export`;
    link.textContent = "Download JSONL";
    link.setAttribute("download", "");
    action.append(link);
    row.append(action);
    rows.append(row);
  });
}

function renderHistoryStorage(storage) {
  const container = document.querySelector("#historyStorageSummary");
  const summary = payloadTools.historyStorageSummary(storage);
  container.replaceChildren();
  container.hidden = summary === null;
  if (summary === null) return;
  if (summary.retained !== null) {
    const retained = document.createElement("span");
    retained.textContent = summary.retained;
    container.append(retained);
  }
  if (summary.invalid !== null) {
    const invalid = document.createElement("span");
    invalid.className = "history-storage-invalid";
    invalid.textContent = summary.invalid;
    container.append(invalid);
  }
}

async function updateHistory() {
  if (historyRequestInFlight) return;
  historyRequestInFlight = true;
  const historyNotice = document.querySelector("#historyNotice");
  try {
    const result = await localRequest("/local/v1/history");
    renderHistory(Array.isArray(result.sessions) ? result.sessions : []);
    renderHistoryStorage(result.storage);
    showNotice(
      historyNotice,
      result.persistent ? "" : "Persistent storage is not configured for this DragonSniff service.",
      result.persistent ? "available" : "idle",
    );
  } catch (error) {
    renderHistoryStorage(null);
    showNotice(historyNotice, `Could not load session history: ${error.message}`, "error");
  } finally {
    historyRequestInFlight = false;
  }
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
  document.querySelector("#churnExportLink").hidden = !churn.run_id;
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
  text("#captureEstimate", `${capture.recorder?.records || 0} / ${capture.recorder?.max_records || 0}`);
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
  renderAnnotation(capture);

  document.querySelectorAll("#captureForm input").forEach((input) => {
    input.disabled = running || stopping;
  });
  document.querySelector("#captureProfile").disabled = running || stopping;
  document.querySelector("#captureStopButton").disabled = !running;
  document.querySelector("#copyCaptureSummary").disabled = payloadTools.captureSummaryText(capture) === null;
  document.querySelector("#captureExportLink").hidden = !capture.run_id;
  updateCaptureBudget();
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
  const exportLink = document.querySelector("#exportLink");
  const evidence = payloadTools.currentEvidenceControl(snapshot);
  exportLink.textContent = evidence.label;
  exportLink.setAttribute("aria-disabled", String(!evidence.available));
  if (evidence.available) exportLink.href = evidence.href;
  else exportLink.removeAttribute("href");
  renderChurn(snapshot);
  renderCapture(snapshot);
  if (snapshot.automation_return?.error) {
    showNotice(
      notice,
      `Automated test finished, but live observation could not resume: ${snapshot.automation_return.error}`,
      "error",
    );
  } else if (snapshot.active_mode === "observation" && snapshot.automation_return?.resumed_after) {
    showNotice(notice, "Automated test finished. Live observation has resumed.", "available");
  }
}

async function update() {
  if (requestInFlight) return;
  requestInFlight = true;
  try {
    render(await localRequest("/local/v1/session"));
    recoverPendingAnnotation();
  } catch (error) {
    showNotice(notice, `Local service error: ${error.message}`, "error");
  } finally {
    requestInFlight = false;
  }
}

async function act(path, body = {}, statusNode = notice) {
  showNotice(statusNode, "Working...", "requesting");
  try {
    const snapshot = await localRequest(path, {method: "POST", body: JSON.stringify(body)});
    render(snapshot);
    showNotice(statusNode, "Request accepted. Live status will update below.", "available");
    return snapshot;
  } catch (error) {
    showNotice(statusNode, error.message, "error");
    return null;
  }
}

if (window.location.protocol === "file:") {
  document.querySelector("#fileWarning").hidden = false;
  document.querySelector("main").hidden = true;
  document.querySelector("footer").hidden = true;
  document.querySelector("#sessionBadge").textContent = "service required";
} else {
  activatePage(pageFromLocation());
  document.querySelectorAll("[data-go-page]").forEach((control) => {
    control.addEventListener("click", () => navigateToPage(control.dataset.goPage));
  });
  window.addEventListener("popstate", () => activatePage(pageFromLocation()));
  window.addEventListener("hashchange", () => activatePage(pageFromLocation()));
  document.querySelector("#historyRefreshButton").addEventListener("click", updateHistory);
  ["#labPollInterval", "#labOpenRaw", "#labDense"].forEach((selector) => {
    document.querySelector(selector).addEventListener("change", () => applyLabOptions());
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
      updateCaptureBudget();
    });
  });
  document.querySelector("#captureForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const configuration = captureConfigurationFromForm();
    act(
      "/local/v1/capture/start",
      {target: targetInput.value.trim(), configuration},
      captureNotice,
    );
  });
  document.querySelector("#captureStopButton").addEventListener("click", () => {
    act("/local/v1/capture/stop", {}, captureNotice);
  });
  document.querySelector("#annotationForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitAnnotation("operator_note");
  });
  document.querySelector("#annotationNote").addEventListener("input", () => {
    setAnnotationControls(currentSnapshot?.capture);
  });
  document.querySelector("#annotationOperator").addEventListener("input", (event) => {
    localStorage.setItem("dragonsniff.operator", event.currentTarget.value);
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
    act(
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
  document.querySelector("#annotationOperator").value = localStorage.getItem("dragonsniff.operator") || "";
  renderAnnotationQuickPicks();
  update();
  if (window.location.pathname === "/lab") loadLabOptions();
  else scheduleUpdates(1000);
}
