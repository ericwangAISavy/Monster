# LAN Ollama: discovery, vision checks and action preview

This milestone connects Monster to native Ollama inference. It does **not** connect model output to real mouse/keyboard execution. The default remains the GUI demo. An explicitly enabled preview task sends one desktop screenshot to the selected local/LAN server and displays one validated proposed action. It cannot execute that proposal, open an application, or establish that a user task actually completed.

## Set up your inference server

1. Install Ollama on the inference computer and install a hardware-appropriate vision model yourself. Monster lists installed models; it never pulls weights or silently chooses a cloud model. Qwen3-VL is a candidate, not a promise of reliable GUI grounding.
2. Confirm local inference works on that host. Check RAM/VRAM and model cold-load behavior before trying desktop screenshots.
3. For access from another machine, configure Ollama's `OLLAMA_HOST` to listen on its intended private LAN interface, then restart Ollama. Default loopback binding cannot accept requests from another computer. Binding all interfaces exposes more than a private-interface binding.
4. Restrict the host firewall to the Monster computer on your trusted private network. Do not forward the port on a router or expose Ollama publicly. Native local Ollama is not authenticated merely because an SDK accepts a placeholder API key.
5. Confirm local-only inference with local weights and cloud routing disabled. The inspected Ollama version documents `OLLAMA_NO_CLOUD=1`; verify the setting and behavior for your deployed version. A local URL alone does not establish that inference stays local.

Monster uses HTTP(S) with TLS verification enabled. Plain HTTP is accepted for loopback. Other HTTP endpoints require an explicit trusted-LAN confirmation in settings. Prefer a properly trusted HTTPS setup where available. This milestone has no API-key/client-certificate configuration for authenticated reverse proxies; authentication failures are reported rather than bypassed. Proxy environment variables and CA trust are honored by httpx. Configure legitimate private-host proxy exclusions in your own environment if your organization requires them; Monster does not bypass network policy.

## Configure Monster

From the checkout:

```powershell
uv sync --frozen
uv run monster
```

Open the tray → **AI model settings…**.

1. Enter your Ollama origin, such as `http://localhost:11434` for the same machine, or your server's actual private LAN URL. The address is configurable and no example LAN IP is hardcoded.
2. For private LAN HTTP, select **Allow unencrypted HTTP on my trusted LAN**. That means screenshots will be unencrypted in transit on that LAN. URL credentials, query parameters and API paths are rejected; enter the origin only.
3. Click **Test connection / discover**. Monster reads `/api/version` and `/api/tags`. No images are sent during this operation.
4. Select an installed model. Confirm that you have checked local-only server operation and disabled cloud routing.
5. Click **Test vision + JSON**. Monster reads model metadata and sends a tiny generated image containing three colored panels. It asks the model to identify the randomized left-to-right colors in strict JSON. No desktop screenshot is used for the probe. Cold loading can take time; the request timeout is configurable from 5–300 seconds, with a separate 5-second connection timeout.
6. When the probe passes, select **Ollama screenshot/action preview · no input**, then Save. Save requires a successful probe for these exact endpoint/model/timeout/privacy settings. Editing any relevant setting invalidates the result.

Model metadata that explicitly lacks vision support blocks the probe; absent capability metadata requires the synthetic probe. Known cloud-backed models/remote model metadata are rejected in local-only mode. A passing color/JSON probe is a compatibility signal, **not** proof of accurate click coordinates, robust task understanding, or trustworthy server internals. The server operator's local-only confirmation remains necessary.

Only one secondary Monster surface is opened at a time. Model settings stay out of the everyday pill. Cancel Request or closing the settings window cancels the local HTTP operation and discards late results. API/server-side computation may continue after client cancellation.

## Try a preview task

1. Close settings and put a non-sensitive application such as a disposable Notepad document on the foreground application's display.
2. Press **Ctrl+Alt+M**, type an instruction such as “Write a short paragraph in Notepad,” and press Enter.
3. Monster collapses the pill, reruns the synthetic model compatibility check, captures the foreground application's display through the overlay-isolation gate, and sends that single screenshot plus your instruction to the configured server.
4. When inference finishes, the pill says **Preview ready · no computer actions performed**.
5. Open tray → **Last action preview…** to inspect the decision kind, summary and complete proposed action. There is intentionally no Execute button. A model `complete` response is displayed as an unverified hypothesis, never as actual task completion.

Ollama emits explicit `x_1000`/`y_1000` coordinates normalized to the full screenshot.
The adapter converts them once; source values and mapping appear in action details.
Coordinates in the canonical preview action are **image-relative physical pixels** in the selected target display's native-resolution screenshot. They are not Qt device-independent coordinates or directly executable virtual-desktop coordinates. A future engine must retain the observation's monitor origin and transforms, check freshness/focus/policy, then map coordinates before dispatch. The preview does not export or arm an InputSession.

**Ctrl+Alt+P** pauses/cancels pending inference. Resuming restarts compatibility checking and obtains a fresh screenshot; it does not replay a cached proposal. **Ctrl+Alt+X** cancels the preview and invalidates pending observation/result generations. Because no real input is connected, these GUI controls only cancel capture/inference. The separate real-input worker's independent stop mechanism is unchanged.

If you return to **GUI demo** in AI model settings, new instructions use only the simulated lifecycle again. Endpoint and model switching cancel the old controller; old responses cannot populate the new preview. No local-to-cloud fallback exists.

## Privacy and error handling

