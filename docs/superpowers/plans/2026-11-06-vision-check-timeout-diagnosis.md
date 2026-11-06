# Vision-check timeout diagnosis

User confirms final failure is timeout / compatibility not confirmed, despite
connection/model-loading status. Discovery reads version/model metadata; it does
not establish that image inference completed. A whole-check timeout is not a
wrong-color score or proof of incompatibility.

Evidence: independently decode all six 96×32 generated PNG orders using Qt's PNG
reader and inspect center RGB values. Existing native payload tests verify base64
images and structured request options. Cloud access to the configured private LAN
origin returns proxy HTTP 403 Domain forbidden, so no live model diagnosis is
possible here. This is an environment limitation, not an Ollama failure result.
No demonstrated image-generator/encoding defect was found. The internal defect
was loss of stage/failure evidence, preventing attribution.

Repair, with regression-first implementation:
- Provider emits version/list/metadata/inference/validation stages; contextual
  errors distinguish unfinished HTTP/JSON from wrong synthetic-image answers.
- Report includes runtime, settings, server version, model digest/capability,
  request start, status/timing, PNG size/hash/colors and safe response metrics.
- Whole deadline identifies pending stage and stage wait. It remains inconclusive.
- Read-only in-memory report in Advanced settings; no automatic files/uploads,
  desktop screenshots, raw response bodies, model reasoning or exception messages.
- Request serial/pending job gates discard stale diagnostics. Internal exceptions
  retain only class/project stack. Existing acceptance rules, limits and inference
  configuration remain in force; no automatic retries or model changes.

Verification covers success, timeout at image inference, stale/cancelled events,
reasoning exclusion, internal exceptions and independent image decoding. Independent
review found no blockers; focused provider/settings suite passed 80 tests. Final
whole-project suite, lint and startup results are reported with delivery.

Native follow-up: update/restart, run Test vision + JSON once, open Advanced
inference settings → Last check report, select/copy it. It contains the selected model,
settings and synthetic request evidence needed for attribution without asking the
user to reconstruct context. A pending inference stage narrows the failure to an
unfinished image-request path; it does not alone prove model versus server versus
network causation. A completed invalid/wrong answer is separately recorded.

Final evidence: 270 tests passed in 37.02 seconds; ruff and diff checks passed;
offscreen startup exited 0. New request-start telemetry required the existing
run-origin test to assert start then HTTP-result events; both remain bound to the
original recorder. No live LAN model inference or native Windows behavior was
claimed verified.
