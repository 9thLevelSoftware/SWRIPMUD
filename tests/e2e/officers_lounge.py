"""Check the approved Officer's Lounge route with synthetic player profiles."""

import argparse
from pathlib import Path

from .telnet_client import TelnetClient
from .wsl_env import SwripProcess, seed_immortal_fixture, stage_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    world = stage_workspace("officers-lounge", args.repo)
    print(f"Officer's Lounge evidence: {world}", flush=True)
    seed_immortal_fixture(world)
    password = "LoungeFixture1"
    for name in ("Den", "Loungeguest"):
        seed_immortal_fixture(world, name=name, password=password)

    process = SwripProcess(world, binary_path=args.binary.resolve(strict=True))
    process.start()
    try:
        boot = process.wait_ready(timeout=40)
        assert boot.ready and boot.area_count == 40 and not boot.bug_lines, boot
        with TelnetClient("127.0.0.1", process.port) as guest:
            guest.login("Loungeguest", password)
            guest.cmd("goto 101", require_prompt=True)
            look = guest.cmd("look", require_prompt=True)
            assert "Den's Den" in look, look
            guest.cmd("say Gerard MacManus", require_prompt=True)
            look = guest.cmd("look", require_prompt=True)
            assert "Den's Den" in look, look
        with TelnetClient("127.0.0.1", process.port) as den:
            den.login("Den", password)
            den.cmd("goto 101", require_prompt=True)
            look = den.cmd("look", require_prompt=True)
            assert "Den's Den" in look, look
            den.cmd("say Gerard MacManus", require_prompt=True)
            look = den.cmd("look", require_prompt=True)
            assert "Officer's Lounge" in look, look
            den.cmd("north", require_prompt=True)
            look = den.cmd("look", require_prompt=True)
            assert "Den's Den" in look, look
    finally:
        shutdown = process.shutdown_clean()
        assert shutdown.clean_exit and shutdown.sanitizer_report.clean, shutdown
    print("PASS Officer's Lounge admission and return route")


if __name__ == "__main__":
    main()
