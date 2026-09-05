(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DragonSniffPayload = api;
})(typeof window === "undefined" ? null : window, function () {
  "use strict";

  function payloadText(result, view) {
    if (!result || (view !== "parsed" && view !== "raw")) return null;
    if (view === "raw") {
      return typeof result.raw_payload === "string" ? result.raw_payload : null;
    }
    if (result.parse_error || !Object.prototype.hasOwnProperty.call(result, "parsed")) {
      return null;
    }
    const formatted = JSON.stringify(result.parsed, null, 2);
    return formatted === undefined ? null : formatted;
  }

  function churnSummaryText(churn) {
    if (!churn || !churn.run_id) return null;
    const fields = [
      "run_id", "state", "target", "profile", "configuration", "current_cycle", "total_cycles",
      "successful_connections", "rejected_connections", "http_failures",
      "transport_failures", "local_resource_failures", "remote_eof", "events_observed",
      "parse_failures", "boot_id_changed", "boot_id_changes", "initial_boot_id",
      "latest_boot_id", "settlement", "cleanup_complete", "failure", "start_timestamp", "end_timestamp",
      "elapsed_ms", "cycles",
    ];
    const summary = {};
    fields.forEach((name) => { summary[name] = churn[name]; });
    return JSON.stringify(summary, null, 2);
  }

  function churnHealthText(churn) {
    const raw = churn?.latest_health?.raw_payload;
    return typeof raw === "string" ? raw : null;
  }

  function churnProfileConfiguration(profiles, name) {
    const configuration = profiles?.[name];
    if (!configuration || typeof configuration !== "object") return null;
    const fields = ["cycles", "observe_seconds", "max_events", "delay_seconds"];
    if (!fields.every((field) => typeof configuration[field] === "number")) return null;
    return Object.fromEntries(fields.map((field) => [field, configuration[field]]));
  }

  function captureSummaryText(capture) {
    if (!capture || !capture.run_id) return null;
    const fields = [
      "run_id", "state", "target", "profile", "configuration", "estimated_records",
      "samples_completed", "state_successes", "state_failures", "health_successes",
      "health_failures", "initial_boot_id", "latest_boot_id", "boot_id_changed",
      "boot_id_changes", "cleanup_complete", "failure", "start_timestamp",
      "end_timestamp", "elapsed_ms",
    ];
    const summary = {};
    fields.forEach((name) => { summary[name] = capture[name]; });
    return JSON.stringify(summary, null, 2);
  }

  function captureProfileConfiguration(profiles, name) {
    const configuration = profiles?.[name];
    if (!configuration || typeof configuration !== "object") return null;
    const fields = [
      "duration_seconds", "state_interval_seconds", "health_interval_seconds",
    ];
    if (!fields.every((field) => typeof configuration[field] === "number")) return null;
    return Object.fromEntries(fields.map((field) => [field, configuration[field]]));
  }

  function thermalSnapshot(result) {
    const state = result?.parsed;
    if (!state || typeof state !== "object" || Array.isArray(state)) return null;
    const finite = (value) => typeof value === "number" && Number.isFinite(value)
      ? value
      : null;
    const chamber = finite(state.sensors?.chamber?.temperature_c);
    const ptc = finite(state.sensors?.ptc?.temperature_c);
    const effectiveTarget = finite(state.target?.effective_c);
    const requestedTarget = finite(state.target?.requested_c);
    const duty = finite(state.heater?.commanded_duty);
    return {
      chamber_c: chamber,
      target_c: effectiveTarget ?? requestedTarget,
      ptc_c: ptc,
      duty_percent: duty === null ? null : Math.max(0, Math.min(100, duty * 100)),
      constraint: typeof state.heater?.constraint === "string"
        ? state.heater.constraint
        : null,
      output: typeof state.heater?.output === "boolean" ? state.heater.output : null,
    };
  }

  return {
    payloadText,
    churnSummaryText,
    churnHealthText,
    churnProfileConfiguration,
    captureSummaryText,
    captureProfileConfiguration,
    thermalSnapshot,
  };
});
