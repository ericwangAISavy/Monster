# Control Monster from ChatGPT (MCP)

Monster can expose its tools as an MCP server so you can give commands from a ChatGPT
chat while sitting at the laptop. **Every desktop action is still approved by you in
Monster's pill on the laptop**, and **Ctrl+Alt+X** stops input at any time.

## Tools

| Tool | ChatGPT treats as | What it does |
| --- | --- | --- |
| `monster_status` | read-only | Mode, running task, whether the laptop is waiting for a review |
| `run_task(instruction)` | write | Starts a task with Monster's supervised agent (its own model, rechecks, limits and reviews) |
| `task_status(task_id, wait_seconds)` | read-only | Task state; waits up to 45 s for a change. A completed task includes `result`: what the agent reported (e.g. information read from the screen) |
| `stop()` | write | Stops the task or cancels a pending raw action |
| `screenshot(display)` | read-only | JPEG of the screen (overlays hidden) and a `screenshot_id` |
| `click`, `scroll` | write | Pixel coordinates of a specific `screenshot_id` (max 2 min old); laptop approval each time |
| `type_text`, `press_keys` | write | Into the focused window; laptop approval each time |

ChatGPT will also ask you to confirm each write tool call in the chat. That is in
addition to, not instead of, the approval on the laptop.

## Setup

1. In Monster, open **AI model & automation** and choose **Supervised real input**
   (needed for `run_task`; raw tools work in any mode).
2. Start Monster with the server:

   ```powershell
   uv run monster --remote-mcp          # port 8765; or --remote-mcp 9000
   ```

   It listens on `127.0.0.1` only. Open **Control center → ChatGPT connection…** to
   copy the URL. It looks like `http://127.0.0.1:8765/mcp/<secret>`. The secret is
   stored in `remote.json` next to Monster's settings. Delete that file to rotate it.
3. Make it reachable from ChatGPT. Use **OpenAI Secure MCP Tunnel**: it makes outbound
   connections only, opens no inbound ports, and keeps the server private.

   ```powershell
   $env:CONTROL_PLANE_API_KEY = "sk-..."   # tunnel runtime key from the OpenAI Platform
   tunnel-client init --profile monster --tunnel-id tunnel_... --mcp-server-url <URL from step 2>
   tunnel-client doctor --profile monster --explain
   tunnel-client run --profile monster
   ```

   You need a `tunnel_id` and Tunnels permissions in the OpenAI Platform, and the tunnel
   must be associated with your ChatGPT workspace. See
   <https://developers.openai.com/api/docs/guides/secure-mcp-tunnels>.

   For a different HTTPS tunnel such as Cloudflare Tunnel or ngrok, add
   `--remote-mcp-allow-host your-host.example.com` so the server accepts that Host header.
   The secret path is then the only thing protecting the endpoint, so prefer the OpenAI
   tunnel.
4. In ChatGPT (web), enable **developer mode**, then add a custom MCP server or app. Use
   the tunnel, choose **No authentication**, and accept the risk warning. Whether write
   tools are available depends on your plan and workspace settings.

## Safety notes

- Screenshots you let ChatGPT take are sent to OpenAI and can contain anything on screen.
- Text on screen or in web pages can try to steer the model (prompt injection). Read each
  approval on the laptop. The pill shows the exact click position, keys or text.
- Raw actions are bound to the latest screenshot and to the window that had focus when the
  action was requested. If the display layout or focus changes, nothing is sent.
- Remote raw input is refused on Monster's own windows (pill, control center, settings),
  so ChatGPT cannot click its way past the approval settings.
- Screenshots need no laptop approval, but the laptop briefly shows
  "ChatGPT took a screenshot" each time one is taken.
- Only one thing at a time: a raw action is refused while a task, mission or review is
  active.
