"""Verify bounty posting and claiming after interruption at each publication boundary."""

from pathlib import Path
import hashlib
import re
import shlex
import shutil
import subprocess
import tempfile

from home_bounty_support import arguments, command, login, mortal, pfile, quit_player, server, stage

INTENT = "system/bounty-transaction"
LEDGER = "system/disintigration.lst"
CONTROL = "system/bounty-test-stop"
TRIGGER = "system/bounty-test-triggered"


def build_fault_launcher(binary, output):
    library = output / "bounty_fault.so"
    source = Path(__file__).with_name("bounty_fault.c")
    subprocess.run(
        ["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
         "-o", str(library), str(source), "-ldl"],
        check=True,
    )
    # Keep the candidate's ASan library first in the loader list when present.
    dependencies = subprocess.check_output(["ldd", str(binary)], text=True)
    asan = re.search(r"(?m)^\s*libasan\.so\.\S+\s+=>\s+(/\S+)\s+", dependencies)
    preload = f"{asan[1]}:{library}" if asan else str(library)
    launcher = output / "swrip-bounty-fault"
    launcher.write_text(
        '#!/bin/sh\n'
        f'LD_PRELOAD={shlex.quote(preload)} exec {shlex.quote(str(binary))} "$@"\n'
    )
    launcher.chmod(0o700)
    return launcher


def check_state(world, payer, target, expected_gold, expected_bounty):
    gold = re.search(r"(?m)^Gold\s+(-?\d+)$", pfile(world, payer).read_text())
    assert gold and int(gold[1]) == expected_gold, (payer, expected_gold, gold)
    tokens = (world / LEDGER).read_text().split()
    assert tokens[-1] == "$" and (len(tokens) - 1) % 3 == 0, tokens
    entries = [int(tokens[i + 1]) for i in range(0, len(tokens) - 1, 3) if tokens[i] == target]
    assert entries == ([expected_bounty] if expected_bounty else []), (target, entries)
    assert not (world / INTENT).exists(), world


def perform(op, admin, poster, target, expected=None):
    if op == "post":
        actor = poster
        line = f"addbounty {target} 5000"
    else:
        command(admin, f"transfer {target} 100")
        actor = admin
        line = f"slay {target}"

    if expected is None:
        actor.send_line(line)
    else:
        command(actor, line, expected)


def run_case(repo, binary, fault, output, source, op, boundary, poster_name, target):
    world = output / f"{op}-{boundary}"
    shutil.copytree(source, world)
    payer = poster_name if op == "post" else "Immtest"
    old_gold, old_bounty = (20000, 0) if op == "post" else (1000, 5000)
    new_gold, new_bounty = (15000, 5000) if op == "post" else (6000, 0)
    failed_save = boundary == "save-failure"
    committed = boundary not in ("intent", "save-failure")
    expected = (new_gold, new_bounty) if committed else (old_gold, old_bounty)
    control = {"player": f"player:{payer}", "save-failure": f"failplayer:{payer}"}.get(
        boundary, boundary
    )
    with server(repo, world, fault, expected_exit=0 if failed_save else 88) as (ctx, process, admin):
        poster = login(ctx, process, poster_name)
        victim = login(ctx, process, target)
        (world / CONTROL).write_text(control)
        if failed_save:
            perform(op, admin, poster, target, "pending recovery")
            actor = poster if op == "post" else admin
            command(actor, "gold", f"{old_gold} credits")
            check_state(world, payer, target, *expected)
            assert (world / TRIGGER).read_text() == control
            (world / CONTROL).rename(world / "bounty-fault-completed")
            command(actor, "save", "Ok.")
            check_state(world, payer, target, *expected)
            quit_player(poster)
            if op == "claim":
                # Claim slays the target, so its session may already be closed.
                victim.close()
            else:
                quit_player(victim)
        else:
            perform(op, admin, poster, target)
            assert process.proc.wait(timeout=15) == 88, process.read_boot_log()[-2000:]
            assert (world / TRIGGER).read_text() == control
            (world / CONTROL).rename(world / "bounty-fault-completed")

    with server(repo, world, binary):
        check_state(world, payer, target, *expected)
    ledger = (world / LEDGER).read_bytes()
    with server(repo, world, binary):
        check_state(world, payer, target, *expected)
        assert (world / LEDGER).read_bytes() == ledger, world
    print(f"PASS bounty {op} {boundary}: {payer} gold={expected[0]}, bounty={expected[1]}", flush=True)


def run(repo, binary):
    output = Path(tempfile.mkdtemp(prefix="swrip-bounty-recovery-", dir="/var/tmp"))
    print(f"Retained bounty cases: {output}", flush=True)
    print(f"Candidate SHA-256: {hashlib.sha256(binary.read_bytes()).hexdigest()}", flush=True)
    fault = build_fault_launcher(binary, output)
    base = stage(repo, "bounty-base")
    with server(repo, base, binary) as (ctx, process, admin):
        poster, poster_name = mortal(ctx, process, admin, "Bposter", gold=20000)
        victim, target = mortal(ctx, process, admin, "Btarget", gold=5000)
        for client in (poster, victim):
            command(client, "save", "Ok.")
            quit_player(client)
        command(admin, "mset Immtest credits 1000")
        command(admin, "mset Immtest clan The Bounty Hunter's Guild")
        command(admin, "save", "Ok.")
        command(admin, "score", "Bounty Hunter")
        check_state(base, poster_name, target, 20000, 0)

    claim_base = output / "claim-base"
    shutil.copytree(base, claim_base)
    with server(repo, claim_base, binary) as (ctx, process, admin):
        poster = login(ctx, process, poster_name)
        victim = login(ctx, process, target)
        perform("post", admin, poster, target, "5000 credits")
        check_state(claim_base, poster_name, target, 15000, 5000)
        quit_player(poster)
        quit_player(victim)

    for op, source in (("post", base), ("claim", claim_base)):
        for boundary in ("intent", "player", "list", "clear", "save-failure"):
            run_case(repo, binary, fault, output, source, op, boundary, poster_name, target)


if __name__ == "__main__":
    args = arguments(__doc__)
    run(args.repo, args.binary)
    print("PASS bounty interruption and pfile failure recovery")
