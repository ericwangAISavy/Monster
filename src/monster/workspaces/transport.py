"""Native network integration deliberately gated; no listener or plaintext fallback.

ADR006 selects guest-initiated pinned mutual TLS on a host-only network, with
Windows-protected keys. Design review and actual native qualification are needed
before implementing/integrating that transport. FakeTransport is explicitly not TLS.
"""


def connect_native(*_args, **_kwargs):
    raise RuntimeError(
        "Native workspace transport unavailable: design review and Windows qualification required"
    )


def enable_supervised(*_args, **_kwargs):
    raise RuntimeError("Native workspace input disabled; no qualification override exists")
