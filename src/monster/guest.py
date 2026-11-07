"""Explicit guest entry point. Native integration requires ADR006 review/qualification."""

import argparse
import json
import platform

from monster.workspaces.registry import native_availability


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Monster guest bootstrap diagnostics / portable simulation. Native input disabled."
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Read-only capability report; starts no worker or network listener",
    )
    parser.add_argument(
        "--simulate",
        choices=("pair_and_status", "partition_pause", "crash_reconcile", "six_stage_fake"),
    )
    args = parser.parse_args(argv)
    if args.diagnose:
        print(
            json.dumps(
                {
                    "platform": platform.system(),
                    **native_availability(),
                    "capture_verified": False,
                    "input_worker_started": False,
                    "listener_started": False,
                    "live_credentials_loaded": False,
                    "bootstrap": "disarmed",
                    "native_input_enabled": False,
                },
                indent=2,
            )
        )
        return 0
    if args.simulate:
        import asyncio
        import tempfile
        from pathlib import Path

        from monster.workspaces.simulation import run

        with tempfile.TemporaryDirectory(prefix="monster-guest-") as directory:
            print(json.dumps(asyncio.run(run(Path(directory), args.simulate)), indent=2))
        return 0
    parser.error(
        "Native guest operation is disabled pending design review and Windows qualification; use --diagnose or --simulate"
    )


if __name__ == "__main__":
    raise SystemExit(main())
