# Intelligence providers: Ollama or Claude Code

Monster's reasoning (planning each step, checking each step, checking the whole task,
and the composer/canvas pre-checks) can come from one of two providers. Choose it in
**AI model & automation → Intelligence**.

| Provider | Where the model runs | Screenshots go to | Setup |
| --- | --- | --- | --- |
| **Ollama** (default) | Your local or LAN Ollama server | That server only | Server URL, installed vision model, local-only confirmation |
| **Claude Code** | Anthropic, through this laptop's Claude Code login | Anthropic | Claude Code installed and logged in, a Claude model, screenshot consent |

Both providers use the same prompts, JSON schemas and validation, and the same
controller, approval and safety rules. Only the transport differs.

## How the Claude Code provider works

`src/monster/providers/claude_code.py` subclasses the Ollama provider and replaces its
`chat()` transport. Each reasoning call runs the Claude Code CLI headless:

```
claude -p --input-format stream-json --output-format stream-json --verbose
       --json-schema <schema> --tools "" --strict-mcp-config --no-session-persistence
       --model <claude model> --effort low --system-prompt <system>
```

- The screenshot (and, for step checks, the before image) is sent as image blocks on stdin.
- `--tools ""` and `--strict-mcp-config` give the call no tools and no MCP servers, so it
  can only answer. In particular it cannot reach Monster's own MCP server.
- `--no-session-persistence` keeps nothing on disk; each call runs in an empty temporary
  directory so no project `CLAUDE.md` is loaded.
- The answer is read from the result's `structured_output`. Monster's decision schema is a
  `oneOf`, which the CLI cannot take at the top level, so it is wrapped in `{"answer": …}`
  and unwrapped again.
- Expect roughly 3–5 seconds per call with Sonnet.

The CLI is found from `$MONSTER_CLAUDE_CLI`, then `claude` on `PATH`, then the newest
Claude Code VS Code extension (`~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe`).

## Setup

1. Make sure Claude Code is logged in on this laptop (run `claude`, then `/login`).
2. Open **AI model & automation**, set **Intelligence** to **Claude Code (this laptop)**,
   pick a Claude model, and tick the screenshot consent.
3. Press **Test vision + JSON**. It sends one synthetic colour image, not your desktop.
4. Save.
