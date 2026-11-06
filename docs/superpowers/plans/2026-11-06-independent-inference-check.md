# Independent inference check

Evidence: user's 7041847 Windows report completes metadata at 0.179s then waits
59.823s at vision inference, with no HTTP result. Public Ollama 0.22.1 source
confirms non-streaming responses are withheld until generation completes, and
Gemma4 preprocessing upscales even tiny inputs toward maximum patch budget.
Neither is proof of a hardware/model defect; no demonstrated production image
encoding/validation bug is fixed here. Live LAN access remains unavailable.

Implement an explicit headless comparison without ProviderJobs or Qt event loop:
load the same saved model/config, send controlled text and image cases with/without
schema, preserve all options/timeouts and stop on first failure. Single-case mode
avoids warming other paths before reproducing the report. Share production color
prompt/schema constants without altering the request. No model switches, automatic
retries, control qualification, raw answers/reasoning/screenshots or settings writes.

Regression-first checks cover exact production request equivalence, preserved
config, schema-only paired variation, single-case fixed pixel content, first-failure
stop, unconfirmed/cloud-tag rejection, and a real loopback subprocess with immutable
saved config. Independent review found no blockers; documented warmup confound and
unattested remote aliases. CLI completion is response-envelope timing, not accuracy.

Delivery evidence: 280 tests passed in 37.90s, ruff/diff checks passed, frozen
installation audited 18 packages, application startup exited 0, independent
review's 10 focused diagnostic tests passed. Real loopback HTTP is not live LAN
Ollama or Windows inference qualification.
