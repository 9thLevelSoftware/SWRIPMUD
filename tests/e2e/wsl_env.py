"""WSL isolated build, sysroot provisioning, fixture seeding, and process lifecycle."""

from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import glob
import hashlib
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .telnet_client import TelnetClient

DEFAULT_SYSROOT = Path("/var/tmp/swrip-sysroot")
DEFAULT_SRC_ROOT = Path("/mnt/d/SWRIPMUD")

STAGE_SUBDIRS: tuple[str, ...] = (
    "src",
    "area",
    "system",
    "space",
    "planets",
    "clans",
    "boards",
    "bin",
)

RUNTIME_SUBDIRS: tuple[str, ...] = (
    "gods",
    "player",
    "corpses",
    "vendor",
    "storeroom",
    "backup",
    "guilds",
    "building",
    "mudprogs",
    "space/backup",
)

_PORT_LOCK = threading.Lock()
_CRYPT_LOCK = threading.Lock()
_LIBCRYPT: ctypes.CDLL | None = None


@dataclass(frozen=True)
class SysrootPaths:
    root: Path
    sysroot_dir: Path
    include_dir: Path
    arch_include_dir: Path
    lib_dir: Path


@dataclass
class BuildResult:
    success: bool
    returncode: int
    duration_s: float
    binary_path: Path | None
    compiled_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    warning_count: int = 0
    raw_output: str = ""


@dataclass
class BootMetrics:
    ready: bool
    port: int
    pid: int | None
    duration_s: float
    area_count: int | None = None
    bug_lines: list[str] = field(default_factory=list)
    assign_gsn_failures: list[str] = field(default_factory=list)
    init_language_failures: list[str] = field(default_factory=list)
    boot_errors: list[str] = field(default_factory=list)
    exit_code: int | None = None
    log_tail: str = ""


@dataclass
class SanitizerReport:
    clean: bool
    asan_errors: list[str] = field(default_factory=list)
    ubsan_errors: list[str] = field(default_factory=list)
    lsan_leaks: list[str] = field(default_factory=list)
    leaked_bytes: int = 0
    leaked_allocations: int = 0
    raw_logs: list[str] = field(default_factory=list)


@dataclass
class ShutdownMetrics:
    clean_exit: bool
    exit_code: int | None
    duration_s: float
    used_fallback_kill: bool
    sanitizer_report: SanitizerReport
    normal_termination: bool = False
    shutdown_output: str = ""


def _resolve_runtime_lib(candidates: Iterable[str]) -> Path:
    search_dirs = (
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/lib/x86_64-linux-gnu"),
        Path("/usr/lib"),
        Path("/lib"),
    )
    for pattern in candidates:
        for base in search_dirs:
            if not base.exists():
                continue
            direct = base / pattern
            if direct.exists():
                return direct
            matches = sorted(base.glob(pattern))
            if matches:
                return matches[0]
    raise RuntimeError(f"Could not locate runtime shared library matching {list(candidates)}")


