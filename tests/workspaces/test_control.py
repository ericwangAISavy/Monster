"""Portable control plane acceptance. No sockets, VMs or native input are used."""

import asyncio
import hashlib
import json

import pytest
from pydantic import ValidationError

from monster.mission.runner import FakeGuiSubtaskRunner
from monster.workspaces.contracts import (
    ApprovalResponse,
    Envelope,
    SignedFrame,
    StartMission,
    StatusRequest,
    WorkspaceDescriptor,
    new_id,
)
from monster.workspaces.fake_transport import FakeTransport
from monster.workspaces.guest_service import GuestService
from monster.workspaces.host_client import HostWorkspaceClient
from monster.workspaces.protocol import LinkError, parse, seal
from monster.workspaces.registry import ManualGuestWorkspace, native_availability
from monster.workspaces.simulation import FakeClock, pair_simulation, run
from monster.workspaces.transport import connect_native, enable_supervised


def descriptor():
    return WorkspaceDescriptor(workspace_id=new_id(), display_name="Disposable test")


async def client(guest):
    grant = pair_simulation(guest, human_verified=True)
    h = HostWorkspaceClient(FakeTransport(guest, grant), grant, guest.binding, clock=guest.clock)
    await h.connect()
    return h


def envelope(g, h, command, *, sequence=None, request_id=None):
    return Envelope(
        request_id=request_id or new_id(),
        binding=g.binding,
        sequence=sequence or h.sequence + 1,
        issued_at=g.clock(),
        expires_at=g.clock() + 3,
        command=command,
    )


def test_default_modes_and_native_gate_are_truthful(tmp_path):
    assert not native_availability()["available"]
    assert native_availability()["hyper_v_home_supported"] is False
    for operation in (connect_native, enable_supervised):
        with pytest.raises(RuntimeError):
            operation(
                qualification={
                    "phase2": "PASS",
                    "phase3": "PASS",
                    "phase4": "PASS",
                    "phase5": "PASS",
                }
            )
    registry = ManualGuestWorkspace(tmp_path / "host.json")
    assert not registry.record.guests
    d = registry.register("Manual guest")
    assert ManualGuestWorkspace(registry.path).record.guests == (d,)
    with pytest.raises(ValueError, match="one_guest"):
        registry.register("Second guest")
    assert "secret" not in registry.path.read_text().lower()
    registry.forget()
    assert not registry.record.guests


@pytest.mark.parametrize("value", ["192.168.1.5", "alice", "secret", "../path", "f" * 33])
def test_ids_cannot_embed_addresses_or_profile_names(value):
    with pytest.raises(ValidationError):
        WorkspaceDescriptor(workspace_id=value, display_name="Guest")


@pytest.mark.parametrize(
    "scenario", ["pair_and_status", "six_stage_fake", "partition_pause", "crash_reconcile"]
)
def test_required_simulation_scenarios(tmp_path, scenario):
    report = asyncio.run(run(tmp_path, scenario))
    assert report["native_input_transactions"] == 0
    assert report["provenance"] == "simulated"
    assert report["transport"] == "in_memory_not_TLS"
    if scenario == "six_stage_fake":
        assert report["guest"]["verified_stages"] == 6
        assert report["guest"]["state"] == "completed"
        assert report["synthetic_delegations"] == 6


@pytest.mark.parametrize(
    "verification", ["wrong_code", "wrong_host", "wrong_guest", "no_human", "expired"]
)
def test_pairing_needs_explicit_both_console_identity_verification(tmp_path, verification):
    async def scenario():
        clock = FakeClock()
        async with GuestService(tmp_path, descriptor(), clock=clock) as g:
            offer = g.pairing.begin()
            if verification == "expired":
                clock.advance(61)
            with pytest.raises(LinkError):
                g.pairing.confirm(
                    offer.offer_id,
                    code="000" if verification == "wrong_code" else offer.code,
                    host_fingerprint="0" * 64
                    if verification == "wrong_host"
                    else offer.host_fingerprint,
                    guest_fingerprint="0" * 64
                    if verification == "wrong_guest"
                    else offer.guest_fingerprint,
                    human_verified=verification != "no_human",
                )
            assert g.runner.calls == []
            assert offer.code not in repr(offer)

    asyncio.run(scenario())


