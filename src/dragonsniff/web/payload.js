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

  return {payloadText};
});