def ensure_sysroot(sysroot_root: str | Path = DEFAULT_SYSROOT) -> SysrootPaths:
    """Idempotently provision `/var/tmp/swrip-sysroot` with zlib1g-dev + libssl-dev."""
    root = Path(sysroot_root)
    sysroot_dir = root / "sysroot"
    include_dir = sysroot_dir / "usr" / "include"
    arch_include_dir = include_dir / "x86_64-linux-gnu"
    lib_dir = root / "lib"

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root.parent / f"{root.name}.lock"

    with open(lock_path, "w", encoding="utf-8") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            ready = (
                (include_dir / "zlib.h").is_file()
                and (include_dir / "openssl" / "ssl.h").is_file()
                and (lib_dir / "libz.so").exists()
                and (lib_dir / "libssl.so").exists()
                and (lib_dir / "libcrypto.so").exists()
            )
            if not ready:
                sysroot_dir.mkdir(parents=True, exist_ok=True)
                lib_dir.mkdir(parents=True, exist_ok=True)

                with tempfile.TemporaryDirectory(dir=str(root), prefix="debs-") as deb_tmp:
                    dl = subprocess.run(
                        ["apt-get", "download", "zlib1g-dev", "libssl-dev"],
                        cwd=deb_tmp,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if dl.returncode != 0:
                        raise RuntimeError(
                            f"apt-get download failed ({dl.returncode}): {dl.stdout}\n{dl.stderr}"
                        )
                    debs = sorted(Path(deb_tmp).glob("*.deb"))
                    if not debs:
                        raise RuntimeError("apt-get download produced no .deb archives")
                    for deb in debs:
                        ex = subprocess.run(
                            ["dpkg-deb", "-x", str(deb), str(sysroot_dir)],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if ex.returncode != 0:
                            raise RuntimeError(
                                f"dpkg-deb -x {deb} failed ({ex.returncode}): {ex.stderr}"
                            )

                symlinks = {
                    "libz.so": _resolve_runtime_lib(("libz.so.1", "libz.so.*")),
                    "libssl.so": _resolve_runtime_lib(("libssl.so.3", "libssl.so.*")),
                    "libcrypto.so": _resolve_runtime_lib(("libcrypto.so.3", "libcrypto.so.*")),
                }
                for link_name, target in symlinks.items():
                    dst = lib_dir / link_name
                    if dst.exists() or dst.is_symlink():
                        dst.unlink()
                    dst.symlink_to(target)

            if not arch_include_dir.exists():
                arch_include_dir = include_dir

            return SysrootPaths(
                root=root,
                sysroot_dir=sysroot_dir,
                include_dir=include_dir,
                arch_include_dir=arch_include_dir,
                lib_dir=lib_dir,
            )
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


def require_private_network() -> None:
    """Require a non-root runtime with only a loopback network interface."""
    interfaces = {name for _, name in socket.if_nameindex()}
    if os.geteuid() == 0 or interfaces != {"lo"}:
        raise RuntimeError(
            "Runtime tests require a non-root Linux process in a private network namespace "
            "with only loopback enabled. Provision build dependencies before entering it."
        )


def _ignore_src_artifacts(directory: str, entries: list[str]) -> list[str]:
    base = Path(directory).name
    ignored: list[str] = []
    for name in entries:
        if name in {"__pycache__", ".git", ".DS_Store"}:
            ignored.append(name)
        elif base == "src" and (
            name.endswith(".o")
            or name.endswith(".d")
            or name in {"swrip", "core"}
            or name.startswith("core.")
        ):
            ignored.append(name)
        elif base == "bin" and name == "swrip":
            ignored.append(name)
    return ignored


def stage_workspace(tag: str, src_root: str | Path = DEFAULT_SRC_ROOT) -> Path:
    """Copy canonical repo assets to an isolated, uniquely generated `/var/tmp/swrip-e2e-<tag>-...` directory."""
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag).strip("._") or "run"
    src_path = Path(src_root)
    base_tmp = Path("/var/tmp")
    base_tmp.mkdir(parents=True, exist_ok=True)
    work_dir = Path(
        tempfile.mkdtemp(
            prefix=f"swrip-e2e-{safe_tag}-p{os.getpid()}-",
            dir=str(base_tmp),
        )
    )
    try:
        for subdir in STAGE_SUBDIRS:
            s = src_path / subdir
            d = work_dir / subdir
            if s.is_symlink():
                raise ValueError(f"Symlink in fixture source: {s}")
            if s.exists():
                shutil.copytree(s, d, symlinks=True, ignore=_ignore_src_artifacts)
                for path in d.rglob("*"):
                    if path.is_symlink():
                        raise ValueError(f"Symlink in staged fixture: {path.relative_to(work_dir)}")
            else:
                d.mkdir(parents=True, exist_ok=True)

        for rt in RUNTIME_SUBDIRS:
            (work_dir / rt).mkdir(parents=True, exist_ok=True)
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    return work_dir


def build_server(
    work_dir: Path,
    extra_cflags: str = "",
    jobs: int | None = None,
    sysroot_root: str | Path = DEFAULT_SYSROOT,
) -> BuildResult:
    """Compile `work_dir/src` out-of-tree using GCC 15.2 + ASan/UBSan + sysroot."""
    sysroot = ensure_sysroot(sysroot_root)
    src_dir = Path(work_dir) / "src"
    bin_dir = Path(work_dir) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    cflags_parts = [
        "-g3",
        "-Wall",
        "-Wextra",
        "-Wformat=2",
        "-Wshift-overflow=2",
        "-Wimplicit-fallthrough=2",
        "-Wno-write-strings",
        "-DMCCP",
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        "-MMD",
        "-MP",
        "-fdiagnostics-color=never",
        f"-I{sysroot.include_dir}",
        f"-I{sysroot.arch_include_dir}",
    ]
    if extra_cflags.strip():
        cflags_parts.append(extra_cflags.strip())
    c_flags = " ".join(cflags_parts)

    l_flags = (
        f"-fsanitize=address,undefined -fno-omit-frame-pointer "
        f"-L{sysroot.lib_dir} -Wl,-rpath,/usr/lib/x86_64-linux-gnu"
    )

    if jobs is None:
        env_jobs: int | None = None
        swrip_jobs = os.environ.get("SWIRP_MAKE_JOBS", "").strip()
        if swrip_jobs.isdigit():
            env_jobs = int(swrip_jobs)
        else:
            m_jobs = re.search(r"(?:^|\s)-j\s*(\d+)", os.environ.get("MAKEFLAGS", ""))
            if m_jobs:
                env_jobs = int(m_jobs.group(1))
        raw_jobs = env_jobs if env_jobs is not None else 2
        n_jobs = min(2, max(1, raw_jobs))
    else:
        n_jobs = min(2, max(1, jobs))

    build_env = os.environ.copy()
    build_env["MAKEFLAGS"] = f"-j{n_jobs}"

    cmd = [
        "make",
        "-C",
        str(src_dir),
        f"-j{n_jobs}",
        "-Otarget",
        "swrip",
        "DBUGFLG=-DMCCP",
        f"C_FLAGS={c_flags}",
        f"L_FLAGS={l_flags}",
    ]

    build_lock_path = Path("/var/tmp/swrip-wsl-build.lock")
    build_lock_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with open(build_lock_path, "w", encoding="utf-8") as build_lock_fp:
        fcntl.flock(build_lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            proc = subprocess.run(
                cmd,
                env=build_env,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            fcntl.flock(build_lock_fp.fileno(), fcntl.LOCK_UN)
    duration_s = time.monotonic() - t0
    raw_output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

    compiled_files: list[str] = []
    errors: list[str] = []
    warnings: dict[str, list[str]] = {}
    warning_count = 0

    for line in raw_output.splitlines():
        if line.startswith("g++ -c "):
            parts = line.split()
            for tok in parts:
                if tok.endswith(".c"):
                    compiled_files.append(tok)
                    break
        if ": error:" in line or ": fatal error:" in line or "undefined reference to" in line:
            errors.append(line.strip())
        elif ": warning:" in line:
            warning_count += 1
            m = re.search(r"\[-W([^\]]+)\]", line)
            cat = m.group(1) if m else "uncategorized"
            warnings.setdefault(cat, []).append(line.strip())

    src_bin = src_dir / "swrip"
    bin_out = bin_dir / "swrip"
    binary_path: Path | None = None
    if proc.returncode == 0 and src_bin.is_file():
        shutil.copy2(src_bin, bin_out)
        os.chmod(bin_out, 0o755)
        binary_path = bin_out

    return BuildResult(
        success=(proc.returncode == 0 and binary_path is not None),
        returncode=proc.returncode,
        duration_s=duration_s,
        binary_path=binary_path,
        compiled_files=compiled_files,
        errors=errors,
        warnings=warnings,
        warning_count=warning_count,
        raw_output=raw_output,
    )


def _read_proc_net_ports() -> set[int]:
    """Return local TCP ports currently in use/TIME_WAIT from `/proc/net/tcp{,6}`."""
    used: set[int] = set()
    for proc_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = proc_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            if len(parts) < 4:
                continue
            local_addr = parts[1]
            if ":" not in local_addr:
                continue
            _, hex_port = local_addr.rsplit(":", 1)
            try:
                used.add(int(hex_port, 16))
            except ValueError:
                continue
    return used


def allocate_port_block() -> int:
    """Thread- and process-safely find `p > 1024` (away from 4000..4099) where `p, p+1, p+10, p+20` are free."""
    offsets = (0, 1, 10, 20)
    # Prefer 15000..31900 (well away from impl track 4000..4099 and below Linux ephemeral 32768..60999).
    primary = list(range(15000, 31900, 30))
    secondary = list(range(10000, 14970, 30))
    idx1 = random.randint(0, len(primary) - 1)
    idx2 = random.randint(0, len(secondary) - 1)
    ordered = primary[idx1:] + primary[:idx1] + secondary[idx2:] + secondary[:idx2]

    lock_path = Path("/var/tmp/swrip-e2e-port.lock")
    lease_path = Path("/var/tmp/swrip-e2e-ports.json")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with _PORT_LOCK:
        with open(lock_path, "w", encoding="utf-8") as lock_fp:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            try:
                now = time.time()
                leases: dict[str, float] = {}
                if lease_path.is_file():
                    try:
                        raw = json.loads(lease_path.read_text(encoding="utf-8"))
                        if isinstance(raw, dict):
                            leases = {
                                str(k): float(v)
                                for k, v in raw.items()
                                if isinstance(v, (int, float)) and (now - float(v)) < 15.0
                            }
                    except (OSError, ValueError, TypeError):
                        leases = {}

                reserved_ports: set[int] = _read_proc_net_ports()
                for base_str in leases:
                    try:
                        b = int(base_str)
                        for off in offsets:
                            reserved_ports.add(b + off)
                    except ValueError:
                        pass

                for base in ordered:
                    if any(4000 <= (base + off) <= 4099 for off in offsets):
                        continue
                    if any((base + off) in reserved_ports for off in offsets):
                        continue

                    sockets: list[socket.socket] = []
                    ok = True
                    try:
                        for off in offsets:
                            port = base + off
                            # Probe loopback explicitly first, then hold 0.0.0.0 bind across all 4 offsets.
                            s_lo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            try:
                                s_lo.bind(("127.0.0.1", port))
                            finally:
                                s_lo.close()

                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sockets.append(sock)
                            sock.bind(("0.0.0.0", port))
                    except OSError:
                        ok = False
                    finally:
                        for sock in sockets:
                            try:
                                sock.close()
                            except OSError:
                                pass

                    if ok:
                        leases[str(base)] = now
                        try:
                            lease_path.write_text(json.dumps(leases), encoding="utf-8")
                        except OSError:
                            pass
                        return base
            finally:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
    raise RuntimeError("Failed to allocate a free 4-port block (p, p+1, p+10, p+20)")


def sha256_hash(password: str) -> str:
    """Return lowercase 64-char hex SHA-256 matching `src/hashstr.c:sha256_hash`."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _load_libcrypt() -> ctypes.CDLL:
    global _LIBCRYPT
    if _LIBCRYPT is not None:
        return _LIBCRYPT
    lib_name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
    lib = ctypes.CDLL(lib_name)
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    lib.crypt.restype = ctypes.c_char_p
    _LIBCRYPT = lib
    return lib


def des_crypt(password: str, salt: str = "Da") -> str:
    """Compute a 13-character traditional DES `crypt(3)` hash via `libcrypt.so.1`."""
    if len(salt) < 2:
        raise ValueError("DES salt must be at least 2 characters")
    with _CRYPT_LOCK:
        lib = _load_libcrypt()
        out = lib.crypt(password.encode("utf-8"), salt[:2].encode("ascii"))
        if not out:
            raise RuntimeError("libcrypt crypt() returned NULL")
        result = out.decode("ascii")
    if len(result) != 13:
        raise RuntimeError(f"Expected 13-char DES crypt output, got {len(result)}: {result!r}")
    return result


def _validate_character_name(name: str) -> str:
    """Validate character name against `src/comm.c:check_parse_name` rules."""
    if not isinstance(name, str) or not name:
        raise ValueError("Character name cannot be empty (see src/comm.c:check_parse_name)")
    if not (name.isascii() and name.isalpha()):
        raise ValueError(
            f"Invalid character name {name!r}: must contain only ASCII letters "
            f"(check_parse_name in src/comm.c rejects digits/symbols such as 'Imme2e'; use 'Immtest')"
        )
    if not (3 <= len(name) <= 20):
        raise ValueError(
            f"Invalid character name {name!r}: length must be 3..20 per src/comm.c:check_parse_name"
        )
    return name


def _cap_name(name: str) -> str:
    _validate_character_name(name)
    return name[0].upper() + name[1:].lower()


def seed_immortal_fixture(
    work_dir: Path,
    name: str = "Immtest",
    password: str = "E2eImmPass1",
    level: int = 110,
) -> tuple[Path, Path]:
    """Seed an immortal pfile + god file with SHA-256 auth and NO `Trust` clamp."""
    _validate_character_name(name)
    cap = _cap_name(name)
    sub = cap[0].lower()
    pdir = Path(work_dir) / "player" / sub
    gdir = Path(work_dir) / "gods"
    pdir.mkdir(parents=True, exist_ok=True)
    gdir.mkdir(parents=True, exist_ok=True)

    pfile = pdir / cap
    gfile = gdir / cap

    pwd_hash = sha256_hash(password)
    now_ts = int(time.time())

    # IMPORTANT: Do NOT write a `Trust` line! `fread_char` (`save.c:1991`) clamps
    # `Trust` to `MAX_LEVEL - 1` (109), and `get_trust(ch)` (`handler.c:302`)
    # returns `ch->trust` whenever `ch->trust != 0`.
    pfile_content = "\n".join(
        [
            "#PLAYER",
            "Version      3",
            f"Name         {cap}~",
            "Sex          1",
            "Race         0",
            "MainAbility  0",
            "Languages    -1 1",
            f"Toplevel     {level}",
            "Played       0",
            "Room         100",
            "HpManaMove   1000 1000 1000 1000 1000 1000",
            "Force        0 0 0 0",
            "Gold         10000000",
            "Bank         0",
            "Ability      0 100 0",
            "Ability      1 100 0",
            "Ability      2 100 0",
            "Ability      3 100 0",
            "Ability      4 100 0",
            "Ability      5 100 0",
            "Ability      6 100 0",
            "Ability      7 100 0",
            "Ability      8 100 0",
            "ActEx        3 6 9 10 12 -1",
            "Position     8",
            "SavingThrows 0 0 0 0 0",
            "Alignment    0",
            "Glory        0",
            "MGlory       0",
            "Hitroll      0",
            "Damroll      0",
            "Armor        100",
            "Mentalstate  0",
            f"Password     {pwd_hash}~",
            f"Lastplayed   {now_ts}",
            f"Title         {cap} the Immortal~",
            "AuthedBy     System~",
            "FlagsEx      5 -1",
            "CommandGroupEx 0 1 2 3 4 5 6 7 8 9 -1",
            "AttrPerm     14 14 14 14 14 14 14",
            "AttrMod      0 0 0 0 0 0 0",
            "Condition    0 48 48 10",
            "Site         localhost",
            "Tongue       100 'common'",
            "End",
            "",
            "#END",
            "",
        ]
    )
    pfile.write_text(pfile_content, encoding="utf-8")

    god_content = "\n".join(
        [
            f"Level        {level}",
            "PcflagsEx    -1",
            "",
        ]
    )
    gfile.write_text(god_content, encoding="utf-8")
    return pfile, gfile


def seed_des_legacy_fixture(
    work_dir: Path,
    name: str = "Legacydes",
    password: str = "LegacyPass1",
    level: int = 10,
    salt: str = "Da",
) -> Path:
    """Seed a synthetic player file using a 13-character DES `crypt(3)` password."""
    _validate_character_name(name)
    cap = _cap_name(name)
    sub = cap[0].lower()
    pdir = Path(work_dir) / "player" / sub
    pdir.mkdir(parents=True, exist_ok=True)
    pfile = pdir / cap

    des_hash = des_crypt(password, salt=salt)
    room_vnum = 100 if level >= 101 else 10300
    now_ts = int(time.time())

    lines = [
        "#PLAYER",
        "Version      3",
        f"Name         {cap}~",
        "Sex          1",
        "Race         0",
        "MainAbility  0",
        "Languages    -1 1",
        f"Toplevel     {level}",
        "Played       100",
        f"Room         {room_vnum}",
        "HpManaMove   500 500 0 0 500 500",
        "Force        0 0 0 0",
        "Gold         5000",
        "Bank         0",
        "ActEx        3 9 10 -1",
        "Position     8",
        "SavingThrows 0 0 0 0 0",
        "Alignment    0",
        "Armor        100",
        f"Password     {des_hash}~",
        f"Lastplayed   {now_ts}",
        f"Title         {cap} the Traveler~",
        "AuthedBy     professor~",
        "FlagsEx      -1",
        "AttrPerm     12 12 12 12 12 12 12",
        "AttrMod      0 0 0 0 0 0 0",
        "Condition    0 48 48 10",
        "Site         localhost",
        "Tongue       100 'common'",
        "End",
        "",
        "#END",
        "",
    ]
    pfile.write_text("\n".join(lines), encoding="utf-8")

    if level >= 101:
        gdir = Path(work_dir) / "gods"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / cap).write_text(f"Level        {level}\nPcflagsEx    -1\n", encoding="utf-8")

    return pfile


def parse_sanitizer_logs(work_dir: Path, boot_log_text: str = "", asan_prefix: str = "asan.log") -> SanitizerReport:
    """Parse `boot.log` and `asan.log*` files for ASan, UBSan, and LSan diagnostics."""
    raw_texts: list[str] = []
    if boot_log_text:
        raw_texts.append(boot_log_text)
    else:
        boot_log = Path(work_dir) / "boot.log"
        if boot_log.is_file():
            raw_texts.append(boot_log.read_text(encoding="utf-8", errors="replace"))

    for log_file in sorted(glob.glob(str(Path(work_dir) / f"{asan_prefix}*"))):
        raw_texts.append(Path(log_file).read_text(encoding="utf-8", errors="replace"))

    asan_errors: list[str] = []
    ubsan_errors: list[str] = []
    lsan_leaks: list[str] = []
    leaked_bytes = 0
    leaked_allocations = 0

    for text in raw_texts:
        for line in text.splitlines():
            if "ERROR: AddressSanitizer:" in line or "AddressSanitizer:DEADLYSIGNAL" in line:
                asan_errors.append(line.strip())
            elif "ERROR: LeakSanitizer:" in line:
                lsan_leaks.append(line.strip())
            elif "runtime error:" in line:
                ubsan_errors.append(line.strip())

            m_sum = re.search(
                r"SUMMARY: AddressSanitizer:\s*(\d+)\s+byte\(s\)\s+leaked\s+in\s+(\d+)\s+allocation\(s\)",
                line,
            )
            if m_sum:
                leaked_bytes += int(m_sum.group(1))
                leaked_allocations += int(m_sum.group(2))
            else:
                m_dir = re.search(
                    r"(?:Direct|Indirect) leak of (\d+) byte\(s\) in (\d+) object\(s\)",
                    line,
                )
                if m_dir and not any("SUMMARY: AddressSanitizer:" in ln for ln in text.splitlines()):
                    leaked_bytes += int(m_dir.group(1))
                    leaked_allocations += int(m_dir.group(2))

    clean = (
        not asan_errors
        and not ubsan_errors
        and not lsan_leaks
        and leaked_bytes == 0
        and leaked_allocations == 0
    )
    return SanitizerReport(
        clean=clean,
        asan_errors=asan_errors,
        ubsan_errors=ubsan_errors,
        lsan_leaks=lsan_leaks,
        leaked_bytes=leaked_bytes,
        leaked_allocations=leaked_allocations,
        raw_logs=raw_texts,
    )


class SwripProcess:
    """Managed isolated `swrip <port>` subprocess with ASan/UBSan/LSan capture."""

    def __init__(
        self,
        work_dir: Path,
        port: int | None = None,
        binary_path: Path | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.port = port if port is not None else allocate_port_block()
        self.binary_path = Path(binary_path) if binary_path else (self.work_dir / "bin" / "swrip")
        self._run_id = uuid.uuid4().hex
        self.boot_log_path = self.work_dir / f"boot-{self._run_id}.log"
        self._asan_prefix = f"asan-{self._run_id}.log"
        self.proc: subprocess.Popen[bytes] | None = None
        self._log_fp = None
        self.boot_metrics: BootMetrics | None = None
        self._intentionally_stopped: bool = False
        self.shutdown_metrics: ShutdownMetrics | None = None

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        require_private_network()
        if self.proc is not None:
            raise RuntimeError("Create a new SwripProcess for each server launch")
        if not self.binary_path.is_file():
            raise FileNotFoundError(f"Server binary not found: {self.binary_path}")

        self._intentionally_stopped = False

        area_dir = self.work_dir / "area"
        if not area_dir.is_dir():
            raise FileNotFoundError(f"Area directory not found: {area_dir}")

        env = os.environ.copy()
        env["ASAN_OPTIONS"] = f"log_path={self.work_dir / self._asan_prefix}:detect_leaks=1:abort_on_error=0"
        env["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=0"

        self._log_fp = open(self.boot_log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [str(self.binary_path), str(self.port)],
            cwd=str(area_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def read_boot_log(self) -> str:
        if not self.boot_log_path.is_file():
            return ""
        return self.boot_log_path.read_text(encoding="utf-8", errors="replace")

    def _parse_boot_metrics(self, text: str, ready: bool, duration_s: float) -> BootMetrics:
        pid: int | None = self.proc.pid if self.proc else None
        area_count: int | None = None
        bug_lines: list[str] = []
        assign_gsn_failures: list[str] = []
        init_language_failures: list[str] = []
        boot_errors: list[str] = []

        for line in text.splitlines():
            m_pid = re.search(r"\bPID:\s*(\d+)", line)
            if m_pid:
                pid = int(m_pid.group(1))
            if "[*****] BUG:" in line or " BUG: " in line:
                bug_lines.append(line.strip())
            if "ASSIGN_GSN" in line or "Skill " in line and "not found" in line:
                assign_gsn_failures.append(line.strip())
            if "init_language_sn" in line or "Language " in line and "not found" in line:
                init_language_failures.append(line.strip())
            if "Reading area" in line:
                area_count = (area_count or 0) + 1
            if "ERROR:" in line or "Fatal" in line or "Exiting" in line:
                boot_errors.append(line.strip())

        tail = "\n".join(text.splitlines()[-40:])
        exit_code = self.proc.poll() if self.proc else None
        return BootMetrics(
            ready=ready,
            port=self.port,
            pid=pid,
            duration_s=duration_s,
            area_count=area_count,
            bug_lines=bug_lines,
            assign_gsn_failures=assign_gsn_failures,
            init_language_failures=init_language_failures,
            boot_errors=boot_errors,
            exit_code=exit_code,
            log_tail=tail,
        )

    def wait_ready(self, timeout: float = 30.0) -> BootMetrics:
        if self.proc is None:
            raise RuntimeError("SwripProcess has not been started")

        ready_marker = f"Rise in Power ready on port {self.port}."
        t0 = time.monotonic()
        while True:
            text = self.read_boot_log()
            if ready_marker in text:
                metrics = self._parse_boot_metrics(text, ready=True, duration_s=time.monotonic() - t0)
                self.boot_metrics = metrics
                return metrics
            rc = self.proc.poll()
            if rc is not None:
                text = self.read_boot_log()
                metrics = self._parse_boot_metrics(text, ready=False, duration_s=time.monotonic() - t0)
                self.boot_metrics = metrics
                return metrics
            if time.monotonic() - t0 >= timeout:
                metrics = self._parse_boot_metrics(text, ready=False, duration_s=time.monotonic() - t0)
                self.boot_metrics = metrics
                return metrics
            time.sleep(0.1)

    def shutdown_clean(
        self,
        imm_name: str = "Immtest",
        imm_pass: str = "E2eImmPass1",
        timeout: float = 20.0,
    ) -> ShutdownMetrics:
        """Log in as seeded immortal and execute `shutdown mud now`, checking sanitizers."""
        if self.shutdown_metrics is not None:
            return self.shutdown_metrics
        t0 = time.monotonic()
        shutdown_output = ""
        used_fallback = False

        if self.proc is None:
            report = self.parse_sanitizers()
            return ShutdownMetrics(
                clean_exit=False,
                exit_code=None,
                duration_s=0.0,
                used_fallback_kill=False,
                sanitizer_report=report,
                shutdown_output="Process not started",
            )

        self._intentionally_stopped = True

        if self.proc.poll() is None:
            try:
                with TelnetClient("127.0.0.1", self.port, timeout=min(10.0, timeout)) as client:
                    client.login(imm_name, imm_pass, timeout=min(10.0, timeout), wait_for_prompt=False)
                    client.send_line("shutdown mud now")
                    shutdown_output = client.drain(seconds=0.8)
            except Exception as exc:
                shutdown_output += f"\n[shutdown_clean telnet error: {exc}]"

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.1)

        if self.proc.poll() is None:
            used_fallback = True
            self.terminate()

        exit_code = self.proc.poll()
        if self._log_fp is not None:
            try:
                self._log_fp.flush()
                self._log_fp.close()
            except OSError:
                pass
            self._log_fp = None

        report = self.parse_sanitizers()
        boot_text = self.read_boot_log()
        normal_term = "Normal termination of game." in boot_text
        clean_exit = (
            not used_fallback
            and exit_code == 0
            and normal_term
            and report.clean
        )
        self.shutdown_metrics = ShutdownMetrics(
            clean_exit=clean_exit,
            exit_code=exit_code,
            duration_s=time.monotonic() - t0,
            used_fallback_kill=used_fallback,
            sanitizer_report=report,
            normal_termination=normal_term,
            shutdown_output=shutdown_output,
        )
        return self.shutdown_metrics

    def terminate(self) -> None:
        if self.proc is None:
            return
        self._intentionally_stopped = True
        if self.proc.poll() is None:
            try:
                pgid = os.getpgid(self.proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                self.proc.terminate()

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.1)

        if self.proc.poll() is None:
            try:
                pgid = os.getpgid(self.proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                self.proc.kill()
            try:
                self.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                pass

        if self._log_fp is not None:
            try:
                self._log_fp.flush()
                self._log_fp.close()
            except OSError:
                pass
            self._log_fp = None

    def parse_sanitizers(self) -> SanitizerReport:
        if self._log_fp is not None:
            try:
                self._log_fp.flush()
            except OSError:
                pass
        return parse_sanitizer_logs(self.work_dir, self.read_boot_log(), self._asan_prefix)
