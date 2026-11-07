# V2 migration status

The user-adopted specification is [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md).
Existing companion, input worker, native Ollama configuration, supervised safety
policies and the experimental operating envelope are preserved.

| Stage | State | Required evidence |
| --- | --- | --- |
| 0 — Windows qualification | Blocked in this Linux workspace | Follow COMPUTER_TOOLS, OLLAMA and SUPERVISED_EXECUTION on disposable Windows Notepad; capture exclusion, focus, geometry/DPI, Unicode, stop/cleanup, locks, failures and timeouts need native evidence. |
| 1 — UI-Venus adapter | Portable preview foundation implemented; live qualification pending | Safe native-format parsing, explicit 0–999 conversion and provenance, immutable frame-bound candidates, fake transport and synthetic screenshot tests. Exact serving/checkpoint/projector compatibility remains unqualified. |
| 2 — Grounding benchmark | Pending | Held-out screenshots and DPI/display matrix; target GPU, checkpoint, Q5/Q6/Q8 and latency results. |
| 3 — Supervised integration | Pending, gated | Qualified model and Windows tools; versioned typed action contracts, controller/provider integration, approval binding and independent completion/recovery checks. |
| 4–8 | Pending | Routine automation, cross-app tasks, provider selection, optional consented hints, packaging/release gates in the v2 spec. |

The current model remains user-selected; UI-Venus is not downloaded, installed,
selected automatically or connected to real input by this stage. No reliability
percentage is claimed from portable tests. New preview contracts remain isolated
from current controller contracts; migrating the live action contracts to Pydantic
is stage 3 work, after native/runtime qualification, rather than a prerequisite to
parsing candidate actions safely.
