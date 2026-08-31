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
      "run_id", "state", "target", "configuration", "current_cycle", "total_cycles",
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

  return {payloadText, churnSummaryText, churnHealthText};
});
