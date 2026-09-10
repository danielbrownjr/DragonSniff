"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  MAX_ESTIMATED_RECORDS,
  QUICK_ANNOTATION_MARKERS,
  annotationMarkerLabel,
  annotationRequest,
  captureBudgetState,
  captureProfileConfiguration,
  captureRecordEstimate,
  captureSummaryText,
  churnHealthText,
  churnProfileConfiguration,
  churnSummaryText,
  formatBytes,
  historyStorageSummary,
  currentEvidenceControl,
  payloadText,
  prusalinkSummary,
  resolvePage,
  thermalSnapshot,
} = require("../src/dragonsniff/web/payload.js");

test("PrusaLink summary keeps disabled and unhealthy source state explicit", () => {
  assert.deepEqual(prusalinkSummary({configured: false}), {
    configured: false,
    status: "Disabled",
    connection: "Not configured",
    printer: "—",
    bed: "—",
    freshness: "No sample",
    error: null,
  });
  assert.deepEqual(prusalinkSummary({
    configured: true,
    state: "transport_error",
    connected: false,
    authenticated: null,
    freshness: {state: "stale", sample_age_ms: 16250},
    data: {printer_state: "PRINTING", bed_temperature_c: 61.25, bed_target_c: 65},
    last_error: {message: "printer unavailable"},
  }), {
    configured: true,
    status: "Transport error",
    connection: "Last request disconnected",
    printer: "PRINTING",
    bed: "61.3 / 65.0 °C",
    freshness: "Stale · 16.3 s old",
    error: "printer unavailable",
  });
});

test("PrusaLink summary reports authenticated fresh state without inventing fields", () => {
  assert.deepEqual(prusalinkSummary({
    configured: true,
    state: "healthy",
    connected: true,
    authenticated: true,
    freshness: {state: "fresh", sample_age_ms: 250},
    data: {printer_state: "IDLE"},
  }), {
    configured: true,
    status: "Healthy",
    connection: "Last request connected · authenticated",
    printer: "IDLE",
    bed: "—",
    freshness: "Fresh · 0.3 s old",
    error: null,
  });
});

test("current evidence control follows authoritative recorder records", () => {
  const unavailable = {
    available: false,
    label: "No current evidence",
    href: null,
  };
  const available = {
    available: true,
    label: "Download current session JSONL",
    href: "/local/v1/session/export",
  };
  assert.deepEqual(currentEvidenceControl(undefined), unavailable);
  assert.deepEqual(currentEvidenceControl({
    active_mode: "idle",
    recorder: {records: 0},
  }), unavailable);
  assert.deepEqual(currentEvidenceControl({
    active_mode: "observation",
    recorder: {records: 1},
  }), available);
  assert.deepEqual(currentEvidenceControl({
    active_mode: "idle",
    recorder: {records: 0},
  }), unavailable);
  assert.deepEqual(
    currentEvidenceControl({recorder: {records: "1"}}),
    unavailable,
  );
});

test("byte formatting uses deterministic binary units", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1023), "1023 B");
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(1_048_575), "1 MB");
  assert.equal(formatBytes(50_646_221), "48.3 MB");
  assert.equal(formatBytes(1_073_741_823), "1 GB");
  assert.equal(formatBytes(5 * 1024 ** 3), "5 GB");
  assert.equal(formatBytes(1_099_511_627_775), "1 TB");
  assert.equal(formatBytes(5 * 1024 ** 4), "5 TB");
  assert.equal(formatBytes(-1), null);
  assert.equal(formatBytes("1024"), null);
});

test("History summary renders retained sessions and bytes quietly when valid", () => {
  assert.deepEqual(historyStorageSummary({
    retained_sessions: 0,
    retained_bytes: 0,
    valid_sessions: 0,
    valid_bytes: 0,
    invalid_sessions: 0,
    invalid_bytes: 0,
  }), {
    retained: "0 sessions · 0 B retained",
    invalid: null,
  });
  assert.deepEqual(historyStorageSummary({
    retained_sessions: 12,
    retained_bytes: 50_646_221,
    valid_sessions: 12,
    valid_bytes: 50_646_221,
    invalid_sessions: 0,
    invalid_bytes: 0,
  }), {
    retained: "12 sessions · 48.3 MB retained",
    invalid: null,
  });
});

