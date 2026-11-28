"""Verify home generations, offline revocation, and storage across four boots."""

import re

from home_bounty_support import arguments, command, login, mortal, pfile, quit_player, server, stage

ROOM = 11650
FIRST_TITLE = "First Ownership Test Apartment"
SECOND_TITLE = "Second Ownership Test Apartment"


def record_generation(text, key):
    match = re.search(rf"(?m)^{key}\s+(\d+)$", text)
    return int(match[1]) if match else 0


def room_record(area):
    rooms = area.read_text(encoding="latin-1").split("#ROOMS\n", 1)[1]
    match = re.search(rf"(?ms)^#{ROOM}\n.*?(?=^#\d+\s*$)", rooms)
    assert match, f"Missing room {ROOM} in {area}"
    return match[0]


def check_grant(path, generation):
    text = path.read_text()
    assert record_generation(text, "PlrHome") == ROOM, path
    assert record_generation(text, "HomeGeneration") == generation, path
    assert "23" in re.search(r"(?m)^ActEx\s+(.+)$", text)[1].split(), path


def check_revoked(path):
    text = path.read_text()
    assert not re.search(r"(?m)^(?:PlrHome|HomeGeneration)\s", text), path
    assert "23" not in re.search(r"(?m)^ActEx\s+(.+)$", text)[1].split(), path
    assert record_generation(text, "Room") != ROOM, path


def run(repo, binary):
    world = stage(repo, "home-recovery")
    area = world / "area" / "ImpYApart.are"
    with server(repo, world, binary) as (ctx, process, admin):
        former, former_name = mortal(ctx, process, admin, "Exhome")
        current, current_name = mortal(ctx, process, admin, "Nowhome")
        former_file = pfile(world, former_name)
        current_file = pfile(world, current_name)
        command(admin, f"goto {ROOM}", "An Empty Apartment")
        command(admin, f"buyhome {FIRST_TITLE}")
        command(admin, "look", FIRST_TITLE)

        command(admin, f"transfer {current_name} {ROOM}")
        command(admin, f"addresident {current_name}", "as a resident")
        check_grant(current_file, 1)
        command(admin, f"remresident {current_name}", "as a resident")
        command(admin, f"transfer {current_name} 10300")
        command(current, "save", "Ok.")
        check_revoked(current_file)
        quit_player(current)

        command(admin, f"transfer {former_name} {ROOM}")
        command(admin, f"addresident {former_name}", "as a resident")
        command(former, "drop comlink", "You drop")
        command(former, "save", "Ok.")
        check_grant(former_file, 1)
        assert record_generation(former_file.with_suffix(".home").read_text(), "Vnum") == 10424
        quit_player(former)

    # Convert the initial purchase and grant to their pre-generation disk representation.
    room = room_record(area)
    assert record_generation(room, "G") == 1
    original = area.read_text(encoding="latin-1")
    area.write_text(original.replace(room, room.replace("G 1\n", ""), 1), encoding="latin-1")
    for path in (pfile(world, "Immtest"), former_file):
        text, count = re.subn(r"(?m)^HomeGeneration\s+1\n", "", path.read_text())
        assert count == 1, path
        path.write_text(text)
    check_grant(former_file, 0)

    with server(repo, world, binary) as (ctx, process, admin):
        former = login(ctx, process, former_name)
        command(former, "look", FIRST_TITLE)
        command(former, "save", "Ok.")
        check_grant(former_file, 0)
        quit_player(former)
        old_saved = former_file.read_bytes()

        command(admin, f"goto {ROOM}", FIRST_TITLE)
        command(admin, "sellhome", "You sell your home")
        assert record_generation(room_record(area), "G") == 1
        command(admin, f"buyhome {SECOND_TITLE}")
        command(admin, "look", SECOND_TITLE)
        assert record_generation(room_record(area), "G") == 2
        assert former_file.read_bytes() == old_saved

        current = login(ctx, process, current_name)
        command(admin, f"transfer {current_name} {ROOM}")
        command(admin, f"addresident {current_name}", "as a resident")
        check_grant(current_file, 2)
        quit_player(current)

    with server(repo, world, binary) as (ctx, process, admin):
        current = login(ctx, process, current_name)
        command(current, "look", SECOND_TITLE)
        check_grant(current_file, 2)
        command(admin, f"goto {ROOM}", SECOND_TITLE)
        if "hand-held comlink" in command(admin, "look").lower():
            command(admin, "get comlink", "You get")
        command(current, "remove vibro-blade", "You stop using")
        command(current, "drop vibro-blade", "You drop")
        command(admin, "look", "finely honed vibro-blade")
        snapshot = current_file.with_suffix(".home")
        previous_snapshot = snapshot.read_bytes()
        assert previous_snapshot.rstrip().endswith(b"#END"), snapshot
        saved_snapshot = world / "previous-current-resident.home"
        snapshot.rename(saved_snapshot)
        snapshot.mkdir()
        failed_save = command(current, "save", "Unable to save your home.")
        assert "ok." not in failed_save.lower(), failed_save
        assert snapshot.is_dir() and saved_snapshot.read_bytes() == previous_snapshot
        assert not list(snapshot.parent.glob(f"{snapshot.name}.tmp.*")), snapshot
        snapshot.rename(world / "blocked-home-destination")
        saved_snapshot.rename(snapshot)
        command(current, "save", "Ok.")
        assert snapshot.read_bytes().rstrip().endswith(b"#END"), snapshot
        quit_player(current)

        former = login(ctx, process, former_name)
        assert SECOND_TITLE.lower() not in command(former, "look").lower()
        command(former, "save", "Ok.")
        check_revoked(former_file)
        assert "hand-held comlink" not in command(admin, "look", "finely honed vibro-blade").lower()
        former.close()

    with server(repo, world, binary) as (ctx, process, admin):
        current = login(ctx, process, current_name)
        contents = command(current, "look", SECOND_TITLE)
        assert "finely honed vibro-blade" in contents.lower(), contents
        assert "hand-held comlink" not in contents.lower(), contents
        quit_player(current)


if __name__ == "__main__":
    args = arguments(__doc__)
    run(args.repo, args.binary)
    print("PASS home acquisition, resident revocation, repurchase, and storage recovery")
