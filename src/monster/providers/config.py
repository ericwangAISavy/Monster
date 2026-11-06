"""Non-secret provider configuration, kept separate from companion preferences."""

import ipaddress
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class OllamaConfig:
    url: str = "http://localhost:11434"
    model: str = ""
    timeout: int = 120
    allow_lan_http: bool = False
    local_server_confirmed: bool = False
    mode: str = "demo"

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
        if self.mode in {"preview", "supervised"} and (
            not self.model or not self.local_server_confirmed
        ):
            raise ValueError("Select a model and confirm local-only server operation for preview.")


def load(path: Path) -> OllamaConfig:
    if not path.exists():
        return OllamaConfig()
    config = OllamaConfig(**json.loads(path.read_text(encoding="utf-8")))
    config.validate()
    return config


def save(path: Path, config: OllamaConfig):
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    temporary.replace(path)
