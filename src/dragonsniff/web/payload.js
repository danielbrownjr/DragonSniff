(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DragonSniffPayload = api;
})(typeof window === "undefined" ? null : window, function () {
  "use strict";

  const MAX_ESTIMATED_RECORDS = 25_000;
  const PUBLIC_PAGES = Object.freeze(["dashboard", "thermal", "churn", "history", "evidence"]);
  const QUICK_ANNOTATION_MARKERS = Object.freeze([
    "fan_blocked", "fan_unblocked", "airflow_partial", "airflow_restored",
    "thermistor_unplugged", "thermistor_reconnected", "chamber_opened", "chamber_closed",
    "printer_stopped", "bed_target_changed", "printer_link_lost", "jumpjet_power_off",
    "jumpjet_power_on", "stimulus_applied", "stimulus_removed", "baseline_start",
    "recovery_start", "external_log_start", "scope_trigger", "flir_capture", "abort",
    "operator_intervention",
  ]);

  function payloadText(result, view) {
    if (!result || (view !== "parsed" && view !== "raw")) return null;
    if (view === "raw") {
      return typeof result.raw_payload === "string" ? result.raw_payload : null;
    }
    if (result.parsed_available === false
        || result.decode_error
        || result.parse_error
        || !Object.prototype.hasOwnProperty.call(result, "parsed")) {
      return null;
    }
    const formatted = JSON.stringify(result.parsed, null, 2);
    return formatted === undefined ? null : formatted;
  }

  function formatBytes(bytes) {
    if (!Number.isSafeInteger(bytes) || bytes < 0) return null;
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    let rounded = unit === 0 ? value : Number(value.toFixed(1));
    if (rounded >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
      rounded = Number(value.toFixed(1));
    }
    const formatted = String(rounded);
    return `${formatted} ${units[unit]}`;
  }

  function historyStorageSummary(storage) {
    if (!storage || typeof storage !== "object" || Array.isArray(storage)) return null;
    const validSessions = Number.isSafeInteger(storage.valid_sessions)
      && storage.valid_sessions >= 0 ? storage.valid_sessions : null;
    const validBytes = formatBytes(storage.valid_bytes);
    let retained = null;
    if (validSessions !== null && validBytes !== null) {
      retained = `${validSessions} ${validSessions === 1 ? "session" : "sessions"} · ${validBytes} retained`;
    } else if (validSessions !== null) {
      retained = `${validSessions} ${validSessions === 1 ? "session" : "sessions"} retained`;
    } else if (validBytes !== null) {
      retained = `${validBytes} retained`;
    }

    const invalidSessions = Number.isSafeInteger(storage.invalid_sessions)
      && storage.invalid_sessions > 0 ? storage.invalid_sessions : null;
    const invalidBytes = invalidSessions === null || storage.invalid_bytes === 0
      ? null
      : formatBytes(storage.invalid_bytes);
    const invalid = invalidSessions === null
      ? null
      : `${invalidSessions} invalid storage ${invalidSessions === 1 ? "object" : "objects"}${invalidBytes === null ? "" : ` · ${invalidBytes}`}`;

    return retained === null && invalid === null ? null : {retained, invalid};
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
      "samples_completed", "fetches_completed", "state_successes", "state_failures",
      "health_successes",
      "health_failures", "initial_boot_id", "latest_boot_id", "boot_id_changed",
      "boot_id_changes", "cleanup_complete", "failure", "start_timestamp",
      "end_timestamp", "elapsed_ms", "annotation_count", "annotation_limit",
      "last_annotation",
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

  function captureRecordEstimate(configuration) {
    if (!configuration || typeof configuration !== "object") return null;
    const duration = configuration.duration_seconds;
    const stateInterval = configuration.state_interval_seconds;
    const healthInterval = configuration.health_interval_seconds;
    if (![duration, stateInterval, healthInterval].every(
      (value) => typeof value === "number" && Number.isFinite(value) && value > 0,
    )) return null;

    // Keep this in lockstep with CaptureConfig.estimated_records(). Each fetch
    // retains a request and a response/error, plus boundary identity and lifecycle
    // evidence. The server remains authoritative when a capture is submitted.
    const stateSamples = Math.ceil(duration / stateInterval) + 2;
    const healthSamples = Math.ceil(duration / healthInterval) + 2;
    return (stateSamples + healthSamples + 2) * 2 + 4;
  }

  function captureBudgetState(configuration, maximum = MAX_ESTIMATED_RECORDS) {
    const estimate = captureRecordEstimate(configuration);
    const validMaximum = typeof maximum === "number" && Number.isFinite(maximum) && maximum > 0
      ? maximum
      : MAX_ESTIMATED_RECORDS;
    return {
      estimate,
      maximum: validMaximum,
      allowed: estimate !== null && estimate <= validMaximum,
    };
  }

  function annotationRequest(capture, annotationId, marker, note, operator, correlation) {
    if (
      !capture
      || typeof capture.run_id !== "string"
      || typeof annotationId !== "string"
      || (!QUICK_ANNOTATION_MARKERS.includes(marker) && marker !== "operator_note")
      || typeof note !== "string"
      || (marker === "operator_note" && note.trim() === "")
    ) return null;
    const persistentId = capture.recorder?.persistent_session_id;
    const request = {
      annotation_id: annotationId,
      run_id: capture.run_id,
      capture_session_id: typeof persistentId === "string" ? persistentId : null,
      marker,
      note,
      operator: typeof operator === "string" && operator !== "" ? operator : null,
    };
    if (correlation && typeof correlation === "object") {
      const external = {};
      ["instrument", "file_reference", "clock_sync_method"].forEach((name) => {
        if (typeof correlation[name] === "string" && correlation[name] !== "") {
          external[name] = correlation[name];
        }
      });
      if (Number.isFinite(correlation.known_offset_ms)) {
        external.known_offset_ms = correlation.known_offset_ms;
      }
      if (Object.keys(external).length) request.external_correlation = external;
    }
    return request;
  }

  function annotationMarkerLabel(marker) {
    if (typeof marker !== "string") return "";
    return marker
      .replaceAll("_", " ")
      .replace(/^./, (letter) => letter.toUpperCase())
      .replace(/^Jumpjet /, "Jump Jet ")
      .replace(/^Flir /, "FLIR ");
  }

  function currentEvidenceControl(snapshot) {
    const records = snapshot?.recorder?.records;
    const available = Number.isSafeInteger(records) && records > 0;
    return {
      available,
      label: available ? "Download current session JSONL" : "No current evidence",
      href: available ? "/local/v1/session/export" : null,
    };
  }

  function resolvePage(candidate, labRoute = false) {
    if (labRoute) return "lab";
    return PUBLIC_PAGES.includes(candidate) ? candidate : "dashboard";
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

  function prusalinkSummary(source) {
    if (!source || source.configured !== true) {
      return {
        configured: false,
        status: "Disabled",
        polling: "Disabled",
        connection: "Not configured",
        printer: "—",
        bed: "—",
        freshness: "No sample",
        error: null,
      };
    }
    const data = source.data && typeof source.data === "object" ? source.data : {};
    const finite = (value) => typeof value === "number" && Number.isFinite(value);
    const bed = finite(data.bed_temperature_c) && finite(data.bed_target_c)
      ? `${data.bed_temperature_c.toFixed(1)} / ${data.bed_target_c.toFixed(1)} °C`
      : "—";
    const age = source.freshness?.sample_age_ms;
    const freshness = source.freshness?.state === "stale"
      ? `Stale${finite(age) ? ` · ${(age / 1000).toFixed(1)} s old` : ""}`
      : source.freshness?.state === "fresh"
        ? `Fresh${finite(age) ? ` · ${(age / 1000).toFixed(1)} s old` : ""}`
        : "No sample";
    const labels = {
      configured: "Configured",
      connecting: "Connecting",
      healthy: "Healthy",
      stale: "Stale",
      auth_error: "Authentication error",
      transport_error: "Transport error",
      parse_error: "Parse error",
      internal_error: "Internal error · polling stopped",
      paused: "Paused during Churn",
    };
    const connection = source.connected
      ? source.authenticated === true
        ? "Last request connected · authenticated"
        : "Last request connected"
      : source.authenticated === false
        ? "Last request: authentication rejected"
        : "Last request disconnected";
    return {
      configured: true,
      status: labels[source.source_state] || "Configured",
      polling: source.polling === true ? "Active" : "Stopped",
      connection,
      printer: typeof data.printer_state === "string" ? data.printer_state : "—",
      bed,
      freshness,
      error: typeof source.last_error?.message === "string"
        ? source.last_error.message
        : null,
    };
  }

  return {
    payloadText,
    formatBytes,
    historyStorageSummary,
    churnSummaryText,
    churnHealthText,
    churnProfileConfiguration,
    captureSummaryText,
    captureProfileConfiguration,
    captureRecordEstimate,
    captureBudgetState,
    annotationRequest,
    annotationMarkerLabel,
    currentEvidenceControl,
    resolvePage,
    thermalSnapshot,
    prusalinkSummary,
    MAX_ESTIMATED_RECORDS,
    QUICK_ANNOTATION_MARKERS,
  };
});
