"""Verify that routine roster updates preserve unreadable on-disk data."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.e2e.telnet_client import TelnetClient
from tests.e2e.wsl_env import (
    SwripProcess,
    require_private_network,
    seed_des_legacy_fixture,
    seed_immortal_fixture,
    stage_workspace,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    require_private_network()
    world = stage_workspace("clan-roster", args.repo)
    print(f"Clan roster evidence: {world}", flush=True)

    seed_immortal_fixture(world)
    name, password = "Rosterfixture", "RosterFixture1"
    pfile = seed_des_legacy_fixture(world, name=name, password=password)
    content = pfile.read_text(encoding="utf-8")
    assert "End\n" in content
    pfile.write_text(content.replace("End\n", "Clan         The Empire~\nEnd\n", 1), encoding="utf-8")
    roster = world / "clans/empire.clan.mem"
    original_bytes = b"Version       2\nName          The Empire~\n"
    roster.write_bytes(original_bytes)
    missing_roster = world / "clans/hunters-guild.clan.mem"
    missing_roster.rename(world / "unused-hunters-fixture.mem")

    process = SwripProcess(world, binary_path=args.binary.resolve(strict=True))
    process.start()
    try:
        boot = process.wait_ready(timeout=40)
        assert boot.ready and boot.area_count == 40, boot
        assert "preserving file on disk" in process.read_boot_log()
        assert "Name          The Bounty Hunter's Guild~" in missing_roster.read_text(encoding="utf-8")
        with TelnetClient("127.0.0.1", process.port) as player:
            player.login(name, password)
            score = player.cmd("score", require_prompt=True)
            assert "The Empire" in score, score
        assert roster.read_bytes() == original_bytes

        with TelnetClient("127.0.0.1", process.port) as admin:
            admin.login("Immtest", "E2eImmPass1")
            response = admin.cmd("members create The Empire", require_prompt=True)
            assert "Existing roster data was preserved." in response, response
        assert roster.read_bytes() == original_bytes
        assert not list(roster.parent.glob("empire.clan.mem.tmp.*"))
    finally:
        shutdown = process.shutdown_clean()
        assert shutdown.clean_exit and shutdown.sanitizer_report.clean, shutdown

    print("PASS unreadable clan roster preservation and missing roster creation")


if __name__ == "__main__":
    main()
