# V4 slice 1: operational contract compatibility layer

**Current follow-up:** journaled mission execution now requires these contracts;
see [slice 2a and standalone slice 2b](V4_DURABLE_AUTHORIZATION.md).
[Bounded Worker slice 2c](V4_BOUNDED_WORKER.md) reuses these records for read-only
operations. The description below records the original slice 1 boundary.

## Delivered scope

`agent/contracts.py` defines strict, frozen, version-1 records for observations,
proposals, mapped actions, input receipts and model verification assessments.
`agent/contract_adapters.py` projects existing data into these records. The
supervised controller records a linked per-input contract chain in its existing
bounded run diagnostics. Actual input, policy, reviews, cleanup and completion
still follow the existing code path. No database or input-wire migration is made.

This is the compatibility/projection stage, not the shared durable authority
migration. A contract never authorizes input. Every record has
`execution_authority="none"`. Adapter refusal emits a fixed, content-free
`operational_contract_unavailable` event and does not bypass or replace the
existing policy. The next authority-consolidation slice must make its admission
and dispatch requirements explicit rather than treating this projection as a gate.

## Reuse and ownership

| Record | Reused source / meaning |
| --- | --- |
| `ObservationFrame` | Existing `computer.screenshot.Observation` and native `Bounds`; contains no image bytes |
| `ActionProposal` | Existing provider `Decision`, with a generated action/criterion ID and immutable RAM payload |
| `GroundedAction` | Existing scene-checked physical action; validates payload mapping against the originating proposal |
| `ApprovalGrant` | Alias of existing `mission.contracts.Approval`; no second grant type or permission authority |
| `ExecutionReceipt` | Existing input result, job identity and available cleanup facts; insertion is not visible success |
| `VerificationResult` | Existing per-input `Verification`, explicitly labelled `model_assessment`; not independent ground truth |

Existing `ExecutionReport` remains the post-cleanup aggregate and existing
`TaskResult` remains the mission runner result. Neither is replaced. Task-wide
completion checks and target-readiness checks retain their existing contracts.

When available, the current mission action-effect ID is carried into mapped
records, receipts and assessments. Standalone records do not invent one.

## Coordinates and context

- Provider coordinates are already mapped to native image pixels by the existing
  parser. The adapter cross-checks normalized-1000 provenance; it does not remap
  the action used for dispatch.
- The physical action must match the proposal plus the captured origin, including
  both drag endpoints. Changed payload, geometry, foreground, controller
  generation or known session/DPI/capture metadata cannot bind to that proposal.
- UI-Venus preserves its separate normalized-999 formula. Its preview adapter
  produces `preview_only=True`; the live mapped-action adapter rejects it.
- `selected_display_index` is an index in the captured topology, not a stable
  hardware identifier. Virtual-desktop captures may have no single selected index.
- Legacy capture does not report DPI scale, capture epoch or session reference.
  Those fields stay `None`. Their presence in a schema does not establish native
  detection, multi-monitor qualification, or a stronger freshness guarantee.
- Worker generation is not inferred from controller generation. Unknown worker
  generation and unknown transaction cleanup remain `None`.
- Receipt timestamps describe controller handoff and receipt delivery, not
  measured physical OS-event start/end times.

The existing scene checks, independent worker lease, generation invalidation,
exact reviews and cleanup barrier remain authoritative. Semantic target accuracy
and grounding confidence are not established by coordinate validation; calibrated
confidence is deliberately absent.

## Privacy and serialization

Payloads are immutable canonical JSON strings held only in RAM. `to_action()`
returns a fresh dictionary, including a fresh chord-key list. Mutation of that
copy cannot alter the record. Unknown operations and fields are rejected through
existing native action validation; raw down/up primitives are not added to the
provider contract.

Default Pydantic serialization excludes payload JSON, target descriptions,
expected-result text and foreground handles. Screenshots, raw model evidence and
model names are not copied into these contract exports. Model references are
hashed identifiers. A metadata-only action export intentionally cannot reconstruct
executable input; volatile content must not be silently recovered after restart.
The original bounded run report retains its existing separately controlled
content/export behavior.

Verification assessments carry action, criterion, post-observation and evidence
references plus fixed reason codes. A legacy controller expectation override is
labelled and receives a separate legacy criterion reference; it is not presented
as proof of the original proposal criterion. Model-reported success does not
manufacture a human confirmation or change mission result requirements.

## Versioning and remaining gates

Only schema version 1 is accepted, including when revalidating existing model
instances. Boolean/string versions, unknown fields and future versions fail
validation. No ledger/checkpointer, provider endpoint, policy version, permission,
mission state or primitive allowlist was changed.

Shared durable authorization now supplies actual mission/session/approval
bindings through the existing SQLite effect authority. The first bounded Worker
extraction reuses these bindings without changing their admission rules. Further
target/completion request extraction remains incremental. Windows/DPI,
physical stop/cleanup, semantic grounding and model reliability still need native
qualification and benchmark evidence. ADR006 and guest-native gates are unchanged.
