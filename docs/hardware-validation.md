# DragonBreath Issue #1 hardware validation

This record preserves the first physical DragonSniff acceptance result. It is evidence for DragonSniff behavior, not a general performance claim.

## Device and session

- DragonBreath firmware: `bba937b`
- DragonBreath boot ID: `b81d2613208fe5fa2c64edae51a7ca4d`
- DragonBreath SSE client cap: 2
- DragonSniff mode: one explicit target, fixed read-only endpoints

## Observed behavior

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