test("History summary calls out invalid storage only when its count is nonzero", () => {
  assert.deepEqual(historyStorageSummary({
    retained_sessions: 1,
    retained_bytes: 1024,
    valid_sessions: 1,
    valid_bytes: 1024,
    invalid_sessions: 1,
    invalid_bytes: 0,
  }), {
    retained: "1 session · 1 KB retained",
    invalid: "1 invalid storage object",
  });
  assert.deepEqual(historyStorageSummary({
    retained_sessions: 3,
    retained_bytes: 12_700,
    valid_sessions: 2,
    valid_bytes: 4_400,
    invalid_sessions: 1,
    invalid_bytes: 8_300,
  }), {
    retained: "2 sessions · 4.3 KB retained",
    invalid: "1 invalid storage object · 8.1 KB",
  });
});

test("History summary tolerates absent and malformed storage data", () => {
  assert.equal(historyStorageSummary(undefined), null);
  assert.equal(historyStorageSummary([]), null);
  assert.equal(historyStorageSummary({
    retained_sessions: "12",
    retained_bytes: -1,
    valid_sessions: "12",
    valid_bytes: -1,
    invalid_sessions: "2",
    invalid_bytes: Number.NaN,
  }), null);
  assert.deepEqual(historyStorageSummary({
    retained_sessions: 1,
    retained_bytes: "unknown",
    valid_sessions: 1,
    valid_bytes: "unknown",
    invalid_sessions: 1,
    invalid_bytes: "unknown",
  }), {
    retained: "1 session retained",
    invalid: "1 invalid storage object",
  });
});

test("parsed copy uses stable formatted JSON and preserves unknown fields", () => {
  const result = {parsed: {api_version: 2, future_field: {value: true}}, parse_error: null};
  assert.equal(
    payloadText(result, "parsed"),
    '{\n  "api_version": 2,\n  "future_field": {\n    "value": true\n  }\n}',
  );
});

test("parsed copy preserves supplementary multilingual and combining Unicode", () => {
  const result = {parsed: {emoji: "🚀", text: "日本語 café é"}, parse_error: null};
  assert.equal(
    payloadText(result, "parsed"),
    '{\n  "emoji": "🚀",\n  "text": "日本語 café é"\n}',
  );
});

test("parsed JSON null remains a valid copyable payload", () => {
  assert.equal(payloadText({parsed: null, parse_error: null}, "parsed"), "null");
});

test("raw copy preserves the original response exactly", () => {
  const raw = '{"compact":true}\n';
  assert.equal(payloadText({raw_payload: raw}, "raw"), raw);
});

test("parsed HTTP error objects remain copyable instead of becoming null", () => {
  const result = {parsed: {error: "missing", future_detail: true}, parse_error: null};
  assert.equal(
    payloadText(result, "parsed"),
    '{\n  "error": "missing",\n  "future_detail": true\n}',
  );
});

test("invalid JSON error bodies do not enable parsed copy", () => {
  assert.equal(payloadText({parsed: null, parse_error: "invalid JSON"}, "parsed"), null);
});

test("unsafe parsed device Unicode stays unavailable while exact raw evidence remains copyable", () => {
  const raw = '{"nested":["ok","\\ud800"]}';
  const result = {
    raw_payload: raw,
    parsed: null,
    parse_error: "parsed JSON contains non-UTF-8-encodable text",
    parse_error_kind: "unsafe_text",
    parsed_available: false,
  };
  assert.equal(payloadText(result, "parsed"), null);
  assert.equal(payloadText(result, "raw"), raw);
});

test("decode failures never expose replacement-decoded text as parsed JSON", () => {
  const result = {
    raw_payload: '{"value":"�"}',
    parsed: null,
    parsed_available: false,
    decode_error: "invalid UTF-8",
    parse_error: null,
    parse_error_kind: null,
  };
  assert.equal(payloadText(result, "parsed"), null);
  assert.equal(payloadText(result, "raw"), result.raw_payload);
});

test("malformed or absent representations are not copyable", () => {
  assert.equal(payloadText({parsed: null, parse_error: "bad JSON"}, "parsed"), null);
  assert.equal(payloadText({}, "raw"), null);
  assert.equal(payloadText({raw_payload: "{}"}, "unknown"), null);
});

test("churn summary copies stored evidence rather than rendered text", () => {
  const churn = {
    run_id: "run-1",
    state: "completed",
    target: "http://dragon.local",
    configuration: {cycles: 1},
    current_cycle: 1,
    total_cycles: 1,
    successful_connections: 1,
    rejected_connections: 0,
    events_observed: 2,
    boot_id_changed: false,
    settlement: {state: "recovered", baseline_sse_clients: 0, latest_sse_clients: 0},
    cleanup_complete: true,
    cycles: [{cycle: 1, outcome: "disconnected", future: {value: true}}],
  };

  const copied = JSON.parse(churnSummaryText(churn));
  assert.equal(copied.run_id, "run-1");
  assert.equal(copied.cycles[0].future.value, true);
  assert.equal(copied.cleanup_complete, true);
  assert.equal(copied.settlement.state, "recovered");
});

