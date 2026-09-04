# Passive thermal telemetry capture

DragonSniff can collect a bounded, deterministic series of raw Dragon API observations while a controller is exercised elsewhere. The capture runner is deliberately passive: it issues only fixed `GET` requests to `/api/v2/info`, `/api/v2/state`, and `/api/v2/health`. It does not open SSE, set a target, select a mode, acknowledge a fault, or become part of the control loop.

## Profiles

| Profile | Duration | State cadence | Health cadence | Intended use |
| --- | ---: | ---: | ---: | --- |
| Smoke | 2 minutes | 1 second | 10 seconds | Confirm fields, identity, and short-run stability. |
| Soak | 15 minutes | 2 seconds | 30 seconds | Capture a normal warm-up or target-hold interval. |
| Extended | 30 minutes | 5 seconds | 60 seconds | Observe longer resource and steady-state behavior. |
| Long Haul | 8 hours | 5 seconds | 60 seconds | Observe full thermal soak, equilibrium drift, and long-run resource behavior. |

Every value remains visible and editable. An edited profile becomes **Custom** and is still checked against hard bounds. A schedule is rejected when its estimated request/response records could exceed the retained-session budget. This preserves the project's rule that a completed nominal run must not silently discard its own evidence.

## Evidence model

At the beginning of a run, DragonSniff captures device information. It then polls state and health independently on monotonic schedules. At the duration boundary it captures final state, health, and device information before completing.

If an endpoint responds too slowly to maintain the requested cadence, DragonSniff advances the schedule instead of issuing a burst of catch-up requests. The timestamps and elapsed request times preserve that timing evidence.

Each request and response retains:

- UTC and monotonic timestamps
- run ID, sample number, owner, and sample point
- endpoint, status, headers, and elapsed time
- exact raw response text
- parsed JSON and parsing errors when applicable

Unknown and product-specific fields remain untouched. Missing optional fields do not fail a capture. State or health request failures increment visible counters and remain evidence rather than being converted into invented controller conclusions.

Health observations track a present `boot_id`. A change is reported with the two observed values and sample location, but DragonSniff does not label it a crash or infer a cause.

## PID release-candidate workflow

1. Start the Dragon firmware build being tested and control its heater through its normal supported interface.
2. Run the Smoke profile to confirm that the expected identity and telemetry fields are present.
3. Run Soak during a representative warm-up and hold.
4. Run Extended when longer steady-state or resource evidence is useful.
5. Run Long Haul for a supervised full-system thermal soak when slow enclosure,
   controller, or resource drift is the question.
6. Export JSONL after each run and name the files externally with the firmware build and test condition.
7. Repeat the same profile and physical test condition against the comparison build.

The resulting JSONL supports later analysis of temperatures, targets, requested and delivered output, constraint reasons, heap, uptime, and boot identity when those fields are exposed by the product. DragonSniff preserves those values; it does not decide whether PID tuning, overshoot, settling time, or safety behavior passes. Acceptance criteria remain part of the product's validation plan.

## Traffic and lifecycle bounds

- One capture-owned device connection at a time
- Serialized endpoint polling
- No SSE connection during capture
- No overlap with ordinary observation or churn
- 1–43,200 second duration bounds
- 0.5–60 second state interval bounds
- 5–300 second health interval bounds
- Maximum 25,000 estimated records; each capture receives a recorder sized to its
  validated schedule so a nominal run retains its complete evidence
- Stop prevents future samples and retains everything already observed

An in-flight read remains bounded by the existing client request timeout. While it finishes, the run truthfully remains `stopping`; a replacement observation, churn run, or capture cannot start.