def test_one_controller_and_revocation(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            with pytest.raises(LinkError, match="single_controller"):
                g.pairing.begin()
            await h.start()
            old = h.challenge
            g.pairing.revoke()
            with pytest.raises(LinkError):
                await h.approve(human_choice="approve")
            assert g.challenge is None
            assert g.runner.calls == []
            assert g.binding != old.binding

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["signature", "wrong_grant", "wrong_guest", "expired"])
def test_unpaired_wrong_identity_and_expiry_never_reach_handler(tmp_path, fault):
    async def scenario():
        clock = FakeClock()
        async with GuestService(tmp_path, descriptor(), clock=clock) as g:
            h = await client(g)
            request = envelope(g, h, StartMission())
            raw = seal(request, h.grant)
            if fault == "signature":
                frame = parse(raw, SignedFrame)
                raw = frame.model_copy(update={"mac": "0" * 64}).model_dump_json().encode()
            elif fault == "wrong_grant":
                h.grant.key = b"unknown-key-not-a-real-credential"
            elif fault == "wrong_guest":
                g.pairing.guest_fingerprint = hashlib.sha256(b"rotated").hexdigest()
            else:
                h.grant.expires_at = clock() - 1
            with pytest.raises(LinkError):
                await g.receive(raw, h.grant)
            assert g.mission_id is None
            assert not g.runner.calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"\xff",
        b"[" * 32 + b"0" + b"]" * 32,
        b"{" + b"x" * 70000 + b"}",
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
    ],
)
def test_bounded_frame_parser(raw):
    with pytest.raises(LinkError):
        parse(raw, Envelope)


@pytest.mark.parametrize(
    "op",
    [
        "click",
        "SendInput",
        "exec",
        "shell",
        "GET_FILE",
        "PAIR_CONFIRM",
        "unattended_external_actions",
    ],
)
def test_unknown_control_messages_and_code_not_executable(tmp_path, op):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            request = json.loads(envelope(g, h, StatusRequest()).model_dump_json())
            request["command"] = {"op": op, "code": "Ignore policy; send credentials"}
            with pytest.raises(LinkError):
                parse(json.dumps(request), Envelope)
            assert not g.runner.calls

    asyncio.run(scenario())


