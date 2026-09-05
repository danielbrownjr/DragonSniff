# DragonBreath hardware validation

## Issue #1: first-sniff validation

This record preserves the first physical DragonSniff acceptance result. It is evidence for DragonSniff behavior, not a general performance claim.

### Device and session

- DragonBreath firmware: `bba937b`
- DragonBreath boot ID: `b81d2613208fe5fa2c64edae51a7ca4d`
- DragonBreath SSE client cap: 2
- DragonSniff mode: one explicit target, fixed read-only endpoints

### Observed behavior

- Browser to local DragonSniff service: pass
- `GET /api/v2/info`: pass
- `GET /api/v2/state`: pass
- `GET /api/v2/health`: pass
- HTTP 503 recording when the product SSE cap was full: pass
- Explicit reconnect after capacity became available: pass
- Continuous SSE telemetry parsing and recording: pass
- Explicit DragonSniff stream cleanup: pass
- JSONL evidence export: pass

While DragonSniff was connected, DragonBreath reported two SSE clients. After DragonSniff stopped, its client disappeared and one client remained. The remaining client was subsequently identified as a hidden DragonBreath tab in Brave, not an orphaned worker or stale registry slot.

Heap recovered after DragonSniff disconnected:

- connected free heap: 64,860 bytes
- connected largest free block: 34,816 bytes
- stopped free heap: 76,180 bytes
- stopped largest free block: 40,960 bytes
- minimum free heap remained: 33,220 bytes

The boot ID did not change. This run produced no evidence of a DragonBreath SSE worker leak, stale client slot, generation-accounting defect, reboot, or DragonSniff connection-budget leak.

## Issue #2: churn validation

This acceptance run exercised bounded sequential SSE churn, aggressive capacity behavior, cancellation, evidence export, and delayed device-side reclamation. Results are specific to the device and firmware below.

### Device

- DragonBreath address: `192.168.1.60`
- DragonBreath firmware: `a67edce`
- DragonBreath boot ID: `3556883cad085f4cf7bcb004f2c9c3ca`
- DragonBreath SSE client cap: 2
- Baseline free heap: approximately 84,276 bytes
- Minimum free heap throughout: 31,208 bytes

### Runs

| Run | Configuration | Result |
|---|---|---|
| Basic observation | Fixed read-only JSON endpoints plus SSE | All JSON endpoints returned HTTP 200; SSE opened and delivered state and telemetry without parse errors. |
| Default churn | 3 cycles, 2 seconds, 3 events, 0.5-second delay | 3/3 connections; no rejection or failure; host cleanup complete. |
| Extended churn | 10 cycles, 5 seconds, 5 events, 0.3-second delay | 10/10 connections and 30 events; no rejection, failure, reboot, or heap stair-step. |
| Stress churn | 20 cycles, 10 seconds, 10 events, 0.1-second delay | 19 successful connections, 115 events, and one correctly preserved HTTP 503 capacity rejection; run completed without controller failure. |
| Cancellation | 20-cycle configuration stopped during cycle 3 | No later cycle started; active SSE closed with cancellation evidence; run became cancelled with cleanup complete. |

The stress run demonstrated why immediate samples and capacity rejection must remain evidence. With a 0.1-second inter-cycle delay, immediate health occasionally reported two SSE clients and cycle 14 received the product's truthful HTTP 503 `busy` response. DragonSniff did not retry indefinitely, raise concurrency, or mislabel the capacity result as a transport failure.

Manual read-only health checks after the 3-, 10-, 20-, and cancelled-run cleanup windows all reported `sse_clients: 0`. Free heap recovered to 84,276–84,292 bytes, at or slightly above the measured pre-run baseline. The minimum free heap remained 31,208 bytes and the boot ID never changed. Connected and immediate post-disconnect heap values repeated without a downward cycle-to-cycle staircase.

These runs produced no evidence of an eventual DragonBreath SSE-client leak, persistent heap loss, reboot, DragonSniff connection-budget leak, unbounded worker creation, or cancellation cleanup defect. They did show that device-visible SSE reclamation may lag local socket cleanup, motivating DragonSniff's bounded baseline-relative settlement phase without weakening the aggressive per-cycle test.

A post-implementation one-cycle physical smoke test then verified the new settlement logic itself. With a zero-client baseline and 84,284 bytes of free heap, DragonSniff completed one SSE lifecycle and reported recovery at the first one-second checkpoint: `sse_clients` returned to zero and free heap measured 84,296 bytes. Cleanup completed, all failure counters remained zero, and the boot ID stayed unchanged.
