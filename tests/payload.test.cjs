"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  captureProfileConfiguration,
  captureSummaryText,
  churnHealthText,
  churnProfileConfiguration,
  churnSummaryText,
  payloadText,
  thermalSnapshot,
} = require("../src/dragonsniff/web/payload.js");

test("parsed copy uses stable formatted JSON and preserves unknown fields", () => {
  const result = {parsed: {api_version: 2, future_field: {value: true}}, parse_error: null};
  assert.equal(
    payloadText(result, "parsed"),
    '{\n  "api_version": 2,\n  "future_field": {\n    "value": true\n  }\n}',
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