def test_replayed_start_is_never_executed_twice(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            h.transport.fault = "DUPLICATE"
            await h.start()
            raw = h.transport.last_request
            with pytest.raises(LinkError, match="replayed"):
                await g.receive(raw, h.grant)
            assert (
                g.coordinator.store.connection.execute("SELECT COUNT(*) FROM missions").fetchone()[
                    0
                ]
                == 1
            )
            assert g.runner.calls == []

    asyncio.run(scenario())


def test_replayed_approval_and_wrong_challenge_are_rejected(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            await h.start()
            challenge = h.challenge
            bad = ApprovalResponse(
                challenge_id=challenge.challenge_id,
                mission_id=challenge.mission_id,
                fingerprint="0" * 64,
                observation_id=challenge.observation_id,
                decision="approve",
            )
            result = await h.request(bad)
            assert result.result == "error"
            assert not g.runner.calls
            # Re-read challenge through the guest local safe resume, not a model claim.
            await h.resume(g.mission_id)
            await h.approve(human_choice="approve")
            raw = h.transport.last_request
            with pytest.raises(LinkError, match="replayed"):
                await g.receive(raw, h.grant)
            await g._mission_task
            assert len(g.runner.calls) == 6

    asyncio.run(scenario())


def test_stale_sequence_binding_cross_guest_and_observation_cannot_authorize(tmp_path):
    async def scenario():
        async with (
            GuestService(tmp_path / "a", descriptor(), clock=FakeClock()) as a,
            GuestService(tmp_path / "b", descriptor(), clock=FakeClock()) as b,
        ):
            h = await client(a)
            other = await client(b)
            request = envelope(a, h, StartMission()).model_copy(update={"binding": b.binding})
            with pytest.raises(LinkError, match="stale_binding"):
                await a.receive(seal(request, h.grant), h.grant)
            with pytest.raises(LinkError):
                await b.receive(seal(request, h.grant), other.grant)
            old = envelope(a, h, StartMission(), sequence=1)
            with pytest.raises(LinkError, match="replayed"):
                await a.receive(seal(old, h.grant), h.grant)
            assert not a.runner.calls and not b.runner.calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "event",
    [
        "SESSION_LOCK",
        "SECURE_DESKTOP",
        "DISPLAY_CHANGE",
        "PROVIDER_CHANGE",
        "TAKEOVER",
        "BLACK_CAPTURE",
        "SUSPEND_VM",
        "RESTORE_SNAPSHOT",
    ],
)
def test_session_scope_changes_invalidate_existing_approvals(tmp_path, event):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            await h.start()
            old = h.challenge
            await g.session_event(event)
            assert g.binding != old.binding
            assert g.challenge is None
            with pytest.raises(LinkError):
                await h.approve(human_choice="approve")
            assert not g.runner.calls

    asyncio.run(scenario())


def test_stop_pending_inference_and_unknown_ack_are_distinct(tmp_path):
    async def scenario():
        runner = FakeGuiSubtaskRunner({"pilot-a": "hang"})
        async with GuestService(tmp_path, descriptor(), runner=runner, clock=FakeClock()) as g:
            h = await client(g)
            await h.start()
            await h.approve(human_choice="approve")
            await runner.started.wait()
            result = await h.stop()
            assert result.result == "stop_ack"
            assert h.stop_state == "confirmed"
            assert h.status.unresolved
            assert "reconciliation" in h.projection()["message"]
            assert len(runner.calls) == 1
            assert g.active_binding is None

    asyncio.run(scenario())


def test_lost_stop_request_is_unconfirmed_and_guest_watchdog_pauses(tmp_path):
    async def scenario():
        clock = FakeClock()
        runner = FakeGuiSubtaskRunner({"pilot-a": "hang"})
        async with GuestService(tmp_path, descriptor(), runner=runner, clock=clock) as g:
            h = await client(g)
            await h.start()
            await h.approve(human_choice="approve")
            await runner.started.wait()
            h.transport.fault = "DROP"
            with pytest.raises(LinkError):
                await h.stop()
            assert h.stop_state == "unconfirmed"
            assert h.projection()["message"] == "Stop unconfirmed"
            clock.advance(6)
            await g.tick()
            assert g.active_binding is None
            assert (await g.status()).unresolved
            assert len(runner.calls) == 1

    asyncio.run(scenario())


def test_lost_stop_ack_does_not_claim_guest_cleanup(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            h.transport.fault = "DROP_ACK"
            with pytest.raises(LinkError):
                await h.stop()
            assert h.stop_state == "unconfirmed"
            assert (await g.status()).cleanup == "confirmed"
            assert h.projection()["stale"]

    asyncio.run(scenario())


@pytest.mark.parametrize("event", ["CLEANUP_UNKNOWN", "WORKER_CRASH"])
def test_unknown_cleanup_quarantines_and_refuses_new_tasks(tmp_path, event):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            await g.session_event(event)
            await h.connect()  # discovers session generation, never grants input
            assert (await g.status()).cleanup == "unknown"
            result = await h.start()
            assert result.result == "error"
            assert not g.runner.calls
            assert "Unknown cleanup" in h.projection()["message"]

    asyncio.run(scenario())


def test_guest_local_stop_has_no_host_or_link_dependency(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            await h.start()
            h.transport.disconnect()
            await g.disarm("stopped")
            assert g.challenge is None and g.active_binding is None
            assert not g.runner.calls

    asyncio.run(scenario())


def test_no_dispatch_after_expiry_even_before_watchdog_callback(tmp_path):
    async def scenario():
        clock = FakeClock()
        async with GuestService(tmp_path, descriptor(), clock=clock) as g:
            h = await client(g)
            await h.start()
            clock.advance(6)
            with pytest.raises(LinkError):
                await h.approve(human_choice="approve")
            assert not g.runner.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("advance", [-1, 60])
def test_vm_time_jump_invalidates_authority(tmp_path, advance):
    async def scenario():
        clock = FakeClock()
        async with GuestService(tmp_path, descriptor(), clock=clock) as g:
            h = await client(g)
            await h.start()
            old = g.binding
            clock.advance(advance)
            await g.tick()
            assert g.binding != old
            assert g.reason == "clock_changed"
            assert g.challenge is None

    asyncio.run(scenario())


def test_explicit_preview_consent_geometry_and_ephemeral_buffers(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            frames = h.transport.frames_sent
            with pytest.raises(LinkError, match="consent"):
                await h.request_preview(consent=False)
            assert h.transport.frames_sent == frames
            await h.request_preview(consent=True)
            frame = h.take_preview()
            assert h.take_preview() is None
            assert frame.provenance == "synthetic" and not frame.use_for_input
            assert frame.source_point(0, 0) == (-1920, 0)
            assert frame.source_point(95, 31) == (-1, 1079)
            assert not g.runner.calls
            assert "png_base64" not in "\n".join(g.coordinator.store.connection.iterdump())

    asyncio.run(scenario())


def test_guest_restart_rehydrates_without_auto_resume_or_approval_reuse(tmp_path):
    async def scenario():
        d, clock = descriptor(), FakeClock()
        async with GuestService(tmp_path, d, clock=clock) as g:
            h = await client(g)
            await h.start()
            mid, binding, old_grant = g.mission_id, g.binding, h.grant
        async with GuestService(tmp_path, d, clock=clock) as g:
            assert g.binding.guest_instance_id != binding.guest_instance_id
            assert g.binding.generation > binding.generation
            assert g.mission_id == mid
            assert not g.runner.calls
            h = await client(g)
            assert h.challenge is None
            await h.resume(mid)
            assert h.challenge is not None and not g.runner.calls
            await h.approve(human_choice="approve")
            await g._mission_task
            assert len(g.runner.calls) == 6
            assert old_grant.revoked

    asyncio.run(scenario())


def test_reconnect_rehydrates_but_never_replays_start(tmp_path):
    async def scenario():
        clock = FakeClock()
        async with GuestService(tmp_path, descriptor(), clock=clock) as g:
            h = await client(g)
            await h.start()
            mid = g.mission_id
            h.transport.disconnect()
            clock.advance(6)
            await g.tick()
            h.transport.reconnect()
            await h.connect()
            assert h.status.mission_id == mid and h.challenge is None
            assert g.runner.calls == []
            await h.resume(mid)
            await h.approve(human_choice="approve")
            await g._mission_task
            assert len(g.runner.calls) == 6

    asyncio.run(scenario())


def test_host_restart_and_cloned_guest_cannot_reuse_approval(tmp_path):
    async def scenario():
        d, clock = descriptor(), FakeClock()
        async with (
            GuestService(tmp_path / "original", d, clock=clock) as original,
            GuestService(tmp_path / "clone", d, clock=clock) as clone,
        ):
            h = await client(original)
            await h.start()
            command = envelope(original, h, StatusRequest())
            cloned_grant = pair_simulation(clone, human_verified=True)
            with pytest.raises(LinkError):
                await clone.receive(seal(command, h.grant), cloned_grant)
            assert not clone.runner.calls
            original.pairing.revoke()
            fresh_host = HostWorkspaceClient(h.transport, h.grant, h.binding, clock=clock)
            with pytest.raises(LinkError):
                await fresh_host.connect()

    asyncio.run(scenario())


def test_guest_receipt_without_verification_is_not_complete(tmp_path):
    async def scenario():
        async with GuestService(
            tmp_path,
            descriptor(),
            runner=FakeGuiSubtaskRunner({"pilot-a": "uncertain"}),
            clock=FakeClock(),
        ) as g:
            h = await client(g)
            await h.start()
            await h.approve(human_choice="approve")
            await g._mission_task
            status = await g.status()
            assert status.state != "completed" and status.unresolved
            response = await h.start()
            assert response.result == "error"
            assert len(g.runner.calls) == 1

    asyncio.run(scenario())


def test_journal_error_refuses_start_and_redacts_secret(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            g.coordinator.store.connection.execute(
                "CREATE TRIGGER fail_requests BEFORE INSERT ON guest_control_requests BEGIN SELECT RAISE(ABORT,'PASSWORD_COOKIE_CANARY'); END"
            )
            with pytest.raises(LinkError) as error:
                await h.start()
            assert "CANARY" not in str(error.value)
            assert not g.runner.calls
            assert "CANARY" not in repr(h.audit)
            g.coordinator.store.connection.execute("DROP TRIGGER fail_requests")

    asyncio.run(scenario())


def test_host_mouse_and_guest_pointer_have_no_control_plane_coupling(tmp_path, monkeypatch):
    from monster.computer import windows

    monkeypatch.setattr(
        windows, "WindowsInput", lambda: pytest.fail("No host or guest native input allowed")
    )
    host_pointer = [11, 22]

    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            host_pointer[:] = [777, 333]
            await h.start()
            await h.approve(human_choice="approve")
            await g._mission_task
            await h.request_preview(consent=True)
            assert h.take_preview().source_bounds.x == -1920
            assert host_pointer == [777, 333]
            assert len(g.runner.calls) == 6

    asyncio.run(scenario())


def test_readonly_duplicate_cache_and_stale_status_event(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            raw = h.transport.last_request
            assert await g.receive(raw, h.grant) == h.transport.last_response
            h.transport.fault = "REPLAY"
            with pytest.raises(LinkError):
                await h.refresh()
            assert h.projection()["stale"]
            assert not g.runner.calls

    asyncio.run(scenario())


def test_pairing_tokens_frames_and_secrets_not_in_durable_state_or_logs(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            await h.start()
            await h.request_preview(consent=True)
            dump = "\n".join(g.coordinator.store.connection.iterdump())
            assert h.grant.key.hex() not in dump
            assert "png_base64" not in dump and "PRIVATE" not in dump
            assert "payload" not in repr(g.audit)
            assert "key=" not in repr(h.grant)
            assert "png_base64" not in repr(h.audit)

    asyncio.run(scenario())


def test_schema5_upgrade_preserves_missions_and_boot_never_runs(tmp_path):
    from monster.mission.coordinator import MissionCoordinator
    from monster.mission.plan_templates import editor_plan
    from monster.mission.repository import Repository

    async def scenario():
        async with MissionCoordinator(tmp_path) as c:
            p = editor_plan()
            await c.start_scheduled(p)
            spec = c.store.control(p.mission.mission_id)["spec"]
            for table in (
                "guest_workspace_sessions",
                "guest_mission_bindings",
                "guest_control_requests",
                "guest_control_challenges",
            ):
                c.store.connection.execute(f"DROP TABLE {table}")
            c.store.connection.execute("PRAGMA user_version=5")
            c.store.connection.commit()
        with Repository(tmp_path) as store:
            assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 6
            assert store.control(p.mission.mission_id)["spec"] == spec

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "DROP",
        "DELAY",
        "DUPLICATE",
        "REORDER",
        "CORRUPT",
        "REPLAY",
        "DISCONNECT",
        "ROTATE_IDENTITY",
        "GUEST_REBOOT",
        "HOST_RESTART",
        "SESSION_LOCK",
        "DISPLAY_CHANGE",
        "SUSPEND_VM",
        "RESTORE_SNAPSHOT",
        "CLEANUP_UNKNOWN",
    ],
)
def test_fault_link_never_dispatches_without_human_consent(tmp_path, fault):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            h.transport.fault = fault
            if fault == "DELAY":
                h.transport.delayed.set()
            try:
                await h.start()
            except LinkError:
                pass
            assert not g.runner.calls
            assert g.active_binding is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["CRASH_BEFORE_DISPATCH", "CRASH_AFTER_INTENT", "CRASH_AFTER_RECEIPT"]
)
def test_fault_link_crash_boundaries_never_claim_completion(tmp_path, fault):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            h.transport.fault = fault
            result = await h.start()
            if fault == "CRASH_BEFORE_DISPATCH":
                assert result.result == "error"
                assert not g.runner.calls
            else:
                await h.approve(human_choice="approve")
                await g._mission_task
                assert (await g.status()).unresolved
                assert (await g.status()).state != "completed"
                assert len(g.runner.calls) == 1
                if fault == "CRASH_AFTER_RECEIPT":
                    effect = g.coordinator.store.effects(g.mission_id)[0]
                    assert effect.result.confirmed_input_transactions == 1
                    assert effect.result.evidence_ref is None

    asyncio.run(scenario())


def test_stop_priority_is_not_starved_by_delayed_noncontrol_frames(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            h.transport.fault = "DELAY"
            pending = asyncio.create_task(h.refresh())
            await asyncio.sleep(0)
            result = await h.stop()
            assert result.result == "stop_ack" and h.stop_state == "confirmed"
            h.transport.delayed.set()
            with pytest.raises(LinkError):
                await pending  # stale sequence is refused; it cannot undo stop
            assert not g.runner.calls

    asyncio.run(scenario())


def test_heartbeat_budget_reserves_stop_capacity(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            h = await client(g)
            c = g.coordinator.store.connection
            with c:
                for i in range(2, 257):
                    c.execute(
                        "INSERT INTO guest_control_requests VALUES (?,?,?,?,?)",
                        (new_id(), h.grant.grant_id, i, "HEARTBEAT", "0" * 64),
                    )
            h.sequence = 256
            result = await h.stop()
            assert result.result == "stop_ack"
            assert not g.runner.calls

    asyncio.run(scenario())


def test_no_unauthenticated_health_payload(tmp_path):
    async def scenario():
        async with GuestService(tmp_path, descriptor(), clock=FakeClock()) as g:
            with pytest.raises(LinkError, match="unpaired"):
                await g.receive(b"{}", None)
            assert not g.runner.calls

    asyncio.run(scenario())


def test_unknown_cleanup_quarantine_survives_guest_restart(tmp_path):
    async def scenario():
        d, clock = descriptor(), FakeClock()
        async with GuestService(tmp_path, d, clock=clock) as g:
            await client(g)
            await g.session_event("CLEANUP_UNKNOWN")
            assert (await g.status()).cleanup == "unknown"
        async with GuestService(tmp_path, d, clock=clock) as g:
            h = await client(g)
            assert (await g.status()).cleanup == "unknown"
            result = await h.start()
            assert result.result == "error"
            assert not g.runner.calls

    asyncio.run(scenario())
