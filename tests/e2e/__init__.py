"""SWRIPMUD opaque-box End-to-End (E2E) test harness and static verifier."""

from .framework import (
    E2EContext,
    SkippedTest,
    TestCaseSpec,
    TestResult,
    get_registered_tests,
    run_tests,
    test_case,
)
from .static_scanners import run_all_scanners
from .telnet_client import TelnetClient
from .wsl_env import (
    BootMetrics,
    BuildResult,
    SanitizerReport,
    ShutdownMetrics,
    SwripProcess,
    SysrootPaths,
    allocate_port_block,
    build_server,
    des_crypt,
    ensure_sysroot,
    seed_des_legacy_fixture,
    seed_immortal_fixture,
    sha256_hash,
    stage_workspace,
)

__all__ = [
    "BootMetrics",
    "BuildResult",
    "E2EContext",
    "SanitizerReport",
    "ShutdownMetrics",
    "SkippedTest",
    "SwripProcess",
    "SysrootPaths",
    "TelnetClient",
    "TestCaseSpec",
    "TestResult",
    "allocate_port_block",
    "build_server",
    "des_crypt",
    "ensure_sysroot",
    "get_registered_tests",
    "run_all_scanners",
    "run_tests",
    "seed_des_legacy_fixture",
    "seed_immortal_fixture",
    "sha256_hash",
    "stage_workspace",
    "test_case",
]