- Submitting a preview is an explicit request to transmit one screenshot and instruction to the server selected in settings. The settings explain this before enabling the mode. Use only non-sensitive displays for qualification.
- Connection checks transmit no image; vision probes transmit generated test pixels; preview tasks transmit actual display pixels. No automatic background screenshot/inference loop exists.
- The GUI hides its own companion/pill before capture and rejects capture when a Monster settings/preview window or tray menu is visible. Native captured-pixel exclusion still requires Windows validation with the chosen backend. Another process's Monster windows cannot be hidden by this instance.
- Configuration is non-secret JSON (`ollama.json`) beside `companion.json` in the per-user Qt AppConfigLocation. No model key is needed for the supported plain local/LAN Ollama listener. Malformed saved configuration falls back to the demo.
- Screenshots, instructions and the last decision remain in memory only; this feature writes no task transcript or screenshots to disk. The manually requested `--capture-test` diagnostic is a separate explicit file-save operation. The last preview may contain sensitive generated text until replaced, switched away or the app exits.
- Server logs/retention are outside Monster's control. DNS names and local/private addresses do not attest that a server is trustworthy or that it never forwards data.
- Requests reject redirects, bound response sizes, reject duplicate/non-finite JSON, ignore hidden thinking content, reject unsolicited native tool calls and fail on truncated responses. Raw HTTP bodies and raw model errors are not shown/logged. No generated code is run.
- If the server is unavailable, wrong, text-only, rate limited or incompatible, Monster reports the error without input or automatic retry. A context/image-size failure is not a reason to switch providers silently.

## Architecture

`providers/base.py` defines provider-independent descriptors, probe results, decisions and the asynchronous provider protocol. `providers/ollama.py` implements native version/tags/show/chat. `providers/decisions.py` supplies the strict provider output schema and validates normalized actions against the existing local contracts. `providers/config.py` owns separate non-secret model settings.

`ProviderJobs` runs cancellable asyncio HTTP work on a dedicated daemon thread and returns Qt signals. UI state remains on the Qt thread. `PreviewTaskController` implements the existing lifecycle port; it requests a capture and accepts it only for the active task generation. It has no import of the input client/worker and never dispatches input. `CompanionApp` wires the existing capture gate and offers intentional secondary settings/details surfaces.

The provider boundary accepts future adapters without changing local computer tools. This milestone implements only native Ollama; other provider kinds, credentials, workflow memory, continuous action loops and actual model-driven input are separate work.

## Validation

```powershell
uv run pytest -q
uv run ruff check src tests
uv run monster --smoke-test
```

Tests use `httpx.MockTransport`, generated synthetic pixels and fake screenshot/model implementations. They cover native request shapes, image encoding, metadata and probes, text-only/cloud rejection, invalid/out-of-range actions, unknown fields, URL/privacy validation, redirects/errors/timeouts/truncation/response size limits, responsive asynchronous UI, cancellation, stale results/captures, pause/resume, model settings invalidation and the companion-to-preview capture flow. No paid requests or real desktop input are performed.

The sandboxed development runner restricts local socket writes, including asyncio's inter-thread wakeup socket. Async GUI tests were run with the runner's approved local socket capability. Normal Windows/CI execution does not require a product permission flow for this Python mechanism.

**Not yet verified:** connection to your LAN server, a real installed model's probe/decision quality, server-side local-only configuration, Windows captured-pixel isolation, mixed-DPI grounding, and model latency/VRAM use. To qualify: run discovery and the probe on your server, deliberately select a text-only model, disconnect/restart the server during a task, cancel during a slow inference, switch models, inspect the saved overlay diagnostic, and compare preview coordinates with the actual screenshot. Record model digest/quantization, server version, hardware, DPI and outcomes.

After tool qualification and measured grounding pass, the next stage is a restricted supervised Notepad loop with policy/focus/freshness checks and independent input-stop integration. A successful JSON/color probe alone is not sufficient to enable autonomous execution.

## Subsequent milestone: supervised execution

The separate opt-in **Supervised real input** task mode now connects approved
model actions to the real worker. Preview mode remains non-executing. See
[Supervised execution](SUPERVISED_EXECUTION.md) before enabling the experimental
mode; native Windows qualification remains outstanding.

## Inference settings and incomplete replies

Expand **Advanced inference settings** in AI model settings to control the native
Ollama request:

| Setting | Default | Request field |
| --- | --- | --- |
| Context size | 16384 tokens | `options.num_ctx` |
| Max output tokens | 4096 | `options.num_predict` |
| Keep model loaded | `5m` | `keep_alive` |
| Enable model thinking | Off | `think: false` |

These defaults match the context/keep-loaded/thinking settings supplied from the
other application. The request timeout remains separately configurable; email
classification deadlines, email batch counts and body-length limits do not apply
to Monster's screenshot tasks. Increasing context or output limits may increase
memory use and latency. Thinking mode, when enabled and supported, also consumes
time/output budget.

Older saved Monster settings gain these defaults without losing the selected
server or model. Previously Monster hardcoded a 1,200-token output cap and omitted
an explicit thinking preference. That small budget could be exhausted before a
thinking model produced its final JSON decision. The new default raises the cap
and explicitly disables thinking. Editing any inference value invalidates the
settings probe; rerun **Test vision + JSON** before saving an inference mode.

Truncated JSON is still rejected, including a syntactically valid JSON fragment
returned with `done_reason: length`. The error now distinguishes output-token
exhaustion, server-reported errors, and a missing/false completion flag. A length
error identifies the configured limit and suggests disabling thinking or raising
Max output tokens. No mouse/keyboard transaction is automatically replayed.

If a failure reports **Confirmed input transactions: 1**, an earlier input was
accepted before the subsequent model decision failed. Inspect the desktop first;
do not blindly rerun a task that could duplicate its earlier action. Use a fresh,
disposable Notepad document for the next test. Live Gemma/Ollama behavior still
requires testing against your server; simulated-response tests do not establish
that every incomplete response is caused by a token cap.
