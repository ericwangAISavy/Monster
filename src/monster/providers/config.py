"""Non-secret provider configuration, kept separate from companion preferences."""

import ipaddress
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


PROVIDERS = {"ollama": "Ollama (local / LAN model)", "claude_code": "Claude Code (this laptop)"}
CLAUDE_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")


@dataclass(frozen=True)
class OllamaConfig:
    url: str = "http://localhost:11434"
    model: str = ""
    timeout: int = 120
    allow_lan_http: bool = False
    local_server_confirmed: bool = False
    mode: str = "demo"
    context_size: int = 16384
    max_output_tokens: int = 4096
    keep_alive: str = "5m"
    thinking: bool = False
    approve_each_step: bool = True
    hands_free: bool = False
    # Which intelligence reasons about the screen: the Ollama server above, or the
    # Claude Code CLI on this laptop (screenshots then go to Anthropic via that login).
    provider: str = "ollama"
    claude_model: str = "claude-sonnet-5-5"
    claude_code_confirmed: bool = False
    # Plan the task as subtasks first (orchestrator), then run each with the step loop.
    orchestrate: bool = False
    # Let the next planning call judge routine steps instead of a separate check call.
    merged_checks: bool = False
    # Faster Claude model for yes/no screen checks (step, completion, canvas, composer).
    claude_check_model: str = "claude-haiku-5-5"
    # Safety policies: review of sensitive/uncertain steps, draft-only (no send) tasks,
    # the shortcut allowlist and typed-content filters. Off = none of these stop a task.
    enforce_policies: bool = True
    # "lean": one model call per action (it also judges the previous action), the screen is
    # watched locally until it settles, and the orchestrator picks one subtask at a time and
    # is the independent check of each. "classic": separate step/completion checks.
    pipeline: str = "classic"

    def validate(self):
        if type(self.url) is not str or len(self.url) > 500:
            raise ValueError("Enter an Ollama server URL.")
        try:
            parsed = urlsplit(self.url)
            port = parsed.port
        except ValueError:
            raise ValueError("Invalid URL or port.") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (port is not None and port == 0)
        ):
            raise ValueError("Use an HTTP(S) server origin without credentials, path or query.")
        host = parsed.hostname
        if not host.isascii() or not re.fullmatch(r"[A-Za-z0-9.:_-]+", host):
            raise ValueError("Use a valid LAN hostname or IP address.")
        loopback = host.lower() == "localhost"
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address:
            if (
                not address.is_private
                or address.is_unspecified
                or address.is_multicast
                or address.is_link_local
            ):
                raise ValueError("Use a loopback or private LAN address, not a public/special IP.")
            loopback = address.is_loopback
        if parsed.scheme == "http" and not loopback and not self.allow_lan_http:
            raise ValueError("Confirm unencrypted HTTP on your trusted LAN, or use HTTPS.")
        if type(self.timeout) is not int or not 5 <= self.timeout <= 300:
            raise ValueError("Request timeout must be 5–300 seconds.")
        if type(self.context_size) is not int or not 512 <= self.context_size <= 131072:
            raise ValueError("Context size must be 512–131072 tokens.")
        if type(self.max_output_tokens) is not int or not 256 <= self.max_output_tokens <= 16384:
            raise ValueError("Maximum output must be 256–16384 tokens.")
        if self.keep_alive not in {"0", "1m", "5m", "15m", "30m"}:
            raise ValueError("Choose a supported keep-loaded duration.")
        if type(self.thinking) is not bool:
            raise ValueError("Thinking must be enabled or disabled explicitly.")
        if type(self.hands_free) is not bool:
            raise ValueError("Hands-free mode must be enabled or disabled explicitly.")
        if type(self.approve_each_step) is not bool:
            raise ValueError("Per-step approval must be enabled or disabled explicitly.")
        if (
            type(self.model) is not str
            or len(self.model) > 200
            or any(ord(c) < 32 for c in self.model)
        ):
            raise ValueError("Invalid model identifier.")
        if self.mode not in {"demo", "preview", "supervised"}:
            raise ValueError("Select demo, preview or supervised mode.")
        if any(type(v) is not bool for v in (self.allow_lan_http, self.local_server_confirmed)):
            raise ValueError("Provider confirmation values must be Boolean.")
        if self.provider not in PROVIDERS:
            raise ValueError("Choose Ollama or Claude Code as the intelligence provider.")
        if (type(self.claude_model) is not str
                or not re.fullmatch(r"claude-[a-z0-9.-]{1,60}", self.claude_model)):
            raise ValueError("Choose a Claude model such as claude-sonnet-5-5.")
        if self.pipeline not in {"classic", "lean"}:
            raise ValueError("Choose the lean or classic pipeline.")
        if type(self.enforce_policies) is not bool:
            raise ValueError("Safety policies must be enabled or disabled explicitly.")
        if type(self.merged_checks) is not bool:
            raise ValueError("Merged step checks must be enabled or disabled explicitly.")
        if (type(self.claude_check_model) is not str
                or not re.fullmatch(r"claude-[a-z0-9.-]{1,60}", self.claude_check_model)):
            raise ValueError("Choose a Claude check model such as claude-haiku-5-5.")
        if type(self.orchestrate) is not bool:
            raise ValueError("Task planning must be enabled or disabled explicitly.")
        if type(self.claude_code_confirmed) is not bool:
            raise ValueError("Claude Code consent must be enabled or disabled explicitly.")
        if self.mode in {"preview", "supervised"}:
            if self.provider == "claude_code":
                if not self.claude_code_confirmed:
                    raise ValueError("Confirm that screenshots are sent to Claude through "
                                     "Claude Code before using it.")
            elif not self.model or not self.local_server_confirmed:
                raise ValueError("Select a model and confirm local-only server operation for preview.")


# The saved-settings default for the Approval choice is "No approval" (hands-free). The
# dataclass default stays False so code building a config explicitly opts in.
DEFAULT_HANDS_FREE = True
DEFAULT_ORCHESTRATE = True
DEFAULT_MERGED_CHECKS = True
DEFAULT_ENFORCE_POLICIES = False  # The user's chosen default for saved settings.
DEFAULT_PIPELINE = "lean"


def load(path: Path) -> OllamaConfig:
    if not path.exists():
        return OllamaConfig(hands_free=DEFAULT_HANDS_FREE, orchestrate=DEFAULT_ORCHESTRATE,
                            merged_checks=DEFAULT_MERGED_CHECKS,
                            enforce_policies=DEFAULT_ENFORCE_POLICIES,
                            pipeline=DEFAULT_PIPELINE)
    data = json.loads(path.read_text(encoding="utf-8"))
    if type(data) is dict:
        data.setdefault("hands_free", DEFAULT_HANDS_FREE)
        data.setdefault("orchestrate", DEFAULT_ORCHESTRATE)
        data.setdefault("merged_checks", DEFAULT_MERGED_CHECKS)
        data.setdefault("enforce_policies", DEFAULT_ENFORCE_POLICIES)
        data.setdefault("pipeline", DEFAULT_PIPELINE)
    config = OllamaConfig(**data)
    config.validate()
    return config


def save(path: Path, config: OllamaConfig):
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    temporary.replace(path)