test("churn copy controls reject absent evidence and preserve raw health exactly", () => {
  const raw = '{"boot_id":"abc","unknown":[1,2]}\n';
  assert.equal(churnSummaryText({state: "idle"}), null);
  assert.equal(churnHealthText({latest_health: {raw_payload: raw}}), raw);
  assert.equal(churnHealthText({latest_health: {parsed: {boot_id: "abc"}}}), null);
});

test("named churn profiles populate exact editable configurations", () => {
  const profiles = {
    Baseline: {cycles: 3, observe_seconds: 2, max_events: 3, delay_seconds: 0.5},
    Extended: {cycles: 10, observe_seconds: 5, max_events: 5, delay_seconds: 0.25},
    Stress: {cycles: 20, observe_seconds: 10, max_events: 10, delay_seconds: 0.1},
  };

  assert.deepEqual(churnProfileConfiguration(profiles, "Baseline"), profiles.Baseline);
  assert.deepEqual(churnProfileConfiguration(profiles, "Extended"), profiles.Extended);
  assert.deepEqual(churnProfileConfiguration(profiles, "Stress"), profiles.Stress);
  assert.equal(churnProfileConfiguration(profiles, "Custom"), null);
  const selected = churnProfileConfiguration(profiles, "Baseline");
  selected.cycles = 4;
  assert.equal(profiles.Baseline.cycles, 3);
});

test("capture summary copies bounded run evidence", () => {
  const capture = {
    run_id: "capture-1",
    state: "completed",
    target: "http://dragon.local",
    profile: "Smoke",
    configuration: {duration_seconds: 120},
    estimated_records: 272,
    samples_completed: 121,
    state_successes: 121,
    state_failures: 0,
    health_successes: 14,
    health_failures: 0,
    boot_id_changed: false,
    cleanup_complete: true,
  };

  const copied = JSON.parse(captureSummaryText(capture));
  assert.equal(copied.run_id, "capture-1");
  assert.equal(copied.samples_completed, 121);
  assert.equal(copied.cleanup_complete, true);
  assert.equal(captureSummaryText({state: "idle"}), null);
});

test("named capture profiles populate exact editable schedules", () => {
  const profiles = {
    Smoke: {duration_seconds: 120, state_interval_seconds: 1, health_interval_seconds: 10},
    Soak: {duration_seconds: 900, state_interval_seconds: 2, health_interval_seconds: 30},
    Extended: {duration_seconds: 1800, state_interval_seconds: 5, health_interval_seconds: 60},
    "Long Haul": {duration_seconds: 28800, state_interval_seconds: 5, health_interval_seconds: 60},
  };

  assert.deepEqual(captureProfileConfiguration(profiles, "Smoke"), profiles.Smoke);
  assert.deepEqual(captureProfileConfiguration(profiles, "Soak"), profiles.Soak);
  assert.deepEqual(captureProfileConfiguration(profiles, "Extended"), profiles.Extended);
  assert.deepEqual(captureProfileConfiguration(profiles, "Long Haul"), profiles["Long Haul"]);
  assert.equal(captureProfileConfiguration(profiles, "Custom"), null);
  const selected = captureProfileConfiguration(profiles, "Smoke");
  selected.duration_seconds = 60;
  assert.equal(profiles.Smoke.duration_seconds, 120);
});

test("capture estimate previews the server retained-record calculation", () => {
  assert.equal(captureRecordEstimate({
    duration_seconds: 120,
    state_interval_seconds: 1,
    health_interval_seconds: 10,
  }), 280);
  assert.equal(captureRecordEstimate({
    duration_seconds: 43_200,
    state_interval_seconds: 0.5,
    health_interval_seconds: 5,
  }), 190_096);
  assert.equal(captureRecordEstimate({
    duration_seconds: 120,
    state_interval_seconds: 0,
    health_interval_seconds: 10,
  }), null);
});

test("capture budget state gates invalid and oversized schedules", () => {
  assert.equal(MAX_ESTIMATED_RECORDS, 25_000);
  assert.deepEqual(captureBudgetState({
    duration_seconds: 120,
    state_interval_seconds: 1,
    health_interval_seconds: 10,
  }), {estimate: 280, maximum: 25_000, allowed: true});
  assert.equal(captureBudgetState({
    duration_seconds: 43_200,
    state_interval_seconds: 0.5,
    health_interval_seconds: 5,
  }).allowed, false);
  assert.equal(captureBudgetState({duration_seconds: 0}).allowed, false);
});

