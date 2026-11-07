"""Portable Phase6 pilots; RAM authentication is not a real encrypted VM link."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from monster.mission.runner import FakeGuiSubtaskRunner

from .contracts import WorkspaceDescriptor, new_id
from .fake_transport import FakeTransport
from .guest_service import GuestService
from .host_client import HostWorkspaceClient


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def pair_simulation(guest, *, human_verified):
    offer = guest.pairing.begin()
    return guest.pairing.confirm(
        offer.offer_id,
        code=offer.code,
        host_fingerprint=offer.host_fingerprint,
        guest_fingerprint=offer.guest_fingerprint,
        human_verified=human_verified,
    )


async def run(directory, scenario="six_stage_fake"):
    clock = FakeClock()
    descriptor = WorkspaceDescriptor(workspace_id=new_id(), display_name="Disposable simulation")
    runner = FakeGuiSubtaskRunner(
        {"pilot-a": "hang"}
        if scenario == "partition_pause"
        else {"pilot-a": "crash"}
        if scenario == "crash_reconcile"
        else None
    )
    async with GuestService(directory, descriptor, runner=runner, clock=clock) as guest:
        grant = pair_simulation(guest, human_verified=True)
        host = HostWorkspaceClient(FakeTransport(guest, grant), grant, guest.binding, clock=clock)
        await host.connect()
        if scenario != "pair_and_status":
            await host.start()
            await host.approve(human_choice="approve")
            if scenario == "partition_pause":
                await runner.started.wait()
                host.transport.disconnect()
                clock.advance(6)
                await guest.tick()
                host.connected = False
            else:
                await guest._mission_task
                await host.refresh()
        status = await guest.status()
        report = {
            "scenario": scenario,
            "provenance": "simulated",
            "native_input_transactions": 0,
            "transport": "in_memory_not_TLS",
            "guest": status.model_dump(mode="json"),
            "host": host.projection(),
            "synthetic_delegations": len(runner.calls),
        }
        if scenario == "six_stage_fake":
            assert status.state == "completed" and status.verified_stages == 6
        if scenario in ("partition_pause", "crash_reconcile"):
            assert status.unresolved
        return report


def main():
    parser = argparse.ArgumentParser(
        description="Phase6 portable guest simulation. No VM, sockets or native input."
    )
    parser.add_argument(
        "--scenario",
        choices=("pair_and_status", "partition_pause", "crash_reconcile", "six_stage_fake"),
        default="pair_and_status",
    )
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.directory:
        result = asyncio.run(run(args.directory, args.scenario))
    else:
        with tempfile.TemporaryDirectory(prefix="monster-guest-simulation-") as directory:
            result = asyncio.run(run(Path(directory), args.scenario))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
