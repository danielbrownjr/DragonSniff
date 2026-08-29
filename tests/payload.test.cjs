"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  churnHealthText,
  churnSummaryText,
  payloadText,
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
    cleanup_complete: true,
    cycles: [{cycle: 1, outcome: "disconnected", future: {value: true}}],
  };

  const copied = JSON.parse(churnSummaryText(churn));
  assert.equal(copied.run_id, "run-1");
  assert.equal(copied.cycles[0].future.value, true);
  assert.equal(copied.cleanup_complete, true);
});

test("churn copy controls reject absent evidence and preserve raw health exactly", () => {
  const raw = '{"boot_id":"abc","unknown":[1,2]}\n';
  assert.equal(churnSummaryText({state: "idle"}), null);
  assert.equal(churnHealthText({latest_health: {raw_payload: raw}}), raw);
  assert.equal(churnHealthText({latest_health: {parsed: {boot_id: "abc"}}}), null);
});