test("quick annotations expose the complete stable marker vocabulary", () => {
  assert.deepEqual(QUICK_ANNOTATION_MARKERS, [
    "fan_blocked", "fan_unblocked", "airflow_partial", "airflow_restored",
    "thermistor_unplugged", "thermistor_reconnected", "chamber_opened", "chamber_closed",
    "printer_stopped", "bed_target_changed", "printer_link_lost", "jumpjet_power_off",
    "jumpjet_power_on", "stimulus_applied", "stimulus_removed", "baseline_start",
    "recovery_start", "external_log_start", "scope_trigger", "flir_capture", "abort",
    "operator_intervention",
  ]);
  assert.equal(annotationMarkerLabel("printer_link_lost"), "Printer link lost");
  assert.equal(annotationMarkerLabel("jumpjet_power_on"), "Jump Jet power on");
  assert.equal(annotationMarkerLabel("flir_capture"), "FLIR capture");
});

test("annotation request preserves freeform and external correlation content", () => {
  const capture = {
    run_id: "0123456789abcdef0123456789abcdef",
    recorder: {persistent_session_id: "fedcba9876543210fedcba9876543210"},
  };
  const request = annotationRequest(
    capture,
    "01234567-89ab-4cde-8fab-0123456789ab",
    "flir_capture",
    " ΔT 12.5 °C — IMG #42 ",
    "Dan / 現場",
    {
      instrument: "FLIR E8-XT",
      file_reference: "IMG_0042.jpg",
      clock_sync_method: "visible UTC clock frame",
      known_offset_ms: -237.5,
      ignored: "not part of the contract",
    },
  );

  assert.deepEqual(request, {
    annotation_id: "01234567-89ab-4cde-8fab-0123456789ab",
    run_id: capture.run_id,
    capture_session_id: capture.recorder.persistent_session_id,
    marker: "flir_capture",
    note: " ΔT 12.5 °C — IMG #42 ",
    operator: "Dan / 現場",
    external_correlation: {
      instrument: "FLIR E8-XT",
      file_reference: "IMG_0042.jpg",
      clock_sync_method: "visible UTC clock frame",
      known_offset_ms: -237.5,
    },
  });
});

test("annotation request rejects stale or unsupported browser state", () => {
  const id = "01234567-89ab-4cde-8fab-0123456789ab";
  assert.equal(annotationRequest(null, id, "abort", "", "", null), null);
  assert.equal(annotationRequest({}, id, "abort", "", "", null), null);
  assert.equal(
    annotationRequest({run_id: "run"}, id, "made_up", "", "", null),
    null,
  );
  assert.equal(
    annotationRequest({run_id: "run"}, id, "operator_note", " \t\n ", "", null),
    null,
  );
});

test("page routing accepts only owned public page names", () => {
  assert.equal(resolvePage("thermal"), "thermal");
  assert.equal(resolvePage("history"), "history");
  assert.equal(resolvePage("lab"), "dashboard");
  assert.equal(resolvePage("constructor"), "dashboard");
  assert.equal(resolvePage("toString"), "dashboard");
  assert.equal(resolvePage("valueOf"), "dashboard");
  assert.equal(resolvePage("anything", true), "lab");
});

test("thermal snapshot extracts bounded optional display values", () => {
  const sample = thermalSnapshot({parsed: {
    sensors: {
      chamber: {temperature_c: 69.95},
      ptc: {temperature_c: 66.7},
    },
    target: {requested_c: 70, effective_c: 69},
    heater: {commanded_duty: 0.155, constraint: "approach_limit", output: true},
  }});

  assert.deepEqual(sample, {
    chamber_c: 69.95,
    target_c: 69,
    ptc_c: 66.7,
    duty_percent: 15.5,
    constraint: "approach_limit",
    output: true,
  });
  assert.equal(thermalSnapshot({parsed: null}), null);
  assert.equal(thermalSnapshot({parsed: []}), null);
});

test("thermal snapshot does not invent absent values and clamps the gauge", () => {
  assert.deepEqual(thermalSnapshot({parsed: {heater: {commanded_duty: 1.4}}}), {
    chamber_c: null,
    target_c: null,
    ptc_c: null,
    duty_percent: 100,
    constraint: null,
    output: null,
  });
  assert.equal(
    thermalSnapshot({parsed: {heater: {commanded_duty: "0.5"}}}).duty_percent,
    null,
  );
});
