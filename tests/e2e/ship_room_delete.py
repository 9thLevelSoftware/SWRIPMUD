"""Verify that an empty virtual rental cockpit cannot be deleted while its ship owns it."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tests.e2e.telnet_client import TelnetClient, strip_ansi
from tests.e2e.wsl_env import SwripProcess, seed_immortal_fixture, stage_workspace


def _cmd(client: TelnetClient, command: str, timeout: float = 12.0) -> str:
    return strip_ansi(client.cmd(command, timeout=timeout, require_prompt=True))


def _landing_pads(client: TelnetClient) -> list[int]:
    systems = _cmd(client, "starsystems")
    if "no spaceobjects currently formed" in systems.lower():
        raise AssertionError(f"No space objects are available for the rental fixture:\n{systems}")

    pads: list[int] = []
    for line in systems.splitlines():
        system_name = line.strip()
        if not system_name or system_name.lower().startswith(("starsystems", "name", "-")):
            continue
        details = _cmd(client, f"showstarsystem {system_name}")
        match = re.search(r"doca:\s*(\d+)\s+\(([^)]*)\)", details, re.IGNORECASE)
        if match and int(match.group(1)) > 0 and match.group(2).strip():
            pads.append(int(match.group(1)))
    return list(dict.fromkeys(pads))


def _rentable_choice(client: TelnetClient, pads: list[int]) -> tuple[int, str, str]:
    for pad in pads:
        _cmd(client, f"goto {pad}")
        listing = _cmd(client, "rent")
        match = re.search(r"\[(\d+)\]\s+(.+?),\s+[\d,]+ credits to rent\.", listing, re.IGNORECASE)
        if match:
            return pad, match.group(1), match.group(2).strip()
    raise AssertionError(f"No supported rental was listed at landing pads {pads}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Reviewed repaired SWRIPMUD binary")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()

    binary = args.binary.resolve(strict=True)
    world = stage_workspace("ship-room-delete", args.repo)
    print(f"Ship room deletion fixture: {world}", flush=True)
    seed_immortal_fixture(world)

    process = SwripProcess(world, binary_path=binary)
    process.start()
    client: TelnetClient | None = None
    try:
        boot = process.wait_ready(timeout=45)
        assert boot.ready and boot.area_count == 40 and not boot.bug_lines, boot

        client = TelnetClient("127.0.0.1", process.port)
        client.login("Immtest", "E2eImmPass1")
        _cmd(client, "goto 100")

        pads = _landing_pads(client)
        pad, choice, template_name = _rentable_choice(client, pads)
        rent = _cmd(client, f"rent {choice}")
        assert "you rent the" in rent.lower(), f"Could not rent {template_name} at {pad}:\n{rent}"
        personal = re.search(r"board this rental with 'board ([^']+)'", rent, re.IGNORECASE)
        assert personal, f"Rental response omitted its board name:\n{rent}"
        ship_name = personal.group(1).strip()

        ship_before = _cmd(client, f"showship {ship_name}")
        cockpit_match = re.search(r"Cockpit:\s*(\d+)", ship_before, re.IGNORECASE)
        assert cockpit_match, f"Could not read virtual cockpit from ship:\n{ship_before}"
        cockpit = int(cockpit_match.group(1))
        assert cockpit >= 100000, f"Expected a virtual rental cockpit, got {cockpit}:\n{ship_before}"

        # Keep the cockpit empty: the staff character remains at the landing pad.
        refusal = _cmd(client, f"rdelete {cockpit}")
        assert "remove the room from the ship before deleting it" in refusal.lower(), (
            f"Room deletion was not refused for the ship-owned empty cockpit {cockpit}:\n{refusal}"
        )
        assert "room deleted" not in refusal.lower(), f"Cockpit deletion unexpectedly succeeded:\n{refusal}"

        ship_after = _cmd(client, f"showship {ship_name}")
        assert "no ship exists" not in ship_after.lower() and f"Cockpit: {cockpit}" in ship_after, (
            f"Rental ship did not retain its cockpit after refused deletion:\n{ship_after}"
        )
        room_after = _cmd(client, f"at {cockpit} look")
        assert "no such location" not in room_after.lower(), (
            f"Cockpit room {cockpit} disappeared after refused deletion:\n{room_after}"
        )

        # Verify ordinary use still works after the refused deletion.
        _cmd(client, f"open {ship_name}")
        boarded = _cmd(client, f"board {ship_name}")
        assert "i see no" not in boarded.lower() and "no such ship" not in boarded.lower(), (
            f"Could not board rental after refused deletion:\n{boarded}"
        )
        _cmd(client, "leave")
        ship_final = _cmd(client, f"showship {ship_name}")
        assert f"Cockpit: {cockpit}" in ship_final, f"Ship lifecycle lost its cockpit after leaving:\n{ship_final}"
    finally:
        if client is not None:
            client.close()
        shutdown = process.shutdown_clean()
        assert shutdown.clean_exit and shutdown.sanitizer_report.clean, shutdown

    print("PASS empty virtual rental cockpit remained owned and usable")


if __name__ == "__main__":
    main()
