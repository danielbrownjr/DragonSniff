"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {payloadText} = require("../src/dragonsniff/web/payload.js");

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

test("malformed or absent representations are not copyable", () => {
  assert.equal(payloadText({parsed: null, parse_error: "bad JSON"}, "parsed"), null);
  assert.equal(payloadText({}, "raw"), null);
  assert.equal(payloadText({raw_payload: "{}"}, "unknown"), null);
});
