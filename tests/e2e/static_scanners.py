"""Reproducible static audit verifiers for SWRIPMUD (Scanners 1..5).

Reproduces all 5 canonical audit invariants from `docs/audit/scanners/*.json`:
  1. allocator_mismatch  (allocator_mismatch_audit)
  2. flag_domain         (flag_domain_audit)
  3. command_skill_wiring (command_skill_wiring_audit)
  4. const_and_format    (const_and_format_audit)
  5. persistence_and_vnum (persistence_and_vnum_audit)

Supports `--mode baseline` (reproducing baseline audit counts) and
`--mode post-remediation` (enforcing strict 0-violation active-source + asset gates).
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared Lexer, Preprocessor & Balanced Call-Argument Parser
# ---------------------------------------------------------------------------

ALWAYS_FALSE_MACROS: set[str] = {
    "0",
    "NOTDEFD",
    "OLDMAPS",
    "UNDEFD",
    "WIN32",
    "_WIN32_FALSE",
    "macintosh",
    "TRADITIONAL",
    "_AIX",
    "apollo",
    "hpux",
    "interactive",
    "MIPS_OS",
    "NeXT",
    "sequent",
    "sun",
    "SYSV",
    "ultrix",
    "NODEATH",
    "NODEATHSHIP",
    "DNS_SLAVE",
    "MALLOC_DEBUG",
    "LOGTELNET",
    "DUNNO_STRSTR",
    "DEBUG",
    "REQUESTS",
}

RETIRED_DO_MAKE_NAMES: tuple[str, ...] = (
    "do_makearmor",
    "do_makeblade",
    "do_makeblaster",
    "do_makebowcaster",
    "do_makecomlink",
    "do_makecontainer",
    "do_makedisguise",
    "do_makefurniture",
    "do_makegrenade",
    "do_makejewelry",
    "do_makelandmine",
    "do_makelight",
    "do_makelightsaber",
    "do_makemedpac",
    "do_makemissile",
    "do_makeshield",
    "do_makespice",
)

DEAD_FILES_TO_REMOVE: tuple[str, ...] = (
    "src/newarena.c",
    "src/smalloc.c",
    "src/requests.c",
    "src/objflags",
)


@dataclass
class MakefileInfo:
    c_files: set[str]
    ordered_c_files: list[str]
    h_files: list[str]
    all_flags: str
    has_d_mccp: bool
    has_d_requests: bool


@dataclass
class SourceFile:
    rel_path: str
    path: Path
    in_makefile: bool
    is_header: bool
    raw: str
    comment_stripped: str
    active_text: str
    newline_offsets: list[int]


def make_newline_offsets(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch == "\n"]


def line_of_offset(offsets: list[int], idx: int) -> int:
    return bisect.bisect_left(offsets, idx) + 1


def parse_makefile(repo: Path) -> MakefileInfo:
    mk_path = repo / "src" / "Makefile"
    if not mk_path.is_file():
        c_paths = sorted((repo / "src").glob("*.c"))
        h_paths = sorted((repo / "src").glob("*.h"))
        return MakefileInfo(
            c_files={p.name for p in c_paths},
            ordered_c_files=[p.name for p in c_paths],
            h_files=[p.name for p in h_paths],
            all_flags="",
            has_d_mccp=False,
            has_d_requests=False,
        )

    raw = mk_path.read_text(encoding="utf-8", errors="replace")
    joined = re.sub(r"\\\r?\n", " ", raw)

    c_files: list[str] = []
    flag_lines: list[str] = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m_c = re.match(r"^C_FILES\s*=\s*(.*)$", stripped)
        if m_c:
            c_files = [tok for tok in m_c.group(1).split() if tok.endswith(".c")]
        if re.match(r"^(?:DBUGFLG|C_FLAGS|CFLAGS|L_FLAGS|PROF|NOCRYPT)\s*[+:?]?=", stripped):
            flag_lines.append(stripped)

    all_flags = "\n".join(flag_lines)
    h_files = sorted(p.name for p in (repo / "src").glob("*.h"))
    return MakefileInfo(
        c_files=set(c_files),
        ordered_c_files=c_files,
        h_files=h_files,
        all_flags=all_flags,
        has_d_mccp=bool(re.search(r"-DMCCP\b", all_flags)),
        has_d_requests=bool(re.search(r"-DREQUESTS\b", all_flags)),
    )


def strip_comments(text: str) -> str:
    """Strip C/C++ `//` and `/* */` comments preserving exact 1-based line numbers."""
    out: list[str] = []
    n = len(text)
    i = 0
    state = "NORMAL"

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if state == "NORMAL":
            if ch == "/" and nxt == "/":
                out.append("  ")
                i += 2
                state = "LINE_COMMENT"
            elif ch == "/" and nxt == "*":
                out.append("  ")
                i += 2
                state = "BLOCK_COMMENT"
            elif ch == '"':
                out.append(ch)
                i += 1
                state = "STRING"
            elif ch == "'":
                out.append(ch)
                i += 1
                state = "CHAR"
            else:
                out.append(ch)
                i += 1
        elif state == "LINE_COMMENT":
            if ch == "\n":
                out.append("\n")
                state = "NORMAL"
            else:
                out.append(" ")
            i += 1
        elif state == "BLOCK_COMMENT":
            if ch == "*" and nxt == "/":
                out.append("  ")
                i += 2
                state = "NORMAL"
            elif ch == "\n":
                out.append("\n")
                i += 1
            else:
                out.append(" ")
                i += 1
        elif state == "STRING":
            out.append(ch)
            i += 1
            if ch == "\\" and i < n:
                out.append(text[i])
                i += 1
            elif ch == '"':
                state = "NORMAL"
        elif state == "CHAR":
            out.append(ch)
            i += 1
            if ch == "\\" and i < n:
                out.append(text[i])
                i += 1
            elif ch == "'":
                state = "NORMAL"

    return "".join(out)


def mask_string_and_char_literals(text: str) -> str:
    """Replace string and char literal contents with spaces, preserving delimiters, newlines, and offsets."""
    out: list[str] = []
    n = len(text)
    i = 0
    state = "NORMAL"

    while i < n:
        ch = text[i]
        if state == "NORMAL":
            out.append(ch)
            if ch == '"':
                state = "STRING"
            elif ch == "'":
                state = "CHAR"
            i += 1
        elif state == "STRING":
            if ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                out.append(" ")
                out.append("\n" if nxt == "\n" else " ")
                i += 2
            elif ch == '"':
                out.append('"')
                state = "NORMAL"
                i += 1
            elif ch == "\n":
                out.append("\n")
                i += 1
            else:
                out.append(" ")
                i += 1
        elif state == "CHAR":
            if ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                out.append(" ")
                out.append("\n" if nxt == "\n" else " ")
                i += 2
            elif ch == "'":
                out.append("'")
                state = "NORMAL"
                i += 1
            elif ch == "\n":
                out.append("\n")
                i += 1
            else:
                out.append(" ")
                i += 1

    return "".join(out)


def _eval_pp_expr(expr: str, active_defines: set[str], always_false: set[str]) -> bool:
    expr = expr.strip()
    if not expr:
        return False
    if expr == "0":
        return False
    if expr == "1":
        return True

    m_not_def = re.match(r"^!\s*defined\s*\(\s*([A-Za-z_]\w*)\s*\)$", expr)
    if m_not_def:
        sym = m_not_def.group(1)
        if sym in active_defines:
            return False
        return True

    m_def = re.match(r"^defined\s*\(\s*([A-Za-z_]\w*)\s*\)$", expr)
    if m_def:
        sym = m_def.group(1)
        if sym in always_false:
            return False
        if sym in active_defines:
            return True
        return True

    # Compound || or &&
    if "&&" in expr:
        return all(_eval_pp_expr(p, active_defines, always_false) for p in expr.split("&&"))
    if "||" in expr:
        return any(_eval_pp_expr(p, active_defines, always_false) for p in expr.split("||"))

    if re.fullmatch(r"[A-Za-z_]\w*", expr):
        if expr in always_false:
            return False
        return True
    return True


def strip_inactive_ifdefs(
    comment_stripped: str,
    active_defines: set[str],
    always_false: set[str],
) -> str:
    """Blank lines inside inactive `#if`/`#ifdef`/`#ifndef`/`#else` branches."""
    raw_parts = comment_stripped.split("\n")
    lines = [p + "\n" for p in raw_parts[:-1]] + ([raw_parts[-1]] if raw_parts[-1] else [])
    # Stack entries: [parent_active, branch_taken, cur_active]
    stack: list[list[bool]] = []
    out: list[str] = []

    for line in lines:
        s = line.lstrip()
        if s.startswith("#"):
            directive = s[1:].strip()
            m_ifdef = re.match(r"^ifdef\s+([A-Za-z0-9_]+)", directive)
            m_ifndef = re.match(r"^ifndef\s+([A-Za-z0-9_]+)", directive)
            m_if = re.match(r"^if\s+(.+)$", directive)
            m_elif = re.match(r"^elif\s+(.+)$", directive)
            is_else = bool(re.match(r"^else\b", directive))
            is_endif = bool(re.match(r"^endif\b", directive))

            parent_active = stack[-1][2] if stack else True

            if m_ifdef:
                sym = m_ifdef.group(1)
                cond = (sym not in always_false) if sym not in active_defines else True
                if sym in always_false and sym not in active_defines:
                    cond = False
                cur = parent_active and cond
                stack.append([parent_active, cond, cur])
                out.append(line if parent_active else ("\n" if line.endswith("\n") else ""))
                continue
            if m_ifndef:
                sym = m_ifndef.group(1)
                cond = sym not in active_defines
                cur = parent_active and cond
                stack.append([parent_active, cond, cur])
                out.append(line if parent_active else ("\n" if line.endswith("\n") else ""))
                continue
            if m_if:
                cond = _eval_pp_expr(m_if.group(1), active_defines, always_false)
                cur = parent_active and cond
                stack.append([parent_active, cond, cur])
                out.append(line if parent_active else ("\n" if line.endswith("\n") else ""))
                continue
            if m_elif and stack:
                p_act, taken, _ = stack[-1]
                cond = (not taken) and _eval_pp_expr(m_elif.group(1), active_defines, always_false)
                stack[-1][1] = taken or cond
                stack[-1][2] = p_act and cond
                out.append(line if p_act else ("\n" if line.endswith("\n") else ""))
                continue
            if is_else and stack:
                p_act, taken, _ = stack[-1]
                cur = p_act and (not taken)
                stack[-1][1] = True
                stack[-1][2] = cur
                out.append(line if p_act else ("\n" if line.endswith("\n") else ""))
                continue
            if is_endif and stack:
                p_act = stack[-1][0]
                stack.pop()
                out.append(line if p_act else ("\n" if line.endswith("\n") else ""))
                continue

        cur_active = stack[-1][2] if stack else True
        if cur_active:
            out.append(line)
        else:
            out.append("\n" if line.endswith("\n") else "")

    return "".join(out)


def extract_call_args(text: str, open_paren_idx: int) -> tuple[list[str], int]:
    """Split C++ function/macro arguments starting at `text[open_paren_idx] == '('`."""
    n = len(text)
    assert text[open_paren_idx] == "("
    depth_paren = 1
    depth_bracket = 0
    depth_brace = 0
    i = open_paren_idx + 1
    arg_start = i
    args: list[str] = []
    state = "NORMAL"

    while i < n:
        ch = text[i]
        if state == "NORMAL":
            if ch == '"':
                state = "STRING"
            elif ch == "'":
                state = "CHAR"
            elif ch == "(":
                depth_paren += 1
            elif ch == ")":
                depth_paren -= 1
                if depth_paren == 0:
                    tail = text[arg_start:i].strip()
                    if tail or args:
                        args.append(tail)
                    return args, i
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket = max(0, depth_bracket - 1)
            elif ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace = max(0, depth_brace - 1)
            elif ch == "," and depth_paren == 1 and depth_bracket == 0 and depth_brace == 0:
                args.append(text[arg_start:i].strip())
                arg_start = i + 1
        elif state == "STRING":
            if ch == "\\" and i + 1 < n:
                i += 1
            elif ch == '"':
                state = "NORMAL"
        elif state == "CHAR":
            if ch == "\\" and i + 1 < n:
                i += 1
            elif ch == "'":
                state = "NORMAL"
        i += 1

    return args, n - 1


def find_matching_brace(text: str, open_brace_idx: int) -> int:
    n = len(text)
    depth = 0
    i = open_brace_idx
    state = "NORMAL"
    while i < n:
        ch = text[i]
        if state == "NORMAL":
            if ch == '"':
                state = "STRING"
            elif ch == "'":
                state = "CHAR"
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        elif state == "STRING":
            if ch == "\\" and i + 1 < n:
                i += 1
            elif ch == '"':
                state = "NORMAL"
        elif state == "CHAR":
            if ch == "\\" and i + 1 < n:
                i += 1
            elif ch == "'":
                state = "NORMAL"
        i += 1
    return n - 1


def load_source_corpus(repo: Path, mode: str) -> tuple[MakefileInfo, list[SourceFile], set[str]]:
    mk = parse_makefile(repo)
    active_defines: set[str] = {"OPROG_ACT_TRIGGER", "RPROG_ACT_TRIGGER"}
    always_false = set(ALWAYS_FALSE_MACROS)
    if mk.has_d_requests:
        always_false.discard("REQUESTS")
        active_defines.add("REQUESTS")
    if mk.has_d_mccp:
        active_defines.add("MCCP")

    mud_h_path = repo / "src" / "mud.h"
    if mud_h_path.is_file():
        mud_h_stripped = strip_comments(mud_h_path.read_text(encoding="utf-8", errors="replace"))
        if re.search(r"^\s*#\s*define\s+HASHSTR\b", mud_h_stripped, re.MULTILINE):
            active_defines.add("HASHSTR")
        if re.search(r"^\s*#\s*define\s+MCCP\b", mud_h_stripped, re.MULTILINE):
            active_defines.add("MCCP")

    files: list[SourceFile] = []
    src_dir = repo / "src"
    all_paths = sorted(list(src_dir.glob("*.h")) + list(src_dir.glob("*.c")))
    for p in all_paths:
        is_header = p.suffix == ".h"
        in_mk = is_header or (p.name in mk.c_files)
        raw = p.read_text(encoding="utf-8", errors="replace")
        cs = strip_comments(raw)
        active = strip_inactive_ifdefs(cs, active_defines, always_false)
        files.append(
            SourceFile(
                rel_path=f"src/{p.name}",
                path=p,
                in_makefile=in_mk,
                is_header=is_header,
                raw=raw,
                comment_stripped=cs,
                active_text=active,
                newline_offsets=make_newline_offsets(active),
            )
        )
    return mk, files, active_defines


# ---------------------------------------------------------------------------
# Shared C++ Struct & Typedef Parser (used by Scanners 1, 2, 4)
# ---------------------------------------------------------------------------

NON_POD_MARKERS = (
    "std::string",
    "std::vector",
    "std::map",
    "std::unordered_map",
    "std::set",
    "std::shared_ptr",
    "std::unique_ptr",
    "std::function",
    "BitSet",
    "FLAG_SET",
)


@dataclass
class StructDB:
    structs: dict[str, dict[str, str]] = field(default_factory=dict)
    alias_to_struct: dict[str, str] = field(default_factory=dict)
    non_pod_structs: dict[str, list[str]] = field(default_factory=dict)


def _strip_decl_name_and_type(stmt: str) -> list[tuple[str, str]]:
    """Parse a struct member declaration statement like `CHAR_DATA *next, *prev;`."""
    stmt = stmt.strip()
    if not stmt or stmt.startswith("#"):
        return []
    # Skip member functions / constructors / destructors / access specifiers
    if "(" in stmt or stmt.startswith(("public:", "private:", "protected:", "friend ", "using ", "typedef ")):
        return []
    # Remove bitfield widths and default initializers
    stmt_clean = re.sub(r"\s*=\s*[^,;]+", "", stmt)
    stmt_clean = re.sub(r"\s*:\s*\d+", "", stmt_clean)
    parts = [p.strip() for p in stmt_clean.split(",") if p.strip()]
    if not parts:
        return []

    first = parts[0]
    m = re.match(r"^(.*?)([*&]*\s*[A-Za-z_]\w*)(?:\s*\[[^\]]*\])*$", first)
    if not m:
        return []
    base_type = m.group(1).strip()
    # Clean qualifiers
    if not base_type:
        return []
    res: list[tuple[str, str]] = []
    first_decl = m.group(2).strip()
    ptr_prefix = "*" * first_decl.count("*")
    name0 = re.sub(r"[*&\s]+", "", first_decl)
    is_array0 = "[" in first
    t0 = (base_type + (" *" if ptr_prefix else "") + (" []" if is_array0 else "")).strip()
    res.append((name0, t0))

    for p in parts[1:]:
        m_p = re.match(r"^([*&]*)\s*([A-Za-z_]\w*)(?:\s*\[[^\]]*\])*$", p)
        if m_p:
            stars = "*" * m_p.group(1).count("*")
            is_arr = "[" in p
            tp = (base_type + (" *" if stars else "") + (" []" if is_arr else "")).strip()
            res.append((m_p.group(2), tp))
    return res


def parse_struct_db(files: list[SourceFile]) -> StructDB:
    db = StructDB()
    # Built-in / common aliases
    db.alias_to_struct.update(
        {
            "FLAG_SET": "BitSet",
            "RID": "room_index_data",
            "OID": "obj_index_data",
            "MID": "mob_index_data",
            "ED": "exit_data",
            "EDD": "extra_descr_data",
            "RD": "reset_data",
            "CD": "char_data",
            "OD": "obj_data",
            "MPROG_DATA": "mob_prog_data",
            "MPROG_ACT_LIST": "mob_prog_act_list",
        }
    )

    for sf in files:
        if not sf.in_makefile:
            continue
        text = sf.active_text
        # typedef struct tag ALIAS;
        for m in re.finditer(r"\btypedef\s+struct\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;", text):
            db.alias_to_struct[m.group(2)] = m.group(1)

        # struct Name [: Base] { ... } [Alias];
        for m in re.finditer(
            r"\b(?:typedef\s+)?(struct|class)\s+([A-Za-z_]\w*)?(?:\s*:\s*[^{;]+)?\s*\{",
            text,
        ):
            name = m.group(2)
            open_idx = m.end() - 1
            close_idx = find_matching_brace(text, open_idx)
            # Check trailing typedef alias after '}'
            tail = text[close_idx + 1 : close_idx + 120]
            m_alias = re.match(r"^\s*([A-Za-z_]\w*)\s*;", tail)
            if not name and m_alias:
                name = m_alias.group(1)
            if not name:
                continue
            if m_alias:
                db.alias_to_struct[m_alias.group(1)] = name

            body = text[open_idx + 1 : close_idx]
            fields: dict[str, str] = db.structs.setdefault(name, {})
            # Extract top-level statements inside struct body (depth 0 relative to body)
            depth = 0
            stmt_start = 0
            i = 0
            bn = len(body)
            while i < bn:
                c = body[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth = max(0, depth - 1)
                elif c == ";" and depth == 0:
                    stmt = body[stmt_start:i]
                    for fname, ftype in _strip_decl_name_and_type(stmt):
                        fields[fname] = ftype
                    stmt_start = i + 1
                i += 1

    # Fixed-point non-POD propagation
    non_pod: dict[str, list[str]] = {}
    for sname, fields in db.structs.items():
        reasons: list[str] = []
        for fname, ftype in fields.items():
            if any(tok in ftype for tok in NON_POD_MARKERS) and "*" not in ftype:
                reasons.append(f"{ftype} {fname}")
        if reasons:
            non_pod[sname] = reasons

    changed = True
    while changed:
        changed = False
        for sname, fields in db.structs.items():
            for fname, ftype in fields.items():
                if "*" in ftype:
                    continue
                clean_t = re.sub(r"\b(?:const|struct|class|mutable)\b", "", ftype).replace("[]", "").strip()
                resolved = db.alias_to_struct.get(clean_t, clean_t)
                if resolved in non_pod:
                    reason = f"nested {resolved} {fname}"
                    lst = non_pod.setdefault(sname, [])
                    if reason not in lst:
                        lst.append(reason)
                        changed = True

    db.non_pod_structs = non_pod
    return db


def resolve_type_to_struct(raw_type: str, db: StructDB) -> str | None:
    t = re.sub(r"\b(?:const|volatile|struct|class|static|extern|register)\b", "", raw_type)
    t = t.replace("*", "").replace("&", "").replace("[]", "").strip()
    if not t:
        return None
    t = db.alias_to_struct.get(t, t)
    return t if t in db.structs else None


def extract_function_spans(text: str) -> list[tuple[str, str, int, int]]:
    """Return `(func_name, params_str, body_open_brace_idx, body_close_brace_idx)`."""
    spans: list[tuple[str, str, int, int]] = []
    # Match top-level C/C++ function definitions at brace depth 0
    n = len(text)
    i = 0
    depth = 0
    state = "NORMAL"

    while i < n:
        ch = text[i]
        if state == "NORMAL":
            if ch == '"':
                state = "STRING"
                i += 1
            elif ch == "'":
                state = "CHAR"
                i += 1
            elif ch == "{":
                if depth == 0:
                    # Look backwards up to 350 chars for function signature `) [const] {`
                    prefix = text[max(0, i - 350) : i]
                    m_sig = re.search(
                        r"(?:^|[\n;}])\s*(?:static\s+|inline\s+|extern\s+|virtual\s+)*"
                        r"[A-Za-z_][\w:\s*&<>]*?\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*(?:const\s*)?$",
                        prefix,
                        re.DOTALL,
                    )
                    if m_sig:
                        fname = m_sig.group(1)
                        params = m_sig.group(2)
                        if fname not in {"if", "for", "while", "switch", "catch", "sizeof"}:
                            close_idx = find_matching_brace(text, i)
                            spans.append((fname, params, i, close_idx))
                            i = close_idx + 1
                            continue
                depth += 1
                i += 1
            elif ch == "}":
                depth = max(0, depth - 1)
                i += 1
            else:
                i += 1
        elif state == "STRING":
            if ch == "\\" and i + 1 < n:
                i += 2
            elif ch == '"':
                state = "NORMAL"
                i += 1
            else:
                i += 1
        elif state == "CHAR":
            if ch == "\\" and i + 1 < n:
                i += 2
            elif ch == "'":
                state = "NORMAL"
                i += 1
            else:
                i += 1

    return spans


def infer_local_var_types(params: str, body: str) -> dict[str, str]:
    """Collect parameter and local variable type declarations inside a function."""
    vars_map: dict[str, str] = {}
    chunks = [p.strip() for p in params.split(",") if p.strip()]
    for p in chunks:
        p = re.sub(r"\s*=\s*[^,]+$", "", p).strip()
        m = re.match(r"^(.*?)([*&]*\s*[A-Za-z_]\w*)(?:\s*\[[^\]]*\])?$", p)
        if m:
            base = m.group(1).strip()
            decl = m.group(2).strip()
            vname = re.sub(r"[*&\s]+", "", decl)
            stars = "*" * decl.count("*")
            if base and vname:
                vars_map[vname] = (base + (" *" if stars else "")).strip()

    decl_re = re.compile(
        r"^[ \t]*(?:const\s+|static\s+|struct\s+)*"
        r"((?:(?:unsigned|signed|short|long)\s+)*[A-Za-z_][\w:]*(?:<[^>;\n]+>)?(?:\s*\*+)?)\s+([^;\n()]+);",
        re.MULTILINE,
    )
    skip_kw = {
        "return",
        "if",
        "else",
        "for",
        "while",
        "switch",
        "case",
        "break",
        "continue",
        "goto",
        "delete",
        "typedef",
        "using",
        "do",
    }
    for m in decl_re.finditer(body):
        btype = m.group(1).strip()
        if btype in skip_kw:
            continue
        decls = m.group(2)
        for part in decls.split(","):
            part_clean = re.sub(r"\s*=\s*.*$", "", part).strip()
            m_var = re.match(r"^([*&]*)\s*([A-Za-z_]\w*)(?:\s*\[[^\]]*\])?$", part_clean)
            if m_var:
                stars = "*" * m_var.group(1).count("*")
                is_arr = "[" in part_clean
                vname = m_var.group(2)
                if vname not in skip_kw:
                    vars_map[vname] = (btype + (" *" if stars else "") + (" []" if is_arr else "")).strip()
    return vars_map


DEFAULT_RECEIVER_TYPES: dict[str, str] = {
    "ch": "char_data",
    "victim": "char_data",
    "och": "char_data",
    "rch": "char_data",
    "actor": "char_data",
    "supermob": "char_data",
    "d": "descriptor_data",
    "dnew": "descriptor_data",
    "room": "room_index_data",
    "location": "room_index_data",
    "pRoomIndex": "room_index_data",
    "in_room": "room_index_data",
    "obj": "obj_data",
    "pObjIndex": "obj_index_data",
    "pMobIndex": "mob_index_data",
    "mprog": "mob_prog_data",
    "mprg": "mob_prog_data",
    "mprg2": "mob_prog_data",
    "mpact": "mob_prog_act_list",
    "tmp_act": "mob_prog_act_list",
    "ship": "ship_data",
    "xit": "exit_data",
    "pexit": "exit_data",
    "tarea": "area_data",
    "clan": "clan_data",
    "planet": "planet_data",
    "board": "board_data",
    "pShop": "shop_data",
    "pRepair": "repairshop_data",
    "sysdata": "system_data",
}


def resolve_expr_member(
    expr: str,
    local_vars: dict[str, str],
    db: StructDB,
) -> tuple[str | None, str | None, str | None]:
    """Resolve `a->b->c` to `(struct_name, field_name, leaf_type)`."""
    clean = expr.strip()
    clean = re.sub(r"^\([^)]*\)\s*", "", clean).lstrip("&* ").strip()
    clean = re.sub(r"\[[^\]]*\]", "", clean)
    if "->" not in clean and "." not in clean:
        # Plain variable
        t = local_vars.get(clean)
        if t:
            s = resolve_type_to_struct(t, db)
            return s, None, t
        return None, None, None

    tokens = re.split(r"->|\.", clean)
    root = tokens[0].strip()
    cur_struct: str | None = None
    if root in local_vars:
        cur_struct = resolve_type_to_struct(local_vars[root], db)
    if cur_struct is None and root in DEFAULT_RECEIVER_TYPES:
        cur_struct = DEFAULT_RECEIVER_TYPES[root]
    if cur_struct is None and root in ("game", "d->game") and "game_context" in db.structs:
        cur_struct = "game_context"

    if cur_struct is None:
        # Try matching penultimate token in DEFAULT_RECEIVER_TYPES
        if len(tokens) >= 2 and tokens[-2] in DEFAULT_RECEIVER_TYPES:
            s = DEFAULT_RECEIVER_TYPES[tokens[-2]]
            fname = tokens[-1].strip()
            ftype = db.structs.get(s, {}).get(fname)
            return s, fname, ftype
        return None, tokens[-1].strip(), None

    for idx, seg in enumerate(tokens[1:], start=1):
        fname = seg.strip()
        fields = db.structs.get(cur_struct, {})
        ftype = fields.get(fname)
        if idx == len(tokens) - 1:
            return cur_struct, fname, ftype
        if not ftype:
            # Fallback for known sub-fields
            if fname in DEFAULT_RECEIVER_TYPES:
                cur_struct = DEFAULT_RECEIVER_TYPES[fname]
                continue
            return None, tokens[-1].strip(), None
        nxt = resolve_type_to_struct(ftype, db)
        if not nxt:
            return None, tokens[-1].strip(), None
        cur_struct = nxt
    return None, None, None


# ---------------------------------------------------------------------------
# Scanner 1: allocator_mismatch_audit
# ---------------------------------------------------------------------------

ALLOC_HASHSTR_FUNCS = ("STRALLOC", "QUICKLINK", "fread_string", "copy_buffer", "make_olc_string_field")
ALLOC_MALLOC_FUNCS = (
    "str_dup",
    "strdup",
    "fread_string_nohash",
    "make_olc_string_nohash_field",
    "CREATE_OLD",
    "RECREATE_OLD",
    "calloc",
    "malloc",
    "realloc",
)
FREE_HASHSTR_FUNCS = ("STRFREE", "str_free")
FREE_MALLOC_FUNCS = ("STR_DISPOSE", "free", "make_olc_string_nohash_field")


def _clean_ptr_target(expr: str) -> str:
    """Strip whitespace, balanced outer parentheses, and C/C++ casts from a pointer target."""
    s = re.sub(r"\s+", "", expr)
    changed = True
    while changed and s:
        changed = False
        if s.startswith("(") and s.endswith(")"):
            depth = 0
            wraps_all = True
            for idx, ch in enumerate(s):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and idx < len(s) - 1:
                        wraps_all = False
                        break
            if wraps_all and depth == 0:
                s = s[1:-1]
                changed = True
                continue
        m_cpp = re.match(r"^(?:static_cast|const_cast|reinterpret_cast)<[^>]+>\((.+)\)$", s)
        if m_cpp:
            s = m_cpp.group(1)
            changed = True
            continue
        m_c = re.match(r"^\([^()]+\)(.+)$", s)
        if m_c:
            s = m_c.group(1)
            changed = True
            continue
    return s


def run_allocator_scanner(
    repo: Path,
    mk: MakefileInfo,
    files: list[SourceFile],
    mode: str,
) -> dict[str, Any]:
    db = parse_struct_db(files)

    # Check if STRFREE in mud.h sets point = NULL (joining backslash line continuations)
    strfree_nulls = False
    mud_h = next((f for f in files if f.rel_path == "src/mud.h"), None)
    if mud_h:
        mud_h_joined = re.sub(r"\\[ \t]*\r?\n", " ", mud_h.active_text)
        m_sf = re.search(
            r"^\s*#\s*define\s+STRFREE\s*\(\s*(\w+)\s*\)\s*(.+)$",
            mud_h_joined,
            re.MULTILINE,
        )
        if m_sf:
            arg = m_sf.group(1)
            body = m_sf.group(2)
            if re.search(rf"\(?\s*{re.escape(arg)}\s*\)?\s*(?<![=!<>])=(?!=)\s*(?:NULL|nullptr)\b", body):
                strfree_nulls = True

    field_allocs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    field_frees: dict[tuple[str, str], list[dict[str, Any]]] = {}
    local_alloc_free_mismatches: list[dict[str, Any]] = []
    non_1_create_sites: list[dict[str, Any]] = []
    strfree_reuse_without_null: list[dict[str, Any]] = []
    strfree_dangling_member_on_exit: list[dict[str, Any]] = []
    non_pod_mem_ops: list[dict[str, Any]] = []

    def _rec_field_alloc(s: str, f: str, regime: str, rel: str, line: int, op: str) -> None:
        field_allocs.setdefault((s, f), []).append(
            {"regime": regime, "file": rel, "line": line, "op": op}
        )

    def _rec_field_free(s: str, f: str, regime: str, rel: str, line: int, op: str) -> None:
        field_frees.setdefault((s, f), []).append(
            {"regime": regime, "file": rel, "line": line, "op": op}
        )

    # Track global CREATE/free in trivia.c (g_trivia) and struct-type CREATEs (e.g., struct_gladiator)
    global_allocs: dict[str, tuple[str, str, int]] = {}
    file_struct_allocs: dict[tuple[str, str], str] = {}

    for sf in files:
        if sf.is_header or not sf.in_makefile:
            continue
        for m_cr in re.finditer(r"\bCREATE\s*\(", sf.active_text):
            args, _ = extract_call_args(sf.active_text, m_cr.end() - 1)
            if len(args) >= 3:
                typ = re.sub(r"\s+", " ", args[1].strip())
                cnt = args[2].strip()
                regime = "cpp_new" if cnt == "1" else "cpp_new_array"
                file_struct_allocs[(sf.rel_path, typ)] = regime

    for sf in files:
        if sf.is_header:
            continue
        if not sf.in_makefile:
            continue
        text = sf.active_text
        raw_lines = sf.raw.split("\n")

        # Check global static allocations like g_trivia
        for m_cr in re.finditer(r"\bCREATE\s*\(", text):
            line_no = line_of_offset(sf.newline_offsets, m_cr.start())
            args, _ = extract_call_args(text, m_cr.end() - 1)
            if len(args) >= 3:
                tgt, typ, cnt = args[0].strip(), args[1].strip(), args[2].strip()
                if cnt != "1":
                    non_1_create_sites.append(
                        {
                            "file": sf.rel_path,
                            "line": line_no,
                            "target": tgt,
                            "type": typ,
                            "count": cnt,
                            "snippet": raw_lines[line_no - 1].strip() if line_no <= len(raw_lines) else "",
                        }
                    )
                if tgt == "g_trivia":
                    global_allocs[tgt] = ("cpp_new" if cnt == "1" else "cpp_new_array", sf.rel_path, line_no)

        # Function-scoped analysis
        for fname, params, bopen, bclose in extract_function_spans(text):
            body = text[bopen + 1 : bclose]
            mbody = mask_string_and_char_literals(body)
            local_vars = infer_local_var_types(params, body)
            local_allocs: dict[str, tuple[str, int]] = {}

            # 1. Local/field allocations via CREATE / CREATE_ARRAY / new
            for m_cr in re.finditer(r"\b(CREATE|CREATE_ARRAY)\s*\(", body):
                abs_pos = bopen + 1 + m_cr.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                macro = m_cr.group(1)
                args, _ = extract_call_args(body, m_cr.end() - 1)
                if len(args) >= 3:
                    tgt, _, cnt = args[0].strip(), args[1].strip(), args[2].strip()
                    regime = "cpp_new_array" if (macro == "CREATE_ARRAY" or cnt != "1") else "cpp_new"
                    if "->" in tgt or "." in tgt:
                        st, fl, _ = resolve_expr_member(tgt, local_vars, db)
                        if st and fl:
                            _rec_field_alloc(st, fl, regime, sf.rel_path, line_no, macro)
                    elif re.fullmatch(r"[A-Za-z_]\w*", tgt):
                        local_allocs[tgt] = (regime, line_no)

            # 1c. C++ new / new[] allocations (using literal-masked body)
            for m_new in re.finditer(
                r"([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)\s*=\s*new\s+(?:\([^)]*\)\s*)?[A-Za-z_][\w:]*(?:<[^>;\n]+>)?(?:\s*(\[[^\]\n]+\]))?",
                mbody,
            ):
                abs_pos = bopen + 1 + m_new.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                tgt = re.sub(r"\s+", "", m_new.group(1))
                is_arr = bool(m_new.group(2))
                regime = "cpp_new_array" if is_arr else "cpp_new"
                if "->" in tgt or "." in tgt:
                    st, fl, _ = resolve_expr_member(tgt, local_vars, db)
                    if st and fl:
                        _rec_field_alloc(st, fl, regime, sf.rel_path, line_no, "new[]" if is_arr else "new")
                elif re.fullmatch(r"[A-Za-z_]\w*", tgt):
                    local_allocs[tgt] = (regime, line_no)

            # 2. Assignment allocations: target = STRALLOC(...) / str_dup(...) / fread_string(...) / fread_string_nohash(...)
            assign_re = re.compile(
                r"([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)\s*=\s*"
                r"(STRALLOC|QUICKLINK|fread_string_nohash|fread_string|copy_buffer|str_dup|strdup|calloc|malloc|realloc)\s*\("
            )
            for m_as in assign_re.finditer(body):
                abs_pos = bopen + 1 + m_as.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                tgt = re.sub(r"\s+", "", m_as.group(1))
                fn = m_as.group(2)
                regime = "hashstr" if fn in ALLOC_HASHSTR_FUNCS else "malloc_strdup"
                if "->" in tgt or "." in tgt:
                    st, fl, _ = resolve_expr_member(tgt, local_vars, db)
                    if st and fl:
                        _rec_field_alloc(st, fl, regime, sf.rel_path, line_no, fn)
                else:
                    local_allocs[tgt] = (regime, line_no)

            # KEY("...", target, fread_string / fread_string_nohash) & make_olc_string*_field
            for m_key in re.finditer(r"\bKEY\s*\(", body):
                abs_pos = bopen + 1 + m_key.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                args, _ = extract_call_args(body, m_key.end() - 1)
                if len(args) >= 3:
                    tgt = args[1].strip()
                    rhs = args[2].strip()
                    if "fread_string_nohash" in rhs:
                        regime, op = "malloc_strdup", "fread_string_nohash"
                    elif "fread_string" in rhs:
                        regime, op = "hashstr", "fread_string"
                    else:
                        continue
                    if "->" in tgt or "." in tgt:
                        st, fl, _ = resolve_expr_member(tgt, local_vars, db)
                        if st and fl:
                            _rec_field_alloc(st, fl, regime, sf.rel_path, line_no, op)

            for m_olc in re.finditer(r"\b(make_olc_string_field|make_olc_string_nohash_field)\s*\(", body):
                abs_pos = bopen + 1 + m_olc.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                fn = m_olc.group(1)
                args, _ = extract_call_args(body, m_olc.end() - 1)
                if args:
                    tgt = args[0].strip()
                    regime = "hashstr" if fn == "make_olc_string_field" else "malloc_strdup"
                    if "->" in tgt or "." in tgt:
                        st, fl, _ = resolve_expr_member(tgt, local_vars, db)
                        if st and fl:
                            _rec_field_alloc(st, fl, regime, sf.rel_path, line_no, fn)
                            _rec_field_free(st, fl, regime, sf.rel_path, line_no, fn)

            # 1b. Local pointer alias propagation (e.g., ws2 = ws; or p2 = p;)
            for m_al in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;", body):
                lhs, rhs = m_al.group(1), m_al.group(2)
                if rhs in local_allocs and lhs not in local_allocs:
                    local_allocs[lhs] = local_allocs[rhs]
                elif lhs in local_vars:
                    vt = re.sub(r"\s+", " ", local_vars[lhs].replace("*", "").strip())
                    if (sf.rel_path, vt) in file_struct_allocs and lhs not in local_allocs:
                        local_allocs[lhs] = (file_struct_allocs[(sf.rel_path, vt)], line_of_offset(sf.newline_offsets, bopen + 1 + m_al.start()))

            # 3. Frees: STRFREE, STR_DISPOSE, DISPOSE, DISPOSE_ARRAY, free, delete, delete[]
            for m_fr in re.finditer(r"\b(STRFREE|STR_DISPOSE|DISPOSE_ARRAY|DISPOSE|free|str_free)\s*\(", body):
                abs_pos = bopen + 1 + m_fr.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                fn = m_fr.group(1)
                args, close_p = extract_call_args(body, m_fr.end() - 1)
                if not args:
                    continue
                tgt_clean = _clean_ptr_target(args[0])
                if fn in FREE_HASHSTR_FUNCS:
                    f_regime = "hashstr"
                elif fn in FREE_MALLOC_FUNCS:
                    f_regime = "malloc_strdup"
                elif fn == "DISPOSE":
                    f_regime = "cpp_new"
                elif fn == "DISPOSE_ARRAY":
                    f_regime = "cpp_new_array"
                else:
                    f_regime = "malloc_strdup"

                if "->" in tgt_clean or "." in tgt_clean:
                    st, fl, _ = resolve_expr_member(tgt_clean, local_vars, db)
                    if st and fl:
                        _rec_field_free(st, fl, f_regime, sf.rel_path, line_no, fn)
                elif tgt_clean in local_allocs or tgt_clean in global_allocs:
                    a_regime = (
                        local_allocs[tgt_clean][0]
                        if tgt_clean in local_allocs
                        else global_allocs[tgt_clean][0]
                    )
                    if a_regime != f_regime:
                        local_alloc_free_mismatches.append(
                            {
                                "file": sf.rel_path,
                                "function": fname,
                                "line": line_no,
                                "target": tgt_clean,
                                "alloc_regime": a_regime,
                                "free_op": fn,
                                "free_regime": f_regime,
                                "snippet": raw_lines[line_no - 1].strip() if line_no <= len(raw_lines) else "",
                            }
                        )

                # STRFREE reuse & dangling member checks
                if fn == "STRFREE":
                    after = body[close_p + 1 :]
                    # Scan up to next 5 statements within current brace block
                    block_rem = []
                    d = 0
                    for ch in after:
                        if ch == "{":
                            d += 1
                        elif ch == "}":
                            if d == 0:
                                break
                            d -= 1
                        block_rem.append(ch)
                        if len(block_rem) > 800:
                            break
                    block_text = "".join(block_rem)
                    stmts = [s.strip() for s in block_text.split(";")[:6]]

                    # Check reuse without null (including conditional/braced statements)
                    esc_tgt = re.escape(tgt_clean)
                    for s in stmts:
                        if not s:
                            continue
                        m_reassign = re.search(
                            rf"(?<![A-Za-z0-9_.>])(?<!->){esc_tgt}\s*=(?!=)\s*(.+)$",
                            s,
                            re.DOTALL,
                        )
                        if m_reassign:
                            rhs = m_reassign.group(1).strip()
                            is_conditional = bool(re.match(r"^(?:if|else|while|for)\b", s)) or "{" in s
                            if rhs in ("NULL", "nullptr", "0"):
                                if not is_conditional:
                                    break
                                continue
                            if re.search(rf"(?<![A-Za-z0-9_>]){esc_tgt}(?![A-Za-z0-9_])", rhs):
                                reuse_off = body.find(s, close_p + 1)
                                reuse_line = (
                                    line_of_offset(sf.newline_offsets, bopen + 1 + reuse_off)
                                    if reuse_off >= 0
                                    else line_no + 1
                                )
                                strfree_reuse_without_null.append(
                                    {
                                        "file": sf.rel_path,
                                        "function": fname,
                                        "strfree_line": line_no,
                                        "reuse_line": reuse_line,
                                        "target": tgt_clean,
                                        "snippet": s,
                                    }
                                )
                                break
                            if not is_conditional:
                                break
                            continue
                        if re.search(rf"(?<![A-Za-z0-9_>]){esc_tgt}(?![A-Za-z0-9_])", s):
                            # Passed to another call before reinitialization
                            if not strfree_nulls:
                                break

                    # Check dangling member on exit when STRFREE does not null
                    if not strfree_nulls and ("->" in tgt_clean or "." in tgt_clean):
                        base_obj = re.split(r"->|\.", tgt_clean)[0]
                        reassigned_or_freed = False
                        for s in stmts:
                            if not s:
                                continue
                            if re.match(rf"^{esc_tgt}\s*=", s):
                                reassigned_or_freed = True
                                break
                            if re.search(rf"\b(?:DISPOSE|STR_DISPOSE|free_char|free_desc)\s*\(\s*{re.escape(base_obj)}\b", s):
                                reassigned_or_freed = True
                                break
                            m_exit = re.search(r"\b(return|break)\b", s)
                            if m_exit and not reassigned_or_freed:
                                exit_off = body.find(s, close_p + 1)
                                exit_line = (
                                    line_of_offset(sf.newline_offsets, bopen + 1 + exit_off)
                                    if exit_off >= 0
                                    else line_no + 1
                                )
                                strfree_dangling_member_on_exit.append(
                                    {
                                        "file": sf.rel_path,
                                        "function": fname,
                                        "strfree_line": line_no,
                                        "exit_line": exit_line,
                                        "exit_kind": m_exit.group(1),
                                        "target": tgt_clean,
                                    }
                                )
                                break

            # 3b. C++ delete / delete[] (using literal-masked body)
            for m_del in re.finditer(
                r"\bdelete\b(?:\s*(\[\s*\])|\s*(?=[A-Za-z_(]))\s*([^;\n]+);",
                mbody,
            ):
                pre_del = mbody[max(0, m_del.start() - 4) : m_del.start()]
                if pre_del.rstrip().endswith(("->", ".")):
                    continue
                abs_pos = bopen + 1 + m_del.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                is_arr = bool(m_del.group(1))
                f_regime = "cpp_new_array" if is_arr else "cpp_new"
                op_name = "delete[]" if is_arr else "delete"
                tgt_clean = _clean_ptr_target(m_del.group(2))
                if not tgt_clean:
                    continue
                if "->" in tgt_clean or "." in tgt_clean:
                    st, fl, _ = resolve_expr_member(tgt_clean, local_vars, db)
                    if st and fl:
                        _rec_field_free(st, fl, f_regime, sf.rel_path, line_no, op_name)
                elif tgt_clean in local_allocs or tgt_clean in global_allocs:
                    a_regime = (
                        local_allocs[tgt_clean][0]
                        if tgt_clean in local_allocs
                        else global_allocs[tgt_clean][0]
                    )
                    if a_regime != f_regime:
                        local_alloc_free_mismatches.append(
                            {
                                "file": sf.rel_path,
                                "function": fname,
                                "line": line_no,
                                "target": tgt_clean,
                                "alloc_regime": a_regime,
                                "free_op": op_name,
                                "free_regime": f_regime,
                                "snippet": raw_lines[line_no - 1].strip() if line_no <= len(raw_lines) else "",
                            }
                        )

            # 4. non_pod_mem_ops (guarding POD members inside non-POD structs!)
            for m_mem in re.finditer(r"(?:\bstd::)?\b(memcpy|memmove|memset|bzero|calloc|realloc)\s*\(", body):
                abs_pos = bopen + 1 + m_mem.start()
                line_no = line_of_offset(sf.newline_offsets, abs_pos)
                fn = m_mem.group(1)
                args, _ = extract_call_args(body, m_mem.end() - 1)
                if not args:
                    continue
                arg0 = args[0].strip()
                st, fl, leaf_type = resolve_expr_member(arg0, local_vars, db)
                flagged = False
                if fl and leaf_type:
                    leaf_struct = resolve_type_to_struct(leaf_type, db)
                    if leaf_struct and leaf_struct in db.non_pod_structs:
                        flagged = True
                    elif any(tok in leaf_type for tok in NON_POD_MARKERS) and "*" not in leaf_type:
                        flagged = True
                elif not fl and st and st in db.non_pod_structs:
                    flagged = True
                if flagged:
                    non_pod_mem_ops.append(
                        {
                            "file": sf.rel_path,
                            "function": fname,
                            "line": line_no,
                            "op": fn,
                            "arg0": arg0,
                            "resolved_type": leaf_type or st,
                        }
                    )

    # Aggregate field_regime_conflicts
    field_regime_conflicts: list[dict[str, Any]] = []
    field_inventory: dict[str, Any] = {}
    all_keys = sorted(set(field_allocs.keys()) | set(field_frees.keys()))
    for s, f in all_keys:
        allocs = field_allocs.get((s, f), [])
        frees = field_frees.get((s, f), [])
        a_regimes = sorted({x["regime"] for x in allocs})
        f_regimes = sorted({x["regime"] for x in frees})
        key_str = f"{s}::{f}"
        field_inventory[key_str] = {
            "alloc_regimes": a_regimes,
            "free_regimes": f_regimes,
            "alloc_count": len(allocs),
            "free_count": len(frees),
        }
        conflict = len(a_regimes) > 1 or len(f_regimes) > 1
        if not conflict and a_regimes and f_regimes and a_regimes[0] != f_regimes[0]:
            conflict = True
        if conflict:
            field_regime_conflicts.append(
                {
                    "field": key_str,
                    "alloc_regimes": a_regimes,
                    "free_regimes": f_regimes,
                    "alloc_sites": allocs[:10],
                    "free_sites": frees[:10],
                }
            )

    summary = {
        "structs_parsed": len(db.structs),
        "non_pod_structs_count": len(db.non_pod_structs),
        "struct_fields_mapped": len(field_inventory),
        "field_regime_conflicts_count": len(field_regime_conflicts),
        "local_alloc_free_mismatches_count": len(local_alloc_free_mismatches),
        "non_1_create_count": len(non_1_create_sites),
        "strfree_reuse_without_null_count": len(strfree_reuse_without_null),
        "strfree_dangling_member_exit_count": len(strfree_dangling_member_on_exit),
        "non_pod_mem_ops_count": len(non_pod_mem_ops),
        "strfree_nulls_pointer": strfree_nulls,
    }

    violation_count = (
        len(field_regime_conflicts)
        + len(local_alloc_free_mismatches)
        + len(non_1_create_sites)
        + len(strfree_reuse_without_null)
        + len(strfree_dangling_member_on_exit)
        + len(non_pod_mem_ops)
    )
    if mode == "post-remediation" and not strfree_nulls:
        violation_count += 1

    return {
        "scanner": "allocator_mismatch_audit",
        "scope": "src/*.h and src/*.c",
        "summary": summary,
        "non_pod_structs": db.non_pod_structs,
        "findings": {
            "field_regime_conflicts": field_regime_conflicts,
            "local_alloc_free_mismatches": local_alloc_free_mismatches,
            "non_1_create_sites": non_1_create_sites,
            "strfree_reuse_without_null": strfree_reuse_without_null,
            "strfree_dangling_member_on_exit": strfree_dangling_member_on_exit,
            "non_pod_mem_ops": non_pod_mem_ops,
        },
        "field_regime_inventory": field_inventory,
        "passed": violation_count == 0,
    }


# ---------------------------------------------------------------------------
# Scanner 2: flag_domain_audit
# ---------------------------------------------------------------------------

def _collect_bitmask_macros(files: list[SourceFile]) -> tuple[set[str], set[str]]:
    """Return `(all_bitmask_macros, bv31_derived_macros)`."""
    defines: dict[str, str] = {}
    for sf in files:
        if not sf.in_makefile:
            continue
        joined = re.sub(r"\\\r?\n", " ", sf.active_text)
        for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)(?!\()\s+([^\n]+)", joined, re.MULTILINE):
            defines[m.group(1)] = m.group(2).strip()

    bitmasks: set[str] = {f"BV{i:02d}" for i in range(32)}
    for name, val in defines.items():
        if re.search(r"\bBV\d\d\b", val) or re.search(r"\b1[UuLl]*\s*<<", val):
            if not re.search(r"[+\-*/%<>]=?|==|!=", val.replace("<<", "")):
                bitmasks.add(name)

    changed = True
    while changed:
        changed = False
        for name, val in defines.items():
            if name in bitmasks:
                continue
            toks = re.findall(r"\b[A-Za-z_]\w*\b", val)
            if toks and all(t in bitmasks for t in toks) and not re.search(r"[+\-*/%<>]=?|==|!=", val.replace("<<", "")):
                bitmasks.add(name)
                changed = True

    bv31_derived: set[str] = set()
    if "BV31" in defines and not re.search(r"\b1[UuLl]+\s*<<\s*31\b", defines["BV31"]):
        bv31_derived.add("BV31")
        changed = True
        while changed:
            changed = False
            for name, val in defines.items():
                if name in bv31_derived:
                    continue
                toks = re.findall(r"\b[A-Za-z_]\w*\b", val)
                if any(t in bv31_derived for t in toks):
                    bv31_derived.add(name)
                    changed = True

    return bitmasks, bv31_derived


def run_flag_scanner(
    repo: Path,
    mk: MakefileInfo,
    files: list[SourceFile],
    mode: str,
) -> dict[str, Any]:
    db = parse_struct_db(files)
    bitset_struct_fields: dict[str, list[str]] = {}
    unique_bitset_fields: set[str] = set()
    ambiguous_fields: set[str] = set()
    all_struct_fields_seen: dict[str, set[str]] = {}

    for sname, fields in sorted(db.structs.items()):
        for fname, ftype in sorted(fields.items()):
            all_struct_fields_seen.setdefault(fname, set()).add(ftype)
            if re.search(r"\b(?:BitSet|FLAG_SET)\b", ftype) and "*" not in ftype:
                bitset_struct_fields.setdefault(sname, []).append(fname)

    for fname, types in all_struct_fields_seen.items():
        has_bs = any(re.search(r"\b(?:BitSet|FLAG_SET)\b", t) and "*" not in t for t in types)
        has_non_bs = any(not (re.search(r"\b(?:BitSet|FLAG_SET)\b", t) and "*" not in t) for t in types)
        if has_bs and not has_non_bs:
            unique_bitset_fields.add(fname)
        elif has_bs and has_non_bs:
            ambiguous_fields.add(fname)

    bitmask_macros, bv31_derived = _collect_bitmask_macros(files)

    bitset_passed_bitmask: list[dict[str, Any]] = []
    legacy_bitop_on_bitset: list[dict[str, Any]] = []
    signed_shift_ub: list[dict[str, Any]] = []
    structural_issues: list[dict[str, Any]] = []

    unsigned_cast_re = re.compile(
        r"(?:\(\s*unsigned(?:\s+(?:int|long(?:\s+long)?))?\s*\)|"
        r"\(\s*(?:std::)?uint(?:32|64)_t\s*\)|"
        r"\(\s*(?:std::)?size_t\s*\)|"
        r"\(\s*uintptr_t\s*\))\s*$"
    )
    bare_shift_re = re.compile(r"(?<![A-Za-z0-9_.])1(?![UuLl0-9.])\s*<<\s*")

    for sf in files:
        include_file = sf.in_makefile or (mode == "baseline")
        if not include_file:
            continue
        text = sf.active_text
        masked_text = mask_string_and_char_literals(text)
        active_lines = text.split("\n")
        masked_lines = masked_text.split("\n")
        raw_lines = sf.raw.split("\n")

        # Build local var context map per function for accurate receiver resolution
        fn_spans = extract_function_spans(text)

        def _vars_at(pos: int) -> dict[str, str]:
            for _, params, bo, bc in fn_spans:
                if bo <= pos <= bc:
                    return infer_local_var_types(params, text[bo + 1 : bc])
            return {}

        # 1. bitset_passed_bitmask (BV_* macros and BitSet method calls)
        for m_bv in re.finditer(r"\b(BV_IS_SET|BV_SET_BIT|BV_REMOVE_BIT|BV_TOGGLE_BIT)\s*\(", masked_text):
            line_no = line_of_offset(sf.newline_offsets, m_bv.start())
            line_txt = active_lines[line_no - 1] if line_no <= len(active_lines) else ""
            if re.match(r"^\s*#\s*define\s+BV_", line_txt):
                continue
            args, _ = extract_call_args(masked_text, m_bv.end() - 1)
            if len(args) >= 2:
                bit_arg = args[1].strip()
                toks = re.findall(r"\b[A-Za-z_]\w*\b", bit_arg)
                matched = [t for t in toks if t in bitmask_macros]
                if matched or re.search(r"\b1[UuLl]*\s*<<", bit_arg):
                    bitset_passed_bitmask.append(
                        {
                            "file": sf.rel_path,
                            "in_makefile": sf.in_makefile,
                            "line": line_no,
                            "api": m_bv.group(1),
                            "target": args[0].strip(),
                            "bit_arg": bit_arg,
                            "matched_bitmasks": matched,
                        }
                    )

        for m_meth in re.finditer(
            r"([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*|\([^()]+\))\s*(?:\.|->)\s*(test|set|clear|toggle)\s*\(",
            masked_text,
        ):
            line_no = line_of_offset(sf.newline_offsets, m_meth.start())
            line_txt = active_lines[line_no - 1] if line_no <= len(active_lines) else ""
            if re.match(r"^\s*#\s*define\s+BV_", line_txt):
                continue
            args, _ = extract_call_args(masked_text, m_meth.end() - 1)
            if len(args) != 1:
                continue
            bit_arg = args[0].strip()
            toks = re.findall(r"\b[A-Za-z_]\w*\b", bit_arg)
            matched = [t for t in toks if t in bitmask_macros]
            if matched or re.search(r"\b1[UuLl]*\s*<<", bit_arg):
                bitset_passed_bitmask.append(
                    {
                        "file": sf.rel_path,
                        "in_makefile": sf.in_makefile,
                        "line": line_no,
                        "api": f"BitSet::{m_meth.group(2)}",
                        "target": m_meth.group(1).strip(),
                        "bit_arg": bit_arg,
                        "matched_bitmasks": matched,
                    }
                )

        # 2. legacy_bitop_on_bitset
        for m_leg in re.finditer(r"\b(IS_SET|SET_BIT|REMOVE_BIT|TOGGLE_BIT)\s*\(", masked_text):
            line_no = line_of_offset(sf.newline_offsets, m_leg.start())
            line_txt = active_lines[line_no - 1] if line_no <= len(active_lines) else ""
            if re.match(r"^\s*#\s*define\s+(?:IS_SET|SET_BIT|REMOVE_BIT|TOGGLE_BIT)\b", line_txt):
                continue
            args, _ = extract_call_args(masked_text, m_leg.end() - 1)
            if not args:
                continue
            arg0 = args[0].strip()
            lvars = _vars_at(m_leg.start())
            st, fl, ftype = resolve_expr_member(arg0, lvars, db)
            is_bitset = False
            if ftype and re.search(r"\b(?:BitSet|FLAG_SET)\b", ftype) and "*" not in ftype:
                is_bitset = True
            elif fl and fl in unique_bitset_fields:
                is_bitset = True
            elif fl and st and fl in bitset_struct_fields.get(st, []):
                is_bitset = True
            if is_bitset:
                legacy_bitop_on_bitset.append(
                    {
                        "file": sf.rel_path,
                        "in_makefile": sf.in_makefile,
                        "line": line_no,
                        "api": m_leg.group(1),
                        "target": arg0,
                        "struct": st,
                        "field": fl,
                    }
                )

        # 3. signed_shift_ub (scan line-by-line on literal-masked active_text)
        for line_no, line in enumerate(masked_lines, start=1):
            # 3a. Bare `1 << expr`
            for m_sh in bare_shift_re.finditer(line):
                before = line[: m_sh.start()]
                if unsigned_cast_re.search(before):
                    continue
                rest = line[m_sh.end() :]
                # Extract shift RHS expression up to delimiter
                rhs_chars: list[str] = []
                p_depth = 0
                for ch in rest:
                    if ch == "(":
                        p_depth += 1
                    elif ch == ")":
                        if p_depth == 0:
                            break
                        p_depth -= 1
                    elif p_depth == 0 and ch in ",;|&^+-?:":
                        break
                    rhs_chars.append(ch)
                rhs = "".join(rhs_chars).strip()
                if re.fullmatch(r"\d+", rhs):
                    val = int(rhs)
                    if val >= 31:
                        signed_shift_ub.append(
                            {
                                "file": sf.rel_path,
                                "in_makefile": sf.in_makefile,
                                "line": line_no,
                                "kind": "signed_1_shift_31_ub",
                                "expr": f"1 << {rhs}",
                                "snippet": raw_lines[line_no - 1].strip() if line_no <= len(raw_lines) else line.strip(),
                            }
                        )
                else:
                    signed_shift_ub.append(
                        {
                            "file": sf.rel_path,
                            "in_makefile": sf.in_makefile,
                            "line": line_no,
                            "kind": "signed_1_nonconst_shift",
                            "expr": f"1 << {rhs}",
                            "snippet": raw_lines[line_no - 1].strip() if line_no <= len(raw_lines) else line.strip(),
                        }
                    )

            # 3b. BV31-derived macros (skip if already reported as 1 << 31 on this line)
            if bv31_derived and not re.match(r"^\s*#\s*define\s+BV31\b", line):
                for m_tok in re.finditer(r"\b([A-Za-z_]\w*)\b", line):
                    tok = m_tok.group(1)
                    if tok in bv31_derived:
                        signed_shift_ub.append(
                            {
                                "file": sf.rel_path,
                                "in_makefile": sf.in_makefile,
                                "line": line_no,
                                "kind": "bv31_macro_ub",
                                "expr": tok,
                                "snippet": raw_lines[line_no - 1].strip() if line_no <= len(raw_lines) else line.strip(),
                            }
                        )

    # Check BitSet structural invariants in mud.h (AUD-003 & AUD-005)
    mud_h = next((f for f in files if f.rel_path == "src/mud.h"), None)
    if mud_h:
        m_bs = re.search(r"\b(?:struct|class)\s+BitSet\b[^{]*\{", mud_h.active_text)
        if m_bs:
            bs_open = m_bs.end() - 1
            bs_close = find_matching_brace(mud_h.active_text, bs_open)
            bitset_body = mud_h.active_text[bs_open + 1 : bs_close]

            size_is_word_count = False
            m_sz = re.search(r"\bsize_t\s+size\s*\(\s*\)\s*const\s*\{", bitset_body)
            if m_sz:
                sz_open = m_sz.end() - 1
                sz_close = find_matching_brace(bitset_body, sz_open)
                sz_body = bitset_body[sz_open + 1 : sz_close]
                uses_word_len = bool(re.search(r"\b(?:bits\.size|word_count)\s*\(\s*\)", sz_body))
                scales_to_bits = bool(
                    re.search(r"(?:\*\s*64\b|\b64\s*\*|<<\s*6\b|\bbit_capacity\s*\(\s*\))", sz_body)
                )
                if uses_word_len and not scales_to_bits:
                    size_is_word_count = True
                    structural_issues.append(
                        {
                            "file": "src/mud.h",
                            "issue": "BitSet_size_returns_word_count_instead_of_bit_count",
                        }
                    )

            m_ie = re.search(r"\bbool\s+intersects_excluding\s*\([^)]*\)\s*const\s*\{", bitset_body)
            if m_ie:
                ie_open = m_ie.end() - 1
                ie_close = find_matching_brace(bitset_body, ie_open)
                ie_body = bitset_body[ie_open + 1 : ie_close]
                is_word_loop = ("bits[" in ie_body and "other.bits[" in ie_body)
                is_bit_loop = bool(re.search(r"\btest\s*\(", ie_body))
                word_atom = r"(?:(?:other\.)?(?:bits\.size|word_count)\s*\(\s*\))"
                min_arg = r"(?:[^()]|\([^()]*\))*"
                word_or_min = rf"(?:{word_atom}|(?:std::)?min(?:\s*<[^>]+>)?\s*\({min_arg}{word_atom}{min_arg}\))"
                scaled_word_re = rf"(?:{word_or_min}\s*(?:\*\s*64\b|<<\s*6\b)|\b64\s*\*\s*{word_or_min})"
                bitset_size_call_re = r"(?:(?<!\.)|\bother\.|->)\bsize\s*\(\s*\)"
                if is_word_loop:
                    bad_word_scale = bool(
                        re.search(
                            rf"(?:{scaled_word_re}|\bbit_capacity\s*\(\s*\))",
                            ie_body,
                        )
                    ) or ((not size_is_word_count) and bool(re.search(bitset_size_call_re, ie_body)))
                    if bad_word_scale:
                        structural_issues.append(
                            {
                                "file": "src/mud.h",
                                "issue": "BitSet_intersects_excluding_uses_word_count_instead_of_bit_count",
                            }
                        )
                elif is_bit_loop:
                    has_word_bound = bool(re.search(word_atom, ie_body))
                    scales_word_bound = bool(re.search(scaled_word_re, ie_body))
                    uses_bad_size = size_is_word_count and bool(
                        re.search(bitset_size_call_re, ie_body)
                    )
                    if (has_word_bound and not scales_word_bound) or uses_bad_size:
                        structural_issues.append(
                            {
                                "file": "src/mud.h",
                                "issue": "BitSet_intersects_excluding_uses_word_count_instead_of_bit_count",
                            }
                        )
        mud_h_joined = re.sub(r"\\[ \t]*\r?\n", " ", mud_h.active_text)
        m_arena = re.search(r"^\s*#\s*define\s+IN_ARENA\s*\([^)]*\)\s*(.+)$", mud_h_joined, re.MULTILINE)
        if m_arena:
            arena_body = m_arena.group(1)
            uses_legacy_is_set = bool(re.search(r"\bIS_SET\b", arena_body))
            missing_in_room_guard = False
            if "->room_flags" in arena_body:
                before_rf = arena_body.split("->room_flags", 1)[0]
                missing_in_room_guard = not bool(
                    re.search(r"->in_room\s*(?:!=\s*(?:NULL|nullptr|0)\s*)?(?:&&|\?)", before_rf)
                )
            if uses_legacy_is_set or missing_in_room_guard:
                structural_issues.append(
                    {
                        "file": "src/mud.h",
                        "issue": "IN_ARENA_uses_legacy_IS_SET_or_missing_in_room_null_guard",
                    }
                )

    active_bpb = [x for x in bitset_passed_bitmask if x["in_makefile"]]
    active_leg = [x for x in legacy_bitop_on_bitset if x["in_makefile"]]
    active_ub = [x for x in signed_shift_ub if x["in_makefile"]]

    summary = {
        "bitset_fields_in_headers": sum(len(v) for v in bitset_struct_fields.values()),
        "bitmask_macros_tracked": len(bitmask_macros),
        "bitset_passed_bitmask_count": len(bitset_passed_bitmask),
        "legacy_bitop_on_bitset_count": len(legacy_bitop_on_bitset),
        "signed_shift_ub_count": len(signed_shift_ub),
        "active_bitset_passed_bitmask_count": len(active_bpb),
        "active_legacy_bitop_on_bitset_count": len(active_leg),
        "active_signed_shift_ub_count": len(active_ub),
        "structural_issues_count": len(structural_issues),
    }

    if mode == "baseline":
        passed = (
            len(bitset_passed_bitmask) == 0
            and len(legacy_bitop_on_bitset) == 0
            and len(signed_shift_ub) == 0
        )
    else:
        passed = (
            len(active_bpb) == 0
            and len(active_leg) == 0
            and len(active_ub) == 0
            and len(structural_issues) == 0
        )

    return {
        "scanner": "flag_domain_audit",
        "scope": "src/*.h and src/*.c",
        "summary": summary,
        "bitset_struct_fields": bitset_struct_fields,
        "findings": {
            "bitset_passed_bitmask": bitset_passed_bitmask,
            "legacy_bitop_on_bitset": legacy_bitop_on_bitset,
            "signed_shift_ub": signed_shift_ub,
            "structural_issues": structural_issues,
        },
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Scanner 3: command_skill_wiring_audit
# ---------------------------------------------------------------------------

def _parse_tilde_stanzas(path: Path, header_tag: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s == "#END":
            break
        if s == header_tag:
            cur = {"line": i + 1, "affect_ex": []}
        elif cur is not None:
            if s == "End":
                entries.append(cur)
                cur = None
            elif s.startswith("Name "):
                cur["name"] = s[5:].strip().rstrip("~")
            elif s.startswith("DisplayName "):
                cur["display_name"] = s[12:].strip().rstrip("~")
            elif s.startswith("Code "):
                cur["code"] = s[5:].strip()
            elif s.startswith("Type "):
                cur["type"] = s[5:].strip()
            elif s.startswith("Position "):
                cur["position"] = s[9:].strip()
            elif s.startswith("Level "):
                cur["level"] = s[6:].strip()
            elif s.startswith("Log "):
                cur["log"] = s[4:].strip()
            elif s.startswith("Gsn "):
                cur["gsn"] = s[4:].strip().rstrip("~")
            elif s.startswith("OutputMode "):
                cur["output_mode"] = s[11:].strip().rstrip("~")
            elif s.startswith("ResultVnum "):
                try:
                    cur["result_vnum"] = int(s[11:].strip())
                except ValueError:
                    cur["result_vnum"] = -1
            elif s.startswith("Disabled "):
                val = s[9:].strip().rstrip("~").lower()
                cur["disabled"] = val in {"1", "true", "yes"}
            elif s.startswith("AffectEx "):
                m_ax = re.match(r"^AffectEx\s+'[^']*'\s+(\d+)\s+'[^']*'\s+(-?\d+)", s)
                if m_ax:
                    cur["affect_ex"].append(
                        {"line": i + 1, "location": int(m_ax.group(1)), "bitvector": int(m_ax.group(2))}
                    )
        i += 1
    return entries


def _extract_tables_c_wiring(tables_sf: SourceFile, mk: MakefileInfo, mode: str) -> dict[str, Any]:
    """Parse `src/tables.c` supporting both switch/if style and table-array style."""
    text = tables_sf.comment_stripped
    # In baseline, if -DMCCP is NOT in Makefile, strip #ifdef MCCP blocks in tables.c
    if mode == "baseline" and not mk.has_d_mccp:
        text = strip_inactive_ifdefs(text, {"HASHSTR"}, ALWAYS_FALSE_MACROS | {"MCCP"})
    else:
        text = tables_sf.active_text

    skill_fn_fwd: dict[str, str] = {}
    skill_fn_rev: dict[str, str] = {}
    spell_fn_fwd: dict[str, str] = {}
    spell_fn_rev: dict[str, str] = {}

    spans = extract_function_spans(text)
    fn_bodies = {name: text[bo + 1 : bc] for name, _, bo, bc in spans}

    # Style A: switch/if inside skill_function, skill_name, spell_function, spell_name
    if "skill_function" in fn_bodies:
        for m in re.finditer(r'str_cmp\s*\(\s*\w+\s*,\s*"([^"]+)"\s*\)\s*\)\s*return\s+([A-Za-z_]\w*)\s*;', fn_bodies["skill_function"]):
            skill_fn_fwd[m.group(1)] = m.group(2)
    if "skill_name" in fn_bodies:
        for m in re.finditer(r'==\s*([A-Za-z_]\w*)\s*\)\s*return\s+"([^"]+)"\s*;', fn_bodies["skill_name"]):
            skill_fn_rev[m.group(1)] = m.group(2)
    if "spell_function" in fn_bodies:
        for m in re.finditer(r'str_cmp\s*\(\s*\w+\s*,\s*"([^"]+)"\s*\)\s*\)\s*return\s+([A-Za-z_]\w*)\s*;', fn_bodies["spell_function"]):
            spell_fn_fwd[m.group(1)] = m.group(2)
    if "spell_name" in fn_bodies:
        for m in re.finditer(r'==\s*([A-Za-z_]\w*)\s*\)\s*return\s+"([^"]+)"\s*;', fn_bodies["spell_name"]):
            spell_fn_rev[m.group(1)] = m.group(2)

    # Style B: struct table entries `{ "do_...", do_... }` or `{ "spell_...", spell_... }`
    for m in re.finditer(r'\{\s*"((?:do|spell)_[A-Za-z0-9_]+)"\s*,\s*([A-Za-z0-9_]+)\s*\}', text):
        sname, sym = m.group(1), m.group(2)
        if sname.startswith("do_") and not skill_fn_fwd:
            skill_fn_fwd[sname] = sym
            skill_fn_rev[sym] = sname
        elif sname.startswith("spell_") and not spell_fn_fwd:
            spell_fn_fwd[sname] = sym
            spell_fn_rev[sym] = sname
        else:
            if sname.startswith("do_"):
                skill_fn_fwd.setdefault(sname, sym)
                skill_fn_rev.setdefault(sym, sname)
            else:
                spell_fn_fwd.setdefault(sname, sym)
                spell_fn_rev.setdefault(sym, sname)

    return {
        "skill_fwd": skill_fn_fwd,
        "skill_rev": skill_fn_rev,
        "spell_fwd": spell_fn_fwd,
        "spell_rev": spell_fn_rev,
    }


def run_wiring_scanner(
    repo: Path,
    mk: MakefileInfo,
    files: list[SourceFile],
    mode: str,
    defined_object_vnums: set[int] | None = None,
) -> dict[str, Any]:
    cmds = _parse_tilde_stanzas(repo / "system" / "commands.dat", "#COMMAND")
    skills = _parse_tilde_stanzas(repo / "system" / "skills.dat", "#SKILL")
    crafts = _parse_tilde_stanzas(repo / "system" / "craft_recipes.dat", "#CRAFT")

    tables_sf = next((f for f in files if f.rel_path == "src/tables.c"), None)
    wiring = _extract_tables_c_wiring(tables_sf, mk, mode) if tables_sf else {
        "skill_fwd": {}, "skill_rev": {}, "spell_fwd": {}, "spell_rev": {}
    }

    declared_do: set[str] = set()
    declared_spell: set[str] = set()
    for sf in files:
        if not sf.is_header:
            continue
        non_define_text = "\n".join(
            ln for ln in sf.active_text.split("\n") if not re.match(r"^\s*#\s*define\b", ln)
        )
        for m in re.finditer(r"\bDECLARE_DO_FUN\s*\(\s*([A-Za-z0-9_]+)\s*\)", non_define_text):
            declared_do.add(m.group(1))
        for m in re.finditer(r"\bDECLARE_SPELL_FUN\s*\(\s*([A-Za-z0-9_]+)\s*\)", non_define_text):
            declared_spell.add(m.group(1))

    defined_do: set[str] = set()
    defined_all_void: set[str] = set()
    defined_spell: set[str] = set()
    defined_all_spell: set[str] = set()
    do_make_total: list[str] = []
    do_make_retired: list[str] = []
    live_not_implemented_stubs: list[dict[str, Any]] = []
    registry_targets = set(wiring["skill_fwd"].values()) | set(wiring["spell_fwd"].values())

    non_command_helpers = {
        "do_game",
        "do_end_game",
        "do_look_str",
        "do_note",
        "do_showstatistic_web",
        "do_craft_build",
    }

    for sf in files:
        if sf.is_header or not sf.in_makefile:
            continue
        text = sf.active_text
        spans = extract_function_spans(text) if mode == "post-remediation" else []
        definitions = {name for name, _, _, _ in spans}
        for m in re.finditer(r"^\s*void\s+([A-Za-z0-9_]+)\s*\(", text, re.MULTILINE):
            if mode == "baseline" or m.group(1) in definitions:
                defined_all_void.add(m.group(1))

        if mode == "baseline":
            for m in re.finditer(r"^\s*void\s+(do_[A-Za-z0-9_]+)\s*\(", text, re.MULTILINE):
                defined_do.add(m.group(1))
        else:
            for m in re.finditer(
                r"^\s*void\s+(do_[A-Za-z0-9_]+)\s*\(\s*CHAR_DATA\s*\*\s*[A-Za-z0-9_]*\s*,\s*(?:const\s+)?char\s*\*\s*[A-Za-z0-9_]*\s*\)",
                text,
                re.MULTILINE,
            ):
                fn = m.group(1)
                if fn not in non_command_helpers:
                    defined_do.add(fn)

        for m in re.finditer(r"^\s*ch_ret\s+(spell_[A-Za-z0-9_]+)\s*\(", text, re.MULTILINE):
            sp = m.group(1)
            if mode == "post-remediation" and sp not in definitions:
                continue
            defined_all_spell.add(sp)
            if sp in {"spell_null", "spell_smaug", "spell_notfound"}:
                continue
            defined_spell.add(sp)

        if mode == "post-remediation":
            for fname, _, bo, bc in spans:
                if fname in registry_targets and fname not in {"spell_null", "spell_smaug"}:
                    body = text[bo + 1 : bc]
                    if not body.strip().strip(";") or re.search(r'"[^"]*Not implemented yet![^"]*"', body, re.IGNORECASE):
                        live_not_implemented_stubs.append(
                            {
                                "file": sf.rel_path,
                                "symbol": fname,
                                "line": line_of_offset(sf.newline_offsets, bo),
                            }
                        )

    for sym in sorted(defined_do):
        if sym.startswith("do_make"):
            do_make_total.append(sym)
            if sym in RETIRED_DO_MAKE_NAMES:
                do_make_retired.append(sym)

    # Check commands.dat
    commands_broken: list[dict[str, Any]] = []
    dat_codes: set[str] = set()
    for c in cmds:
        code = c.get("code", "")
        if code:
            dat_codes.add(code)
        if code == "reserved" or not code:
            commands_broken.append({**c, "issue": "resolves_to_skill_notfound_missing_in_tables_c"})
        elif code == "do_compress" and mode == "baseline" and not mk.has_d_mccp:
            commands_broken.append({**c, "issue": "guarded_by_ifdef_MCCP_disabled_in_Makefile"})
        elif code not in wiring["skill_fwd"] and (mode != "baseline" or code not in wiring["spell_fwd"]):
            commands_broken.append({**c, "issue": "code_missing_from_tables_c"})

    # Check skills.dat
    skills_broken: list[dict[str, Any]] = []
    skill_names_lower: set[str] = set()
    for sk in skills:
        sname = sk.get("name", "")
        if sname:
            skill_names_lower.add(sname.lower())
        code = sk.get("code", "")
        if code:
            dat_codes.add(code)
        if mode == "post-remediation":
            if code == "do_say" and sk.get("type", "").lower() in {"skill", "spell", "weapon", "tongue", "herb"}:
                skills_broken.append({"name": sname, "line": sk["line"], "issue": "broken_do_say_binding"})
            if code and code not in {"spell_null", "spell_smaug"}:
                if code.startswith("do_") and code not in wiring["skill_fwd"]:
                    skills_broken.append({"name": sname, "line": sk["line"], "issue": "skill_do_code_missing_from_tables"})
                elif code.startswith("spell_") and code not in wiring["spell_fwd"]:
                    skills_broken.append({"name": sname, "line": sk["line"], "issue": "skill_spell_code_missing_from_tables"})
                elif not code.startswith(("do_", "spell_")) or code == "spell_notfound":
                    skills_broken.append({"name": sname, "line": sk["line"], "issue": "unsupported_skill_code"})
            for ax in sk.get("affect_ex", []):
                if ax["bitvector"] > 64 or (ax["location"] == 26 and ax["bitvector"] == 0):
                    skills_broken.append({"name": sname, "line": ax["line"], "issue": "invalid_AffectEx_bitvector"})

    # C++ -> tables.c / dat comparisons
    cpp_do_missing_from_tables = sorted(
        s for s in defined_do
        if s not in wiring["skill_fwd"] and not (mode == "baseline" and not mk.has_d_mccp and s == "do_compress")
    )
    cpp_do_missing_from_rev = sorted(s for s in defined_do if s not in wiring["skill_rev"])
    cpp_do_missing_from_dat = sorted(s for s in defined_do if s not in dat_codes)

    cpp_spell_missing_from_tables = sorted(s for s in defined_spell if s not in wiring["spell_fwd"])
    cpp_spell_missing_from_rev = sorted(s for s in defined_spell if s not in wiring["spell_rev"])
    cpp_spell_missing_from_dat = sorted(s for s in defined_spell if s not in dat_codes)

    # Internal handlers and available-but-unused registry entries need no DAT row.
    # Every registered code must still resolve to a compiled handler and survive saving.
    registry_issues: list[dict[str, str]] = []
    if mode == "post-remediation":
        for kind, definitions in (("skill", defined_all_void), ("spell", defined_all_spell)):
            forward = wiring[f"{kind}_fwd"]
            reverse = wiring[f"{kind}_rev"]
            for code, target in sorted(forward.items()):
                if code == "reserved" and target == "NULL" and kind == "spell":
                    continue
                if target not in definitions:
                    registry_issues.append({"code": code, "target": target, "issue": "target_not_defined_in_build"})
                elif forward.get(reverse.get(target, "")) != target:
                    registry_issues.append({"code": code, "target": target, "issue": "missing_round_trip_mapping"})
            for target, code in sorted(reverse.items()):
                if forward.get(code) != target:
                    registry_issues.append({"code": code, "target": target, "issue": "reverse_mapping_not_resolved"})
    registered_retired_craft = sorted(registry_targets.intersection(RETIRED_DO_MAKE_NAMES))

    if mode == "baseline":
        declared_but_undefined = sorted(
            s for s in (declared_do | declared_spell)
            if s not in defined_do and s not in defined_all_void and s not in defined_all_spell
        )
    else:
        declared_but_undefined = sorted(
            s for s in (declared_do | declared_spell)
            if s not in defined_all_void and s not in defined_all_spell
        )

    # special.c spec_* lookup
    spec_missing_from_lookup: list[str] = []
    special_sf = next((f for f in files if f.rel_path == "src/special.c"), None)
    if special_sf:
        spec_defs = set(re.findall(r"^\s*bool\s+(spec_[A-Za-z0-9_]+)\s*\(", special_sf.active_text, re.MULTILINE))
        spans = dict((n, special_sf.active_text[bo + 1 : bc]) for n, _, bo, bc in extract_function_spans(special_sf.active_text))
        lookup_body = spans.get("spec_lookup", "") + "\n" + spans.get("lookup_spec", "")
        in_lookup = set(re.findall(r"\bspec_[A-Za-z0-9_]+\b", lookup_body))
        spec_missing_from_lookup = sorted(s for s in spec_defs if s not in in_lookup)

    # ASSIGN_GSN in db.c
    missing_assign_gsns: list[dict[str, Any]] = []
    db_sf = next((f for f in files if f.rel_path == "src/db.c"), None)
    if db_sf:
        for m in re.finditer(r'\bASSIGN_GSN\s*\(\s*(\w+)\s*,\s*"([^"]+)"\s*\)', db_sf.active_text):
            gsn_var, sk_name = m.group(1), m.group(2)
            if sk_name.lower() not in skill_names_lower:
                missing_assign_gsns.append(
                    {
                        "gsn_var": gsn_var,
                        "skill_name": sk_name,
                        "line": line_of_offset(db_sf.newline_offsets, m.start()),
                        "issue": "skill_not_found_in_skills_dat",
                    }
                )

    # craft_recipes.dat
    if defined_object_vnums is None:
        defined_object_vnums = _collect_area_object_vnums(repo, active_only=mode == "post-remediation")

    craft_recipes_broken: list[dict[str, Any]] = []
    for cr in crafts:
        if cr.get("disabled"):
            continue
        gsn = cr.get("gsn", "none")
        if gsn and gsn.lower() != "none" and gsn.lower() not in skill_names_lower:
            craft_recipes_broken.append(
                {"name": cr.get("name"), "line": cr["line"], "issue": "gsn_missing_from_skills_dat", "gsn": gsn}
            )
        if cr.get("output_mode") == "create_proto":
            rv = cr.get("result_vnum", 0)
            if rv <= 0 or rv not in defined_object_vnums:
                craft_recipes_broken.append(
                    {"name": cr.get("name"), "line": cr["line"], "issue": "result_vnum_missing_from_areas", "result_vnum": rv}
                )

    # Post-remediation build/toolchain checks
    toolchain_issues: list[str] = []
    excluded_toolchain_files: list[str] = []
    if mode == "post-remediation":
        if not mk.has_d_mccp:
            toolchain_issues.append("Makefile missing -DMCCP")
        if mk.has_d_requests:
            toolchain_issues.append("Makefile still has -DREQUESTS")
        mud_h = next((f for f in files if f.rel_path == "src/mud.h"), None)
        if (
            mud_h
            and re.search(r"^\s*#\s*define\s+MCCP\b", mud_h.comment_stripped, re.MULTILINE)
            and not re.search(r"#\s*ifndef\s+MCCP\b", mud_h.comment_stripped)
        ):
            toolchain_issues.append("src/mud.h #define MCCP not wrapped in #ifndef MCCP")
        for dead in DEAD_FILES_TO_REMOVE:
            if (repo / dead).exists():
                if Path(dead).name in mk.c_files:
                    toolchain_issues.append(f"Legacy file still compiled by src/Makefile: {dead}")
                else:
                    excluded_toolchain_files.append(dead)

    summary = {
        "commands_dat_count": len(cmds),
        "skills_dat_count": len(skills),
        "craft_recipes_count": len(crafts),
        "commands_broken_count": len(commands_broken),
        "skills_broken_count": len(skills_broken),
        "cpp_do_missing_from_tables_count": len(cpp_do_missing_from_tables),
        "cpp_do_missing_from_reverse_skill_name_count": len(cpp_do_missing_from_rev),
        "cpp_do_missing_from_dat_count": len(cpp_do_missing_from_dat),
        "cpp_spell_missing_from_tables_count": len(cpp_spell_missing_from_tables),
        "cpp_spell_missing_from_reverse_spell_name_count": len(cpp_spell_missing_from_rev),
        "cpp_spell_missing_from_dat_count": len(cpp_spell_missing_from_dat),
        "declared_but_undefined_count": len(declared_but_undefined),
        "spec_missing_from_lookup_count": len(spec_missing_from_lookup),
        "do_make_total_count": len(do_make_total),
        "do_make_retired_craft_count": len(do_make_retired),
        "registered_retired_craft_count": len(registered_retired_craft),
        "registry_issues_count": len(registry_issues),
        "craft_recipes_broken_count": len(craft_recipes_broken),
        "missing_assign_gsn_count": len(missing_assign_gsns),
        "live_not_implemented_stubs_count": len(live_not_implemented_stubs),
        "toolchain_issues_count": len(toolchain_issues),
    }

    if mode == "baseline":
        violation_count = (
            len(commands_broken)
            + len(cpp_do_missing_from_tables)
            + len(cpp_spell_missing_from_tables)
            + len(declared_but_undefined)
            + len(spec_missing_from_lookup)
            + len(craft_recipes_broken)
            + len(missing_assign_gsns)
        )
    else:
        violation_count = (
            len(commands_broken)
            + len(skills_broken)
            + len(registry_issues)
            + len(declared_but_undefined)
            + len(spec_missing_from_lookup)
            + len(registered_retired_craft)
            + len(craft_recipes_broken)
            + len(missing_assign_gsns)
            + len(live_not_implemented_stubs)
            + len(toolchain_issues)
        )

    return {
        "scanner": "command_skill_wiring_audit",
        "scope": "system/{commands.dat,skills.dat,craft_recipes.dat}, src/tables.c, src/special.c, src/mud.h, src/*.c, area/*",
        "blocking_count": violation_count,
        "summary": summary,
        "findings": {
            "commands_broken": commands_broken,
            "skills_broken": skills_broken,
            "cpp_do_missing_from_tables": cpp_do_missing_from_tables,
            "cpp_do_missing_from_reverse_skill_name": cpp_do_missing_from_rev,
            "cpp_do_missing_from_dat": cpp_do_missing_from_dat,
            "cpp_spell_missing_from_tables": cpp_spell_missing_from_tables,
            "cpp_spell_missing_from_reverse_spell_name": cpp_spell_missing_from_rev,
            "cpp_spell_missing_from_dat": cpp_spell_missing_from_dat,
            "declared_but_undefined": declared_but_undefined,
            "spec_missing_from_lookup": spec_missing_from_lookup,
            "do_make_leftovers": do_make_retired,
            "registered_retired_craft": registered_retired_craft,
            "registry_issues": registry_issues,
            "excluded_toolchain_files": excluded_toolchain_files,
            "craft_recipes_broken": craft_recipes_broken,
            "missing_assign_gsns": missing_assign_gsns,
            "live_not_implemented_stubs": live_not_implemented_stubs,
            "toolchain_issues": toolchain_issues,
        },
        "passed": violation_count == 0,
    }


# ---------------------------------------------------------------------------
# Scanner 4: const_and_format_audit
# ---------------------------------------------------------------------------

PRINTF_FAMILY: dict[str, tuple[int, bool]] = {
    "bug": (0, True),
    "log_printf": (0, True),
    "log_printf_plus": (2, True),
    "log_channelf": (3, True),
    "ch_printf_": (1, True),
    "str_printf_": (0, True),
    "pager_printf": (1, True),
    "progbugf": (1, True),
    "fprintf": (1, True),
    "sprintf": (1, True),
    "snprintf": (2, True),
    "SPRINTF_RUNTIME": (1, True),
    "SPRINTF": (1, True),
    "STRAPP": (1, True),
    "TELLOG": (1, True),
    "ch_printf": (1, False),
    "str_printf": (0, False),
}

STD_STRING_RETURNING_FUNCS = {
    "str_printf",
    "capitalize",
    "strlower",
    "strupper",
    "aoran",
    "lang_string",
    "craft_item_type_name",
    "to_string",
    "std::to_string",
}

PRINTF_SPEC_RE = re.compile(
    r"%(?:%|(?:\d+\$)?[-+ #0']*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:hh|h|ll|l|j|z|t|L)?[diuoxXfFeEgGaAcspn])"
)


def _collect_string_macros(files: list[SourceFile]) -> dict[str, str]:
    macros: dict[str, str] = {}
    for sf in files:
        if not sf.in_makefile:
            continue
        for m in re.finditer(r'^\s*#\s*define\s+([A-Za-z_]\w*)\s+("([^"\\]|\\.)*")\s*$', sf.active_text, re.MULTILINE):
            macros[m.group(1)] = m.group(2)[1:-1]
    return macros


def _resolve_compile_time_literal(expr: str, str_macros: dict[str, str]) -> str | None:
    expr = expr.strip()
    if not expr:
        return None
    parts: list[str] = []
    i = 0
    n = len(expr)
    while i < n:
        if expr[i].isspace():
            i += 1
            continue
        if expr[i] == '"':
            j = i + 1
            while j < n:
                if expr[j] == "\\" and j + 1 < n:
                    j += 2
                elif expr[j] == '"':
                    break
                else:
                    j += 1
            if j >= n:
                return None
            parts.append(expr[i + 1 : j])
            i = j + 1
        elif expr[i].isalpha() or expr[i] == "_":
            j = i + 1
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            tok = expr[i:j]
            if tok in str_macros:
                parts.append(str_macros[tok])
                i = j
            else:
                return None
        else:
            return None
    return "".join(parts) if parts else None


def _parse_printf_specs(fmt: str) -> list[str]:
    specs: list[str] = []
    for m in PRINTF_SPEC_RE.finditer(fmt):
        tok = m.group(0)
        if tok == "%%":
            continue
        # Count '*' width/precision placeholders (each expects int)
        stars = tok.count("*")
        for _ in range(stars):
            specs.append("%*int")
        specs.append(tok)
    return specs


def _infer_arg_type(arg: str, local_vars: dict[str, str], db: StructDB) -> str:
    s = arg.strip()
    if not s:
        return "unknown"
    if s.startswith('"') or s.endswith(".c_str()") or s.endswith(".data()"):
        return "char*"
    if re.fullmatch(r"[-+]?\d+", s) or re.fullmatch(r"0x[0-9A-Fa-f]+", s):
        return "int"
    if re.fullmatch(r"[-+]?\d+[lL]", s):
        return "long"
    if re.fullmatch(r"[-+]?\d+[uU][lL]", s):
        return "ulong"
    if re.fullmatch(r"[-+]?\d+[uU]", s):
        return "uint"
    if s.startswith("sizeof(") or s.startswith("sizeof "):
        return "size_t"
    # Explicit C-style cast
    m_cast = re.match(r"^\(\s*([A-Za-z_][\w\s*]+?)\s*\)\s*(.+)$", s)
    if m_cast:
        ct = re.sub(r"\s+", " ", m_cast.group(1)).strip()
        if "size_t" in ct:
            return "size_t"
        if "long long" in ct:
            return "long long"
        if "long" in ct:
            return "long"
        if "char *" in ct or "char*" in ct:
            return "char*"
        if ct in {"int", "short", "sh_int"}:
            return "int"
    # Function call returning std::string
    m_call = re.match(r"^([A-Za-z_:]+)\s*\(", s)
    if m_call and m_call.group(1) in STD_STRING_RETURNING_FUNCS:
        return "std::string"
    if ".size()" in s or ".length()" in s:
        return "size_t"

    # Variable / member lookup
    is_subscript = bool(re.search(r"\]\s*$", s))
    _, fl, leaf_t = resolve_expr_member(s, local_vars, db)
    t = leaf_t or local_vars.get(re.sub(r"\[[^\]]*\]", "", s))
    if not t and fl:
        # Fallback common mud.h struct field types
        if fl in {"outtop", "insize", "outsize"}:
            return "size_t"
        if fl in {"vnum", "top_level", "level", "price", "enlistroom1", "enlistroom2"}:
            return "int"
        if fl in {"gold", "bank", "base_value", "xpgain"}:
            return "long"
    if t:
        tl = t.strip()
        if is_subscript and (
            ("std::string" in tl and "[]" not in tl and "*" not in tl)
            or re.fullmatch(r"(?:const\s+)?char\s*(?:\*|\[\])", tl)
        ):
            return "int"
        if "std::string" in tl and "*" not in tl:
            return "std::string"
        if "char *" in tl or "char*" in tl or "char []" in tl:
            return "char*"
        if "size_t" in tl:
            return "size_t"
        if "long long" in tl:
            return "long long"
        if "long" in tl:
            return "long"
        if re.search(r"\b(?:int|sh_int|short|bool)\b", tl):
            return "int"
    return "unknown"


def _spec_type_mismatch(spec: str, arg_type: str, *, is_raw_varargs: bool = True) -> str | None:
    if arg_type == "unknown":
        return None
    if spec == "%*int":
        return None if arg_type in {"int", "uint"} else f"width_*_expects_int_got_{arg_type}"
    conv = spec[-1]
    length = ""
    m_len = re.search(r"(hh|h|ll|l|j|z|t|L)[diuoxXfFeEgGaAcspn]$", spec)
    if m_len:
        length = m_len.group(1)

    if conv == "s":
        if arg_type in {"int", "uint", "long", "ulong", "long long", "size_t"}:
            return f"%s_expects_string_got_{arg_type}"
        if arg_type == "std::string" and is_raw_varargs:
            return "%s_got_std_string"
    elif conv in {"d", "i"}:
        if length == "" and arg_type in {"long", "ulong", "long long", "size_t", "char*", "std::string"}:
            return f"%{conv}_expects_int_got_{arg_type}"
        if length == "l" and arg_type in {"int", "uint", "size_t", "char*", "std::string"}:
            return f"%l{conv}_expects_long_got_{arg_type}"
        if length == "z" and arg_type in {"int", "long", "char*", "std::string"}:
            return f"%z{conv}_expects_size_t_got_{arg_type}"
    elif conv in {"u", "x", "X", "o"}:
        if length == "" and arg_type in {"long", "ulong", "long long", "size_t", "char*", "std::string"}:
            return f"%{conv}_expects_uint_got_{arg_type}"
        if length == "l" and arg_type in {"int", "uint", "size_t", "char*", "std::string"}:
            return f"%l{conv}_expects_ulong_got_{arg_type}"
    return None


def run_const_format_scanner(
    repo: Path,
    mk: MakefileInfo,
    files: list[SourceFile],
    mode: str,
) -> dict[str, Any]:
    db = parse_struct_db(files)
    str_macros = _collect_string_macros(files)

    # 1. Analyze do_* functions for argument mutations
    do_funcs: dict[str, dict[str, Any]] = {}
    for sf in files:
        if sf.is_header or not sf.in_makefile:
            continue
        text = sf.active_text
        for fname, params, bo, bc in extract_function_spans(text):
            if not fname.startswith("do_"):
                continue
            m_sig = re.match(
                r"^\s*CHAR_DATA\s*\*\s*[A-Za-z0-9_]*\s*,\s*(?:const\s+)?char\s*\*\s*([A-Za-z0-9_]+)\s*$",
                params.strip(),
            )
            if not m_sig:
                continue
            p2 = m_sig.group(1)
            body = text[bo + 1 : bc]
            mutations: list[dict[str, Any]] = []
            mut_patterns = [
                ("array_write", rf"\b{re.escape(p2)}\s*\[[^\]]+\]\s*=(?!=)"),
                ("deref_write", rf"\*\s*{re.escape(p2)}\s*=(?!=)"),
                ("ptr_inc_dec", rf"(?:\+\+|--)\s*{re.escape(p2)}\b|\b{re.escape(p2)}\s*(?:\+\+|--)"),
                ("mutating_call", rf"\b(?:strcpy|strcat|sprintf|smash_tilde|hide_tilde)\s*\(\s*{re.escape(p2)}\b"),
            ]
            for kind, pat in mut_patterns:
                for mm in re.finditer(pat, body):
                    ln = line_of_offset(sf.newline_offsets, bo + 1 + mm.start())
                    mutations.append({"kind": kind, "var": p2, "line": ln, "snippet": mm.group(0)})
            do_funcs[fname] = {
                "file": sf.rel_path,
                "line": line_of_offset(sf.newline_offsets, bo),
                "in_makefile": sf.in_makefile,
                "param_name": p2,
                "mutations": mutations,
            }

    mutating_do = {k: v for k, v in do_funcs.items() if v["mutations"]}
    mutating_const_calls: list[dict[str, Any]] = []
    nonmutating_const_calls: list[dict[str, Any]] = []

    std_string_in_c_varargs: list[dict[str, Any]] = []
    format_mismatches: list[dict[str, Any]] = []
    non_literal_format_with_varargs: list[dict[str, Any]] = []
    legacy_bug_dummy_zero: list[dict[str, Any]] = []

    # Build structural macro & forwarding-template exclusions
    FORWARDING_TEMPLATE_FUNCS = {
        "str_printf_",
        "ch_printf_from_tuple",
        "ch_printf",
        "str_printf_from_tuple",
        "str_printf",
        "vstr_printf",
        "mud_snprintf_runtime",
        "mud_vsnprintf",
    }
    printf_names_pat = "|".join(re.escape(k) for k in PRINTF_FAMILY)
    call_re = re.compile(rf"\b({printf_names_pat})\s*\(")

    for sf in files:
        include_file = sf.in_makefile or (mode == "baseline")
        if not include_file:
            continue
        text = sf.active_text
        active_lines = text.split("\n")
        raw_lines = sf.raw.split("\n")
        macro_lines: set[int] = set()
        in_macro = False
        for idx_l, raw_l in enumerate(active_lines, start=1):
            if not in_macro and re.match(r"^\s*#\s*define\b", raw_l):
                in_macro = True
            if in_macro:
                macro_lines.add(idx_l)
                in_macro = raw_l.rstrip("\r").endswith("\\")

        # do_* const call sites
        for m_dc in re.finditer(r"\b(do_[A-Za-z0-9_]+)\s*\(", text):
            callee = m_dc.group(1)
            if callee not in do_funcs:
                continue
            args, _ = extract_call_args(text, m_dc.end() - 1)
            if len(args) != 2:
                continue
            arg2 = args[1].strip()
            const_kind = None
            if arg2.startswith('"'):
                const_kind = "string_literal"
            elif ".c_str()" in arg2:
                const_kind = "c_str_cast"
            elif "const_cast" in arg2:
                const_kind = "const_cast"
            if const_kind:
                ln = line_of_offset(sf.newline_offsets, m_dc.start())
                rec = {
                    "file": sf.rel_path,
                    "in_makefile": sf.in_makefile,
                    "line": ln,
                    "callee": callee,
                    "arg_expr": arg2,
                    "const_kind": const_kind,
                }
                if callee in mutating_do:
                    mutating_const_calls.append(rec)
                else:
                    nonmutating_const_calls.append(rec)

        # Function spans for local variable type inference & template exclusion
        fn_spans = extract_function_spans(text)

        def _vars_at(pos: int) -> dict[str, str]:
            for _, params, bo, bc in fn_spans:
                if bo <= pos <= bc:
                    return infer_local_var_types(params, text[bo + 1 : bc])
            return {}

        def _enclosing_fn(pos: int) -> tuple[str, str]:
            for fn_name, params, bo, bc in fn_spans:
                if bo <= pos <= bc:
                    return fn_name, params
            return "", ""

        for m_call in call_re.finditer(text):
            fn = m_call.group(1)
            ln = line_of_offset(sf.newline_offsets, m_call.start())
            if ln in macro_lines:
                continue

            enc_fn, enc_params = _enclosing_fn(m_call.start())
            if enc_fn in FORWARDING_TEMPLATE_FUNCS or re.search(
                r"\b(?:Args|Tuple)\b|\bva_list\b", enc_params
            ):
                continue

            # Exclude function declarations/definitions (`void bug(...)` etc.)
            pre_slice = text[max(0, m_call.start() - 180) : m_call.start()]
            last_sep = max(pre_slice.rfind(";"), pre_slice.rfind("{"), pre_slice.rfind("}"))
            if last_sep >= 0:
                pre_slice = pre_slice[last_sep + 1 :]
            pre_lines = [l for l in pre_slice.split("\n") if not l.lstrip().startswith("#")]
            pre_stmt = " ".join(pre_lines).strip()
            if re.search(
                r"(?:^|>)\s*(?:(?:extern|static|inline|virtual|constexpr)\s+)*"
                r"(?:void|int|bool|(?:const\s+)?char\s*\*|std::string)$",
                pre_stmt,
            ):
                continue

            fmt_idx, is_raw_varargs = PRINTF_FAMILY[fn]
            args, _ = extract_call_args(text, m_call.end() - 1)
            if len(args) <= fmt_idx:
                continue

            if args and (
                args[-1].strip() == "..."
                or re.match(r"^(?:const\s+)?char\s*\*\s*[A-Za-z_]\w*$", args[fmt_idx].strip())
            ):
                continue

            # Exclude 2-arg `ch_printf(ch, txt)` overload
            if fn == "ch_printf" and len(args) == 2:
                continue

            fmt_expr = args[fmt_idx].strip()
            varargs = args[fmt_idx + 1 :]
            lvars = _vars_at(m_call.start())

            # std::string in raw C varargs
            if is_raw_varargs and varargs:
                for a in varargs:
                    atype = _infer_arg_type(a, lvars, db)
                    if atype == "std::string":
                        std_string_in_c_varargs.append(
                            {
                                "file": sf.rel_path,
                                "in_makefile": sf.in_makefile,
                                "line": ln,
                                "function": fn,
                                "arg_expr": a,
                                "snippet": raw_lines[ln - 1].strip() if ln <= len(raw_lines) else "",
                            }
                        )

            literal_fmt = _resolve_compile_time_literal(fmt_expr, str_macros)
            if literal_fmt is None:
                if varargs and not (fn == "bug" and len(varargs) == 1 and varargs[0].strip() == "0"):
                    non_literal_format_with_varargs.append(
                        {
                            "file": sf.rel_path,
                            "in_makefile": sf.in_makefile,
                            "line": ln,
                            "function": fn,
                            "fmt_expr": fmt_expr,
                            "arg_count": len(varargs),
                        }
                    )
                continue

            specs = _parse_printf_specs(literal_fmt)
            if fn == "bug" and len(specs) == 0 and len(varargs) == 1 and varargs[0].strip() == "0":
                legacy_bug_dummy_zero.append(
                    {"file": sf.rel_path, "in_makefile": sf.in_makefile, "line": ln}
                )
                continue

            if len(specs) != len(varargs):
                format_mismatches.append(
                    {
                        "file": sf.rel_path,
                        "in_makefile": sf.in_makefile,
                        "line": ln,
                        "function": fn,
                        "issue": "specifier_count_mismatch",
                        "expected_args": len(specs),
                        "actual_args": len(varargs),
                        "format": literal_fmt,
                        "args": varargs,
                    }
                )
                continue

            # Type compatibility check
            # Map positional varargs (skipping width_* placeholders if matched)
            for spec, a in zip(specs, varargs):
                atype = _infer_arg_type(a, lvars, db)
                mismatch = _spec_type_mismatch(spec, atype, is_raw_varargs=is_raw_varargs)
                if mismatch:
                    format_mismatches.append(
                        {
                            "file": sf.rel_path,
                            "in_makefile": sf.in_makefile,
                            "line": ln,
                            "function": fn,
                            "issue": "specifier_type_mismatch",
                            "detail": mismatch,
                            "specifier": spec,
                            "arg_expr": a,
                            "inferred_arg_type": atype,
                            "format": literal_fmt,
                        }
                    )

    active_std_str = [x for x in std_string_in_c_varargs if x["in_makefile"]]
    active_fmt = [x for x in format_mismatches if x["in_makefile"]]
    active_nonlit = [x for x in non_literal_format_with_varargs if x["in_makefile"]]

    # Check if bug() in mud.h has __attribute__((format(printf, ...)))
    mud_h = next((f for f in files if f.rel_path == "src/mud.h"), None)
    bug_has_format_attr = bool(
        mud_h and re.search(r"void\s+bug\s*(?:args\s*)?\([^;]*\)\s*__attribute__\s*\(\(\s*format\s*\(\s*printf", mud_h.active_text)
    )

    summary = {
        "do_functions_analyzed": len(do_funcs),
        "mutating_do_functions_count": len(mutating_do),
        "mutating_do_const_call_sites_count": len(mutating_const_calls),
        "nonmutating_do_const_call_sites_count": len(nonmutating_const_calls),
        "std_string_in_c_varargs_count": len(std_string_in_c_varargs),
        "format_mismatches_count": len(format_mismatches),
        "non_literal_format_with_varargs_count": len(non_literal_format_with_varargs),
        "legacy_bug_dummy_zero_count": len(legacy_bug_dummy_zero),
        "active_std_string_in_c_varargs_count": len(active_std_str),
        "active_format_mismatches_count": len(active_fmt),
        "active_non_literal_format_with_varargs_count": len(active_nonlit),
    }

    if mode == "baseline":
        violation_count = (
            len(mutating_const_calls)
            + len(std_string_in_c_varargs)
            + len(format_mismatches)
        )
    else:
        violation_count = (
            len(mutating_const_calls)
            + len(active_std_str)
            + len(active_fmt)
            + len(active_nonlit)
            + (len(legacy_bug_dummy_zero) if bug_has_format_attr else 0)
        )

    return {
        "scanner": "const_and_format_audit",
        "scope": "src/*.h and src/*.c",
        "summary": summary,
        "findings": {
            "mutating_do_functions": mutating_do,
            "mutating_do_const_call_sites": mutating_const_calls,
            "nonmutating_do_const_call_sites": nonmutating_const_calls,
            "std_string_in_c_varargs": std_string_in_c_varargs,
            "format_mismatches": format_mismatches,
            "non_literal_format_with_varargs": non_literal_format_with_varargs,
        },
        "passed": violation_count == 0,
    }


# ---------------------------------------------------------------------------
# Scanner 5: persistence_and_vnum_audit
# ---------------------------------------------------------------------------

LEGACY_COMPAT_TAGS: set[str] = {
    "Act",
    "Affect",
    "AffectData",
    "AffectedBy",
    "CommandGroup",
    "Deaf",
    "Flags",
    "Guild",
    "Immune",
    "Languages",
    "Resistant",
    "SavingThrow",
    "Susceptible",
    "Wanted",
    "ExtraFlags",
    "WearFlags",
    "Saveflags",
    "Taxes",
    "Laserstr",
    "Missiletype",
}

# These compatibility records are accepted by the corresponding readers but no
# longer emitted. Ship numeric families are handled by prefix in fread_ship_internal.
LEGACY_TAGS_BY_ENTITY = {
    "player_char": {"Mobinvis"},
    "ship": {"Cargohold", "Objectnum", *(f"Statet{i}" for i in range(1, 10)),
             *(f"Turret{i}" for i in range(10)), *(f"Cargo{i}" for i in range(10))},
}

PERSISTENCE_PAIRS: list[tuple[str, str, str, str, str]] = [
    ("player_char", "src/save.c", "fwrite_char", "src/save.c", "fread_char"),
    ("object", "src/save.c", "fwrite_obj", "src/save.c", "fread_obj_versioned"),
    ("mobile_pet", "src/save.c", "fwrite_mobile", "src/save.c", "fread_mobile"),
    ("comments", "src/comments.c", "fwrite_comments", "src/comments.c", "fread_comment"),
    ("sysdata", "src/db.c", "save_sysdata", "src/db.c", "fread_sysdata"),
    ("clan", "src/clans.c", "save_clan_file", "src/clans.c", "fread_clan"),
    ("planet", "src/clans.c", "save_planet_file", "src/clans.c", "fread_planet"),
    ("member_list", "src/clans.c", "save_member_list_file", "src/clans.c", "load_member_list"),
    ("spaceobject", "src/space.c", "save_spaceobject", "src/space.c", "fread_spaceobject"),
    ("ship", "src/space.c", "save_ship_file", "src/space.c", "fread_ship_internal"),
    ("boards_index", "src/boards.c", "write_boards_txt", "src/boards.c", "read_board"),
    ("board_notes", "src/boards.c", "write_board", "src/boards.c", "read_note"),
    ("craft_recipe", "src/craft.c", "save_one_craft_recipe", "src/craft.c", "load_one_craft_recipe"),
]


def _extract_writer_tags(body: str, base_offset: int, sf: SourceFile) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r'fprintf\s*\(\s*\w+\s*,\s*"((?:[^"\\]|\\.)*)"', body):
        fmt = m.group(1)
        for tm in re.finditer(r'(?:^|\\n)([A-Za-z][A-Za-z0-9_]*)(?=\s|\\t|\\n|$)', fmt):
            tag = tm.group(1)
            if tag in {"End", "EndMobile"}:
                continue
            if tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(
                    {
                        "tag": tag,
                        "line": line_of_offset(sf.newline_offsets, base_offset + m.start()),
                        "file": sf.rel_path,
                    }
                )
    for m in re.finditer(r'fwrite_bitset\s*\(\s*\w+\s*,\s*"([A-Za-z][A-Za-z0-9_]*)"', body):
        tag = m.group(1)
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(
                {
                    "tag": tag,
                    "line": line_of_offset(sf.newline_offsets, base_offset + m.start()),
                    "file": sf.rel_path,
                }
            )
    return tags


def _extract_reader_tags(body: str, base_offset: int, sf: SourceFile) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r'(?:\bKEY|\bSKEY)\s*\(\s*"([A-Za-z][A-Za-z0-9_]*)"', body):
        tag = m.group(1)
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append({"tag": tag, "line": line_of_offset(sf.newline_offsets, base_offset + m.start()), "file": sf.rel_path})
    for m in re.finditer(r'!?str_cmp\s*\(\s*(?:word|fread_word\s*\(\s*\w+\s*\))\s*,\s*"([A-Za-z][A-Za-z0-9_]*)"\s*\)', body):
        tag = m.group(1)
        if tag in {"End", "END"}:
            continue
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append({"tag": tag, "line": line_of_offset(sf.newline_offsets, base_offset + m.start()), "file": sf.rel_path})
    return tags


def _find_switch_missing_breaks(body: str, base_offset: int, sf: SourceFile) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    m_sw = re.search(r"\bswitch\s*\(\s*UPPER\s*\(\s*word\s*\[\s*0\s*\]\s*\)\s*\)\s*\{", body)
    if not m_sw:
        return issues
    sw_open = m_sw.end() - 1
    sw_close = find_matching_brace(body, sw_open)
    sw_body = body[sw_open + 1 : sw_close]
    case_matches = list(re.finditer(r"^\s*(case\s+'[^']'\s*:|default\s*:)", sw_body, re.MULTILINE))
    for idx, cm in enumerate(case_matches):
        start = cm.end()
        end = case_matches[idx + 1].start() if idx + 1 < len(case_matches) else len(sw_body)
        chunk = sw_body[start:end].strip()
        if not chunk:
            # Stacked empty case label (`case 'X':\n case 'Y':`)
            continue
        if cm.group(1).startswith("default"):
            continue
        # Check if chunk ends with break or return (outside nested loops)
        if not re.search(r"\b(?:break|return)\b[^;{}]*;\s*$", chunk):
            # Also check if there's an unguarded fallthrough at top level
            lines_chunk = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            last_ln = lines_chunk[-1] if lines_chunk else ""
            if last_ln not in {"break;", "return;", "return NULL;", "return TRUE;", "return FALSE;"} and not last_ln.startswith("return "):
                ln_no = line_of_offset(sf.newline_offsets, base_offset + sw_open + 1 + cm.start())
                nxt_lbl = case_matches[idx + 1].group(1) if idx + 1 < len(case_matches) else "EOF"
                issues.append(
                    {
                        "line": ln_no,
                        "case_label": cm.group(1),
                        "next_case": nxt_lbl,
                    }
                )
    return issues


class _AreaParser:
    """Line-by-line section-aware SMAUG/SWR `.are` parser."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        self.n = len(self.lines)
        self.idx = 0

        self.mobs: dict[int, int] = {}
        self.objs: dict[int, int] = {}
        self.rooms: dict[int, dict[str, Any]] = {}
        self.exits: list[dict[str, Any]] = []
        self.resets: list[dict[str, Any]] = []
        self.shops: list[dict[str, Any]] = []
        self.specials: list[dict[str, Any]] = []
        self.mudprog_refs: list[dict[str, Any]] = []
        self.within_file_dups: list[dict[str, Any]] = []
        self.parse_errors: list[str] = []

    def _eof(self) -> bool:
        return self.idx >= self.n

    def _next_nonempty(self) -> tuple[int, str] | None:
        while self.idx < self.n:
            ln = self.lines[self.idx]
            ln_no = self.idx + 1
            self.idx += 1
            if ln.strip():
                return ln_no, ln
        return None

    def _consume_tilde(self) -> None:
        while self.idx < self.n:
            ln = self.lines[self.idx]
            self.idx += 1
            if "~" in ln:
                return

    def _consume_line(self) -> str:
        if self.idx >= self.n:
            return ""
        ln = self.lines[self.idx]
        self.idx += 1
        return ln

    def _consume_mudprog_block(self, owner_kind: str, owner_vnum: int) -> None:
        """Consume consecutive `>` mudprog triggers until `|` (or next `#`/non-prog)."""
        while self.idx < self.n:
            s = self.lines[self.idx].strip()
            if not s:
                self.idx += 1
                continue
            if s.startswith("|"):
                self.idx += 1
                return
            if not s.startswith(">"):
                return
            # Consume `> trigger ...~` header
            if "~" not in self.lines[self.idx]:
                self.idx += 1
                self._consume_tilde()
            else:
                self.idx += 1
            # Consume prog body lines until trailing `~`
            while self.idx < self.n:
                pln = self.lines[self.idx]
                pln_no = self.idx + 1
                self.idx += 1
                self._scan_mudprog_line(pln, pln_no, owner_kind, owner_vnum)
                if "~" in pln:
                    break

    def _scan_mudprog_line(self, line: str, line_no: int, owner_kind: str, owner_vnum: int) -> None:
        clean = line.replace("~", " ").strip()
        if not clean or clean.startswith("*"):
            return
        tokens = clean.split()
        i = 0
        while i < len(tokens):
            cmd = tokens[i].lower()
            if cmd in {"mpoload", "mpmload", "mpat", "mpgoto"}:
                if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                    vnum = int(tokens[i + 1])
                    if vnum > 0:
                        ref_kind = "obj" if cmd == "mpoload" else ("mob" if cmd == "mpmload" else "room")
                        self.mudprog_refs.append(
                            {
                                "line": line_no,
                                "owner": f"{owner_kind}:{owner_vnum}",
                                "cmd": cmd,
                                "ref_kind": ref_kind,
                                "target_vnum": vnum,
                            }
                        )
                    i += 2
                    continue
            elif cmd == "mptransfer":
                if i + 2 < len(tokens) and tokens[i + 2].isdigit():
                    vnum = int(tokens[i + 2])
                    if vnum > 0:
                        self.mudprog_refs.append(
                            {
                                "line": line_no,
                                "owner": f"{owner_kind}:{owner_vnum}",
                                "cmd": cmd,
                                "ref_kind": "room",
                                "target_vnum": vnum,
                            }
                        )
                    i += 3
                    continue
            i += 1

    def parse(self) -> None:
        while not self._eof():
            item = self._next_nonempty()
            if item is None:
                break
            _, ln = item
            s = ln.strip()
            if s == "#$":
                break
            if s == "#MOBILES":
                self._parse_mobiles()
            elif s == "#OBJECTS":
                self._parse_objects()
            elif s == "#ROOMS":
                self._parse_rooms()
            elif s == "#RESETS":
                self._parse_resets()
            elif s in {"#SHOPS", "#REPAIRS"}:
                self._parse_shops()
            elif s == "#SPECIALS":
                self._parse_specials()

    def _consume_possible_bitset_line(self) -> None:
        ln = self._consume_line().strip()
        nums = ln.split()
        if nums and nums[0] == "-9999":
            # Consumes bitset line(s)
            bs = self._consume_line()
            while "-1" not in bs.split() and self.idx < self.n:
                bs = self._consume_line()

    def _parse_mobiles(self) -> None:
        while not self._eof():
            item = self._next_nonempty()
            if item is None:
                return
            ln_no, ln = item
            s = ln.strip()
            if s == "#0":
                return
            m_v = re.match(r"^#(\d+)\s*$", s)
            if not m_v:
                continue
            vnum = int(m_v.group(1))
            if vnum == 0:
                return
            if vnum in self.mobs:
                self.within_file_dups.append(
                    {"kind": "mob", "vnum": vnum, "first_line": self.mobs[vnum], "second_line": ln_no}
                )
            else:
                self.mobs[vnum] = ln_no
            # 4 tilde strings: player_name, short_descr, long_descr, description
            for _ in range(4):
                self._consume_tilde()
            # act, affected_by (maybe -9999), alignment + letter
            l1 = self._consume_line().split()
            if l1 and l1[0] == "-9999":
                self._consume_line()  # actex
                self._consume_line()  # affectedex
                self._consume_line()  # alignment/letter
            else:
                # May contain act, affected, alignment letter on 1 or more lines
                # Check if letter S/C/Z is already at end of l1
                if not (l1 and l1[-1] in {"S", "C", "Z"}):
                    self._consume_line()
            # Level/hitroll/ac/hp/dam
            self._consume_line()
            self._consume_line()
            # Gold/exp
            self._consume_line()
            # Position/defpos/sex
            self._consume_line()
            # Check for Z / C extra stats or > mudprogs
            while not self._eof():
                peek = self.lines[self.idx].strip()
                if not peek:
                    self.idx += 1
                    continue
                if peek.startswith("#"):
                    break
                if peek.startswith(">"):
                    self._consume_mudprog_block("mob", vnum)
                    continue
                if peek.startswith("|"):
                    self.idx += 1
                    continue
                # Consume Z/C lines (e.g. stats, speaks, resistant)
                self.idx += 1

    def _parse_objects(self) -> None:
        while not self._eof():
            item = self._next_nonempty()
            if item is None:
                return
            ln_no, ln = item
            s = ln.strip()
            if s == "#0":
                return
            m_v = re.match(r"^#(\d+)\s*$", s)
            if not m_v:
                continue
            vnum = int(m_v.group(1))
            if vnum == 0:
                return
            if vnum in self.objs:
                self.within_file_dups.append(
                    {"kind": "obj", "vnum": vnum, "first_line": self.objs[vnum], "second_line": ln_no}
                )
            else:
                self.objs[vnum] = ln_no
            # 4 tilde strings: name, short, long, action_desc
            for _ in range(4):
                self._consume_tilde()
            # type / extra_flags / wear_flags [/ layers]
            fl_line = self._consume_line().split()
            if "-9999" in fl_line:
                self._consume_line()  # objflags
                self._consume_line()  # wear_flags
            # values + weight/cost/rent
            self._consume_line()
            self._consume_line()
            while not self._eof():
                peek = self.lines[self.idx].strip()
                if not peek:
                    self.idx += 1
                    continue
                if peek.startswith("#"):
                    break
                if peek == "A":
                    self.idx += 1
                    self._consume_line()
                elif peek == "E":
                    self.idx += 1
                    self._consume_tilde()
                    self._consume_tilde()
                elif peek == "T":
                    self.idx += 1
                    self._consume_line()
                elif peek.startswith(">"):
                    self._consume_mudprog_block("obj", vnum)
                elif peek.startswith("|"):
                    self.idx += 1
                else:
                    self.idx += 1

    def _parse_rooms(self) -> None:
        while not self._eof():
            item = self._next_nonempty()
            if item is None:
                return
            ln_no, ln = item
            s = ln.strip()
            if s == "#0":
                return
            m_v = re.match(r"^#(\d+)\s*$", s)
            if not m_v:
                continue
            vnum = int(m_v.group(1))
            if vnum == 0:
                return
            if vnum in self.rooms:
                self.within_file_dups.append(
                    {"kind": "room", "vnum": vnum, "first_line": self.rooms[vnum]["line"], "second_line": ln_no}
                )
            else:
                self.rooms[vnum] = {"line": ln_no, "exit_dirs": set()}
            # name~, desc~
            self._consume_tilde()
            self._consume_tilde()
            # area_num room_flags sector [tele_delay tele_vnum tunnel]
            rline = self._consume_line().split()
            if len(rline) >= 2 and rline[1] == "-9999":
                self._consume_line()  # room_flags bitset
                self._consume_line()  # sector / extras
            while not self._eof():
                peek_no = self.idx + 1
                peek = self.lines[self.idx].strip()
                if not peek:
                    self.idx += 1
                    continue
                if peek == "S":
                    self.idx += 1
                    break
                if peek.startswith("#"):
                    break
                if re.match(r"^D(\d+)$", peek):
                    dir_num = int(peek[1:])
                    self.rooms[vnum]["exit_dirs"].add(dir_num)
                    self.idx += 1
                    self._consume_tilde()
                    self._consume_tilde()
                    ex_ln_no = self.idx + 1
                    ex_vals = self._consume_line().split()
                    if len(ex_vals) >= 3:
                        try:
                            key_vnum = int(ex_vals[1])
                            to_vnum = int(ex_vals[2])
                            self.exits.append(
                                {
                                    "room": vnum,
                                    "dir": dir_num,
                                    "line": ex_ln_no,
                                    "key_vnum": key_vnum,
                                    "to_vnum": to_vnum,
                                }
                            )
                        except ValueError:
                            pass
                elif peek == "E":
                    self.idx += 1
                    self._consume_tilde()
                    self._consume_tilde()
                elif peek.startswith(">"):
                    self._consume_mudprog_block("room", vnum)
                elif peek.startswith("|"):
                    self.idx += 1
                elif peek.split()[0] == "M":
                    coords = peek.split()[1:4]
                    if len(coords) != 3 or any(not re.fullmatch(r"[+-]?\d+", v) for v in coords):
                        self.parse_errors.append(f"line {peek_no}: malformed map for room {vnum}")
                    self.idx += 1
                else:
                    self.idx += 1

    def _parse_resets(self) -> None:
        while not self._eof():
            ln_no = self.idx + 1
            ln = self._consume_line().strip()
            if not ln:
                continue
            if ln == "S":
                return
            if ln.startswith("*"):
                continue
            toks = ln.split()
            letter = toks[0]
            ints: list[int] = []
            for t in toks[1:]:
                try:
                    ints.append(int(t))
                except ValueError:
                    break
            if letter in {"G", "R"} and len(ints) >= 3:
                self.resets.append({"line": ln_no, "letter": letter, "arg1": ints[1], "arg2": ints[2], "arg3": 0})
            elif len(ints) >= 4:
                self.resets.append({"line": ln_no, "letter": letter, "arg1": ints[1], "arg2": ints[2], "arg3": ints[3]})

    def _parse_shops(self) -> None:
        while not self._eof():
            ln_no = self.idx + 1
            ln = self._consume_line().strip()
            if not ln:
                continue
            toks = ln.split()
            try:
                keeper = int(toks[0])
            except ValueError:
                continue
            if keeper == 0:
                return
            self.shops.append({"line": ln_no, "keeper": keeper})

    def _parse_specials(self) -> None:
        while not self._eof():
            ln_no = self.idx + 1
            ln = self._consume_line().strip()
            if not ln or ln.startswith("*"):
                continue
            if ln == "S":
                return
            toks = ln.split()
            if toks[0] == "M" and len(toks) >= 3:
                try:
                    self.specials.append({"line": ln_no, "mob_vnum": int(toks[1]), "spec_name": toks[2]})
                except ValueError:
                    pass


def _collect_area_object_vnums(repo: Path, *, active_only: bool = False) -> set[int]:
    area_dir = repo / "area"
    objs: set[int] = set()
    if not area_dir.is_dir():
        return objs
    paths = [area_dir / name for name in _read_list_file(area_dir / "area.lst")] if active_only else sorted(area_dir.iterdir())
    for p in paths:
        if not p.is_file() or p.name in {"area.lst", "shutdown.txt"}:
            continue
        parser = _AreaParser(p)
        parser.parse()
        objs.update(parser.objs.keys())
    return objs


def _read_list_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("*"):
            continue
        if s == "$":
            break
        out.append(s)
    return out


def _read_boards_txt_stanzas(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    stanzas: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s == "$":
            break
        m_fn = re.match(r"^Filename\s+(.+)$", s)
        if m_fn:
            cur = {"filename": m_fn.group(1).strip().rstrip("~")}
        elif cur is not None:
            if s == "End":
                stanzas.append(cur)
                cur = None
            else:
                parts_b = s.split(None, 1)
                if len(parts_b) == 2:
                    cur[parts_b[0].lower()] = parts_b[1].strip().rstrip("~")
    return stanzas


def run_persistence_vnum_scanner(
    repo: Path,
    mk: MakefileInfo,
    files: list[SourceFile],
    mode: str,
) -> dict[str, Any]:
    file_map = {f.rel_path: f for f in files}

    # 1. Persistence pairs & switch break checks
    pairs_out: list[dict[str, Any]] = []
    wo_count = 0
    ro_unexp_count = 0
    switch_breaks_count = 0
    missing_pairs: list[dict[str, str]] = []

    for entity, w_file, w_fn, r_file, r_fn in PERSISTENCE_PAIRS:
        wsf = file_map.get(w_file)
        rsf = file_map.get(r_file)
        w_tags: list[dict[str, Any]] = []
        r_tags: list[dict[str, Any]] = []
        w_start, w_end, r_start, r_end = 0, 0, 0, 0
        sw_issues: list[dict[str, Any]] = []

        if wsf:
            for fn, _, bo, bc in extract_function_spans(wsf.active_text):
                if fn == w_fn:
                    w_start = line_of_offset(wsf.newline_offsets, bo)
                    w_end = line_of_offset(wsf.newline_offsets, bc)
                    w_tags = _extract_writer_tags(wsf.active_text[bo + 1 : bc], bo + 1, wsf)
                    break
        if rsf:
            for fn, _, bo, bc in extract_function_spans(rsf.active_text):
                if fn == r_fn:
                    r_start = line_of_offset(rsf.newline_offsets, bo)
                    r_end = line_of_offset(rsf.newline_offsets, bc)
                    r_tags = _extract_reader_tags(rsf.active_text[bo + 1 : bc], bo + 1, rsf)
                    sw_issues = _find_switch_missing_breaks(rsf.active_text[bo + 1 : bc], bo + 1, rsf)
                    break

        if not w_start or not r_start:
            missing_pairs.append({"entity": entity, "writer": w_fn, "reader": r_fn})

        w_set = {t["tag"].lower() for t in w_tags}
        r_set = {t["tag"].lower() for t in r_tags}
        if entity == "ship":
            r_set.add("mod")  # #MODS sub-stanza is read by fread_mods()
        if entity == "mobile_pet":
            r_set.add("vnum")  # #MOBILE Vnum is read by caller before fread_mobile()
        legacy_lower = {x.lower() for x in LEGACY_COMPAT_TAGS | LEGACY_TAGS_BY_ENTITY.get(entity, set())}
        wo = [t for t in w_tags if t["tag"].lower() not in r_set]
        ro_legacy = [t for t in r_tags if t["tag"].lower() not in w_set and t["tag"].lower() in legacy_lower]
        ro_unexp = [t for t in r_tags if t["tag"].lower() not in w_set and t["tag"].lower() not in legacy_lower]

        wo_count += len(wo)
        ro_unexp_count += len(ro_unexp)
        switch_breaks_count += len(sw_issues)

        pairs_out.append(
            {
                "entity": entity,
                "writer": {"file": w_file, "start_line": w_start, "end_line": w_end, "tag_count": len(w_tags)},
                "reader": {"file": r_file, "start_line": r_start, "end_line": r_end, "tag_count": len(r_tags)},
                "write_only_tags": wo,
                "read_only_legacy_compat_tags": ro_legacy,
                "read_only_unexpected_tags": ro_unexp,
                "switch_missing_breaks": sw_issues,
            }
        )

    # 2. Semantic persistence checks (matching the 10 canonical semantic checks)
    semantic_bugs: list[dict[str, Any]] = []
    build_sf = file_map.get("src/build.c")
    db_sf = file_map.get("src/db.c")
    save_sf = file_map.get("src/save.c")
    space_sf = file_map.get("src/space.c")
    boards_sf = file_map.get("src/boards.c")
    clans_sf = file_map.get("src/clans.c")

    if build_sf and "rShop" in build_sf.active_text:
        semantic_bugs.append({"id": "PERSIST-AREA-REPAIRS", "file": "src/build.c", "detail": "fold_area references legacy rShop instead of pShop"})
    if build_sf and re.search(r'fprintf\s*\(\s*fpout\s*,\s*"M\s+%d', build_sf.active_text):
        if db_sf:
            spans_db_rooms = {n: db_sf.active_text[bo + 1 : bc] for n, _, bo, bc in extract_function_spans(db_sf.active_text)}
            load_rooms_body = spans_db_rooms.get("load_rooms", "")
            if not re.search(r"['\"]M['\"]", load_rooms_body):
                semantic_bugs.append({"id": "PERSIST-AREA-ROOM-MAP", "file": "src/build.c", "detail": "fold_area emits M room map unsupported by load_rooms"})
    if build_sf and re.search(r"rename\s*\((?:[^()]|\([^()]*\))*\)\s*;[\s\S]{0,120}FCLOSE\s*\(\s*fpout\s*\)", build_sf.active_text):
        semantic_bugs.append({"id": "PERSIST-AREA-RENAME-OPEN", "file": "src/build.c", "detail": "rename called before FCLOSE(fpout)"})
    if db_sf and re.search(r"load_repairs|load_shops|load_specials", db_sf.active_text):
        # Check load_shops / load_repairs / load_specials for get_mob_index NULL check
        spans_db = {n: db_sf.active_text[bo + 1 : bc] for n, _, bo, bc in extract_function_spans(db_sf.active_text)}
        for fn_name in ("load_shops", "load_repairs", "load_specials"):
            fn_body = spans_db.get(fn_name, "")
            if fn_body and "get_mob_index" in fn_body:
                has_null_guard = bool(
                    re.search(
                        r"(?:!\s*pMobIndex\b(?!->)|\bpMobIndex\s*(?:==|!=)\s*(?:NULL|nullptr|0)\b|\bif\s*\(\s*pMobIndex\s*\))",
                        fn_body,
                    )
                )
                if not has_null_guard:
                    semantic_bugs.append(
                        {
                            "id": "PERSIST-AREA-NULL-MOB-DEREF",
                            "file": "src/db.c",
                            "detail": f"{fn_name} missing NULL check on get_mob_index",
                        }
                    )
    if save_sf:
        save_spans = {n: save_sf.active_text[bo + 1 : bc] for n, _, bo, bc in extract_function_spans(save_sf.active_text)}
        fwc_body = save_spans.get("fwrite_char", "")
        fc_body = save_spans.get("fread_char", "")
        if fwc_body and re.search(r'fprintf\s*\(\s*fp\s*,\s*"Vnum\s+', fwc_body) and '"Vnum"' not in fc_body:
            semantic_bugs.append({"id": "PERSIST-SAVE-CHAR-SYMMETRY", "file": "src/save.c", "detail": "fwrite_char writes Vnum not handled in fread_char"})
        fm_body = save_spans.get("fread_mobile", "")
        if fm_body and _find_switch_missing_breaks(fm_body, 0, save_sf):
            semantic_bugs.append({"id": "PERSIST-SAVE-MOBILE-SWITCH", "file": "src/save.c", "detail": "fread_mobile switch missing breaks"})
    if space_sf:
        ss_body = dict((n, space_sf.active_text[bo + 1 : bc]) for n, _, bo, bc in extract_function_spans(space_sf.active_text)).get("save_ship_file", "")
        fs_body = dict((n, space_sf.active_text[bo + 1 : bc]) for n, _, bo, bc in extract_function_spans(space_sf.active_text)).get("fread_ship_internal", "")
        if ss_body and "#END" not in ss_body:
            semantic_bugs.append({"id": "PERSIST-SHIP-ROUNDTRIP", "file": "src/space.c", "detail": "save_ship_file omits #END"})
        elif fs_body and re.search(r'\bKEY\(\s*"Statei0"\s*,\s*ship->statet0\b', fs_body):
            semantic_bugs.append({"id": "PERSIST-SHIP-ROUNDTRIP", "file": "src/space.c", "detail": "fread_ship Statei0 writes ship->statet0"})
    if boards_sf:
        lb_body = dict((n, boards_sf.active_text[bo + 1 : bc]) for n, _, bo, bc in extract_function_spans(boards_sf.active_text)).get("load_boards", "")
        if lb_body and re.search(r"\blog_printf\s*\(\s*notefile\b", lb_body):
            semantic_bugs.append({"id": "PERSIST-BOARDS-LOAD", "file": "src/boards.c", "detail": "load_boards calls log_printf(notefile, ...)"})
    if clans_sf and re.search(r"void\s+(?:save_senate|load_senate)\s*\(", clans_sf.active_text):
        # Flag if save_senate or load_senate is a stub / empty body
        clans_spans = {n: clans_sf.active_text[bo + 1 : bc] for n, _, bo, bc in extract_function_spans(clans_sf.active_text)}
        for senate_fn in ("save_senate", "load_senate"):
            ss_c = clans_spans.get(senate_fn)
            if ss_c is not None and not ss_c.strip().strip(";"):
                semantic_bugs.append({"id": "PERSIST-CLANS-SENATE-PLANET", "file": "src/clans.c", "detail": f"{senate_fn} empty stub"})

    # 3. Area files & VNUM audit
    area_dir = repo / "area"
    active_area_names = _read_list_file(area_dir / "area.lst")
    all_area_files = sorted(
        p for p in area_dir.iterdir()
        if p.is_file() and p.name not in {"area.lst", "shutdown.txt"}
    ) if area_dir.is_dir() else []

    parsed_areas: dict[str, _AreaParser] = {}
    for p in all_area_files:
        ap = _AreaParser(p)
        ap.parse()
        parsed_areas[p.name] = ap

    active_set = set(active_area_names)
    unreferenced_area_names = sorted(p.name for p in all_area_files if p.name not in active_set)

    # Collision check across all areas vs active areas
    def _find_collisions(names: list[str]) -> list[dict[str, Any]]:
        seen_rooms: dict[int, str] = {}
        seen_objs: dict[int, str] = {}
        seen_mobs: dict[int, str] = {}
        cols: list[dict[str, Any]] = []
        for nm in names:
            ap = parsed_areas.get(nm)
            if not ap:
                continue
            for dup in ap.within_file_dups:
                cols.append(
                    {
                        "kind": dup["kind"],
                        "vnum": dup["vnum"],
                        "first_file": nm,
                        "second_file": nm,
                        "first_line": dup["first_line"],
                        "second_line": dup["second_line"],
                    }
                )
            for kind, table, seen in (("room", ap.rooms, seen_rooms), ("obj", ap.objs, seen_objs), ("mob", ap.mobs, seen_mobs)):
                for v in sorted(table.keys()):
                    if v in seen:
                        cols.append({"kind": kind, "vnum": v, "first_file": seen[v], "second_file": nm})
                    else:
                        seen[v] = nm
        return cols

    active_collisions = _find_collisions(active_area_names)
    all_collisions = _find_collisions([p.name for p in all_area_files])
    active_area_parse_errors = [
        {"file": f"area/{name}", "error": error}
        for name in active_area_names if name in parsed_areas
        for error in parsed_areas[name].parse_errors
    ]

    active_rooms: set[int] = set()
    active_objs: set[int] = set()
    active_mobs: set[int] = set()
    all_rooms: set[int] = set()
    all_objs: set[int] = set()
    all_mobs: set[int] = set()
    for nm, ap in parsed_areas.items():
        all_rooms.update(ap.rooms.keys())
        all_objs.update(ap.objs.keys())
        all_mobs.update(ap.mobs.keys())
        if nm in active_set:
            active_rooms.update(ap.rooms.keys())
            active_objs.update(ap.objs.keys())
            active_mobs.update(ap.mobs.keys())

    # Valid spec_* names from special.c
    special_sf = file_map.get("src/special.c")
    valid_specs: set[str] = set()
    if special_sf:
        spec_lookup = next((special_sf.active_text[bo + 1 : bc]
                            for fn, _, bo, bc in extract_function_spans(special_sf.active_text)
                            if fn == "spec_lookup"), "")
        valid_specs = set(re.findall(r'"(spec_[A-Za-z0-9_]+)"', spec_lookup))

    def _audit_area_refs(names: list[str], rooms_pool: set[int], objs_pool: set[int], mobs_pool: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        broken_refs: list[dict[str, Any]] = []
        non_pos_exits: list[dict[str, Any]] = []
        reset_doors: list[dict[str, Any]] = []
        broken_specs: list[dict[str, Any]] = []
        mp_broken: list[dict[str, Any]] = []

        for nm in names:
            ap = parsed_areas.get(nm)
            if not ap:
                broken_refs.append({"file": f"area/{nm}", "issue": "missing_area_file"})
                continue
            for ex in ap.exits:
                if ex["to_vnum"] <= 0:
                    non_pos_exits.append({"file": f"area/{nm}", **ex})
                elif ex["to_vnum"] not in rooms_pool:
                    broken_refs.append({"file": f"area/{nm}", "line": ex["line"], "source": "exit_to", "target_vnum": ex["to_vnum"], "ref_kind": "room"})
                if ex["key_vnum"] > 0 and ex["key_vnum"] not in objs_pool:
                    broken_refs.append({"file": f"area/{nm}", "line": ex["line"], "source": "exit_key", "target_vnum": ex["key_vnum"], "ref_kind": "obj"})
            for rs in ap.resets:
                lt, a1, a2, a3 = rs["letter"], rs["arg1"], rs["arg2"], rs["arg3"]
                if lt == "M":
                    if a1 not in mobs_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_M_mob", "target_vnum": a1, "ref_kind": "mob"})
                    if a3 not in rooms_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_M_room", "target_vnum": a3, "ref_kind": "room"})
                elif lt == "O":
                    if a1 not in objs_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_O_obj", "target_vnum": a1, "ref_kind": "obj"})
                    if a3 not in rooms_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_O_room", "target_vnum": a3, "ref_kind": "room"})
                elif lt == "P":
                    if a1 not in objs_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_P_obj", "target_vnum": a1, "ref_kind": "obj"})
                    if a3 > 0 and a3 not in objs_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_P_container", "target_vnum": a3, "ref_kind": "obj"})
                elif lt in {"G", "E"}:
                    if a1 not in objs_pool:
                        broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": f"reset_{lt}_obj", "target_vnum": a1, "ref_kind": "obj"})
                elif lt == "D":
                    if a1 not in rooms_pool:
                        reset_doors.append({"file": f"area/{nm}", "line": rs["line"], "room": a1, "dir": a2})
                    elif a2 not in ap.rooms.get(a1, {}).get("exit_dirs", set()):
                        # Check across all parsed areas for room a1
                        found_dir = any(a2 in p.rooms.get(a1, {}).get("exit_dirs", set()) for p in parsed_areas.values())
                        if not found_dir:
                            reset_doors.append({"file": f"area/{nm}", "line": rs["line"], "room": a1, "dir": a2})
                elif lt == "R" and a1 not in rooms_pool:
                    broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_R_room", "target_vnum": a1, "ref_kind": "room"})
                elif lt == "H" and a1 > 0 and a1 not in objs_pool:
                    broken_refs.append({"file": f"area/{nm}", "line": rs["line"], "source": "reset_H_obj", "target_vnum": a1, "ref_kind": "obj"})
            for sh in ap.shops:
                if sh["keeper"] not in mobs_pool:
                    broken_refs.append({"file": f"area/{nm}", "line": sh["line"], "source": "shop_keeper", "target_vnum": sh["keeper"], "ref_kind": "mob"})
            for sp in ap.specials:
                if sp["mob_vnum"] not in mobs_pool:
                    broken_refs.append({"file": f"area/{nm}", "line": sp["line"], "source": "special_mob", "target_vnum": sp["mob_vnum"], "ref_kind": "mob"})
                if sp["spec_name"].casefold() not in valid_specs:
                    broken_specs.append({"file": f"area/{nm}", **sp})
            for mp in ap.mudprog_refs:
                rk, tv = mp["ref_kind"], mp["target_vnum"]
                pool = objs_pool if rk == "obj" else (mobs_pool if rk == "mob" else rooms_pool)
                if tv not in pool:
                    mp_broken.append({"file": f"area/{nm}", **mp})

        return broken_refs, reset_doors, broken_specs, mp_broken

    act_broken, act_reset_doors, act_specs, act_mp_broken = _audit_area_refs(
        active_area_names, active_rooms, active_objs, active_mobs
    )
    unref_broken, unref_reset_doors, unref_specs, unref_mp_broken = _audit_area_refs(
        unreferenced_area_names, all_rooms, all_objs, all_mobs
    )
    all_mp_broken = act_mp_broken + unref_mp_broken

    # Template prototype rooms check (2701..2732, 2751..2752, 2761..2762)
    missing_template_rooms: list[int] = []
    if mode == "post-remediation":
        str_areavnum = 2700
        mud_h = file_map.get("src/mud.h")
        if mud_h:
            m_sa = re.search(r"#\s*define\s+STR_AREAVNUM\s+(\d+)", mud_h.active_text)
            if m_sa:
                str_areavnum = int(m_sa.group(1))
        space2_sf = file_map.get("src/space2.c")
        rtypes: list[int] = []
        if space2_sf:
            m_tt = re.search(r"\btemplatetypes\s*\[[^\]]*\]\s*=\s*\{", space2_sf.active_text)
            if m_tt:
                open_b = m_tt.end() - 1
                close_b = find_matching_brace(space2_sf.active_text, open_b)
                tt_body = space2_sf.active_text[open_b + 1 : close_b]
                for m_ent in re.finditer(
                    r'\{\s*[A-Za-z0-9_]+\s*,\s*[A-Za-z0-9_]+\s*,\s*"(?:[^"\\]|\\.)*"\s*,\s*"((?:[^"\\]|\\.)*)"',
                    tt_body,
                ):
                    tstr = m_ent.group(1)
                    if "]" not in tstr:
                        continue
                    after_bracket = tstr.split("]", 1)[1]
                    for m_rt in re.finditer(r"(?:^|[;:])\s*(\d+)", after_bracket):
                        rt = int(m_rt.group(1))
                        if rt > 0:
                            rtypes.append(rt)
        for rt in sorted(set(rtypes)):
            if (str_areavnum + rt) not in active_rooms:
                missing_template_rooms.append(str_areavnum + rt)

    # 4. Space / Ship / Planet / Clan / Board lists & assets
    space_dir = repo / "space"
    ship_lst = _read_list_file(space_dir / "ship.lst")
    space_lst = _read_list_file(space_dir / "space.lst")
    planet_lst = _read_list_file(repo / "planets" / "planet.lst")
    clan_lst = _read_list_file(repo / "clans" / "clan.lst")
    board_stanzas = _read_boards_txt_stanzas(repo / "boards" / "boards.txt")

    required_catalogs = {
        "area/area.lst": active_area_names,
        "space/ship.lst": ship_lst,
        "space/space.lst": space_lst,
        "planets/planet.lst": planet_lst,
        "clans/clan.lst": clan_lst,
    }
    catalogs = {**required_catalogs, "boards/boards.txt": [s["filename"] for s in board_stanzas]}
    manifest_issues: list[dict[str, str]] = []
    for rel, entries in catalogs.items():
        path = repo / rel
        if not path.is_file():
            manifest_issues.append({"file": rel, "issue": "missing_catalog"})
            continue
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
        content = [line for line in lines if line and not line.startswith("*")]
        if not content or content[-1] != "$" or content.count("$") != 1:
            manifest_issues.append({"file": rel, "issue": "invalid_catalog_terminator"})
        if rel == "boards/boards.txt" and len(board_stanzas) != sum(line.casefold() == "end" for line in content):
            manifest_issues.append({"file": rel, "issue": "invalid_board_record"})
        seen: set[str] = set()
        for entry in entries:
            if not entry or entry in {".", ".."} or any(c.isspace() or c in "/\\:" for c in entry):
                manifest_issues.append({"file": rel, "entry": entry, "issue": "invalid_catalog_filename"})
            if entry.casefold() in seen:
                manifest_issues.append({"file": rel, "entry": entry, "issue": "duplicate_catalog_entry"})
            seen.add(entry.casefold())

    list_files_info = [
        {"file": "planets/planet.lst", "active_entry_count": len(planet_lst), "effectively_empty": len(planet_lst) == 0},
        {"file": "clans/clan.lst", "active_entry_count": len(clan_lst), "effectively_empty": len(clan_lst) == 0},
        {"file": "boards/boards.txt", "active_entry_count": len(board_stanzas), "effectively_empty": len(board_stanzas) == 0},
        {"file": "space/space.lst", "active_entry_count": len(space_lst), "effectively_empty": len(space_lst) == 0},
        {"file": "space/ship.lst", "active_entry_count": len(ship_lst), "effectively_empty": len(ship_lst) == 0},
        {"file": "area/area.lst", "active_entry_count": len(active_area_names), "effectively_empty": len(active_area_names) == 0},
    ]
    effectively_empty_count = sum(1 for lf in list_files_info if lf["effectively_empty"])

    missing_from_ship_lst = [e for e in ship_lst if not (space_dir / e).is_file()]
    missing_from_space_lst = [e for e in space_lst if not (space_dir / e).is_file()]
    missing_from_planet_lst = [e for e in planet_lst if not (repo / "planets" / e).is_file()]
    missing_from_clan_lst = [e for e in clan_lst if not (repo / "clans" / e).is_file()]
    missing_from_boards_txt = [
        st["filename"]
        for st in board_stanzas
        if st.get("filename") and not (repo / "boards" / st["filename"]).is_file()
    ]

    # Active fread_ship handled keys (case-insensitive per str_cmp)
    fread_ship_keys: set[str] = set()
    if space_sf:
        fs_body = dict((n, space_sf.active_text[bo + 1 : bc]) for n, _, bo, bc in extract_function_spans(space_sf.active_text)).get("fread_ship_internal", "")
        for tag_info in _extract_reader_tags(fs_body, 0, space_sf):
            fread_ship_keys.add(tag_info["tag"].lower())
        fread_ship_keys.update(tag.lower() for tag in LEGACY_TAGS_BY_ENTITY["ship"])

    zero_byte_files: list[str] = []
    ship_files_scanned = 0
    ship_missing_end: list[str] = []
    ship_unhandled_keys: dict[str, int] = {}
    ship_files_with_unhandled = 0
    active_ship_broken: list[dict[str, Any]] = []
    unref_ship_broken: list[dict[str, Any]] = []
    spaceobj_broken: list[dict[str, Any]] = []
    active_spaceobj_broken: list[dict[str, Any]] = []
    active_space_file_issues: list[dict[str, str]] = []

    ship_vnum_fields = {
        "Shipyard", "Hanger", "Lastdoc", "Firstroom", "Lastroom",
        "Cockpit", "Coseat", "Pilotseat", "Gunseat", "Navseat",
        "Engineroom", "Entrance", *(f"Turret{i}" for i in range(10)),
    }
    space_vnum_fields = {"Doca", "Docb", "Docc"}

    ship_lst_set = set(ship_lst)
    space_lst_set = set(space_lst)

    if space_dir.is_dir():
        for p in sorted(space_dir.iterdir()):
            if p.is_dir() or p.name in {"ship.lst", "space.lst"}:
                continue
            rel = f"space/{p.name}"
            expected_header = "#SHIP" if p.name in ship_lst_set else "#SPACE" if p.name in space_lst_set else None
            if p.stat().st_size == 0:
                zero_byte_files.append(rel)
                if expected_header:
                    active_space_file_issues.append({"file": rel, "issue": "empty_active_space_asset"})
                continue
            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            record_lines = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("*")]
            first = record_lines[0].upper() if record_lines else ""
            if expected_header and first != expected_header:
                active_space_file_issues.append({"file": rel, "issue": "wrong_active_space_asset_header"})
            elif expected_header and (record_lines[-1].upper() != "#END" or not re.search(r"(?im)^\s*End\s*$", content)):
                active_space_file_issues.append({"file": rel, "issue": "missing_active_space_asset_terminator"})
            if first == "#SHIP":
                ship_files_scanned += 1
                if not any(ln.strip() == "#END" for ln in lines):
                    ship_missing_end.append(f"space/{p.name}")
                has_template = False
                firstroom = 0
                lastroom = 0
                kv_pairs: list[tuple[str, str]] = []
                in_ship = False
                in_string = False
                unhandled_in_file = False
                for ln in lines:
                    s = ln.strip()
                    if in_string:
                        in_string = "~" not in s
                        continue
                    if not s or s.startswith("*"):
                        continue
                    if s.upper() == "#SHIP":
                        in_ship = True
                        continue
                    if s.lower() in {"end", "#end"}:
                        in_ship = False
                        continue
                    if in_ship:
                        parts = s.split(None, 1)
                        key = parts[0]
                        val = parts[1].rstrip("~").strip() if len(parts) > 1 else ""
                        if key.casefold() in {"name", "personalname", "filename", "description", "owner",
                                               "pilot", "copilot", "home", "templatestring"}:
                            in_string = "~" not in s
                        kv_pairs.append((key, val))
                        if key == "Templatestring" and val:
                            has_template = True
                        if key == "Firstroom" and val.lstrip("-").isdigit():
                            firstroom = int(val)
                        if key == "Lastroom" and val.lstrip("-").isdigit():
                            lastroom = int(val)
                        if fread_ship_keys and key.lower() not in fread_ship_keys:
                            ship_unhandled_keys[key] = ship_unhandled_keys.get(key, 0) + 1
                            unhandled_in_file = True
                            if p.name in ship_lst_set:
                                active_space_file_issues.append({"file": rel, "issue": "unhandled_ship_key", "key": key})
                if unhandled_in_file:
                    ship_files_with_unhandled += 1

                # Check room VNUMs
                if p.name in ship_lst_set:
                    broken_fields: list[dict[str, Any]] = []
                    for k, v in kv_pairs:
                        if k in ship_vnum_fields and v.lstrip("-").isdigit():
                            vn = int(v)
                            if vn > 0 and vn not in active_rooms:
                                if has_template and k in {"Firstroom", "Lastroom", "Cockpit", "Coseat", "Pilotseat", "Gunseat", "Navseat", "Entrance"}:
                                    continue
                                broken_fields.append({"field": k, "vnum": vn})
                    if firstroom > 0 and lastroom > 0 and firstroom > lastroom:
                        broken_fields.append({"field": "Firstroom>Lastroom", "vnum": firstroom})
                    if broken_fields:
                        active_ship_broken.append({"file": f"space/{p.name}", "broken": broken_fields})
                else:
                    broken_fields = []
                    for k, v in kv_pairs:
                        if k in ship_vnum_fields and v.lstrip("-").isdigit():
                            vn = int(v)
                            if vn > 0 and vn not in all_rooms:
                                if has_template and k in {"Firstroom", "Lastroom", "Cockpit", "Coseat", "Pilotseat", "Gunseat", "Navseat", "Entrance"}:
                                    continue
                                broken_fields.append({"field": k, "vnum": vn})
                    if broken_fields:
                        unref_ship_broken.append({"file": f"space/{p.name}", "broken": broken_fields})

            elif first == "#SPACE":
                broken_fields = []
                for ln in lines:
                    s = ln.strip()
                    parts = s.split(None, 1)
                    if len(parts) == 2 and parts[0] in space_vnum_fields:
                        val = parts[1].strip()
                        if val.lstrip("-").isdigit():
                            vn = int(val)
                            pool = active_rooms if p.name in space_lst_set else all_rooms
                            if vn > 0 and vn not in pool:
                                broken_fields.append({"field": parts[0], "vnum": vn})
                if broken_fields:
                    issue = {"file": rel, "broken": broken_fields}
                    spaceobj_broken.append(issue)
                    if p.name in space_lst_set:
                        active_spaceobj_broken.append(issue)

    summary = {
        "persistence_pairs_audited": len(pairs_out),
        "persistence_pairs_missing_count": len(missing_pairs),
        "persistence_write_only_tags_count": wo_count,
        "persistence_read_only_unexpected_tags_count": ro_unexp_count,
        "persistence_switch_missing_breaks_count": switch_breaks_count,
        "semantic_persistence_bugs_count": len(semantic_bugs),
        "area_total_files": len(all_area_files),
        "area_active_files": len(active_area_names),
        "area_unreferenced_files": len(unreferenced_area_names),
        "area_parse_errors_count": sum(len(ap.parse_errors) for ap in parsed_areas.values()),
        "area_active_parse_errors_count": len(active_area_parse_errors),
        "area_active_broken_vnum_refs_count": len(act_broken),
        "area_unreferenced_broken_vnum_refs_count": len(unref_broken),
        "area_active_duplicate_vnums_count": len(active_collisions),
        "area_all_vnum_collisions_count": len(all_collisions),
        "area_mudprog_broken_refs_count": len(all_mp_broken),
        "area_reset_door_issues_count": len(act_reset_doors) + len(unref_reset_doors),
        "area_broken_special_names_count": len(act_specs) + len(unref_specs),
        "missing_template_rooms_count": len(missing_template_rooms),
        "space_ship_lst_entries_count": len(ship_lst),
        "space_space_lst_entries_count": len(space_lst),
        "space_missing_from_ship_lst_count": len(missing_from_ship_lst),
        "space_missing_from_space_lst_count": len(missing_from_space_lst),
        "planets_missing_from_planet_lst_count": len(missing_from_planet_lst),
        "clans_missing_from_clan_lst_count": len(missing_from_clan_lst),
        "boards_missing_from_boards_txt_count": len(missing_from_boards_txt),
        "space_active_ship_broken_vnums_count": len(active_ship_broken),
        "space_active_spaceobject_broken_vnums_count": len(active_spaceobj_broken),
        "space_active_record_issues_count": len(active_space_file_issues),
        "space_unreferenced_ship_broken_vnums_count": len(unref_ship_broken),
        "space_zero_byte_files_count": len(zero_byte_files),
        "effectively_empty_list_files_count": effectively_empty_count,
        "manifest_issues_count": len(manifest_issues),
    }

    if mode == "baseline":
        violation_count = (
            wo_count
            + ro_unexp_count
            + switch_breaks_count
            + len(semantic_bugs)
            + len(act_broken)
            + len(active_collisions)
            + len(all_mp_broken)
            + len(missing_from_ship_lst)
            + len(active_ship_broken)
            + len(zero_byte_files)
            + effectively_empty_count
        )
    else:
        # Asset defects outside the loader's catalogs are still reported below.
        violation_count = (
            wo_count
            + ro_unexp_count
            + switch_breaks_count
            + len(semantic_bugs)
            + len(missing_pairs)
            + len(manifest_issues)
            + len(active_area_parse_errors)
            + len(act_broken)
            + len(active_collisions)
            + len(act_mp_broken)
            + len(act_reset_doors)
            + len(act_specs)
            + len(missing_template_rooms)
            + len(missing_from_ship_lst)
            + len(missing_from_space_lst)
            + len(missing_from_planet_lst)
            + len(missing_from_clan_lst)
            + len(missing_from_boards_txt)
            + len(active_ship_broken)
            + len(active_spaceobj_broken)
            + len(active_space_file_issues)
            + sum(not entries for entries in required_catalogs.values())
        )
        if len(active_area_names) < 40:
            violation_count += 1

    return {
        "scanner": "persistence_and_vnum_audit",
        "scope": ["src/", "area/*", "space/*", "planets/planet.lst", "clans/clan.lst", "boards/boards.txt"],
        "blocking_count": violation_count,
        "summary": summary,
        "tag_symmetry_audit": {
            "pairs": pairs_out,
            "missing_pairs": missing_pairs,
            "semantic_persistence_bugs": semantic_bugs,
        },
        "area_vnum_audit": {
            "area_lst_active_files": active_area_names,
            "unreferenced_area_files": unreferenced_area_names,
            "duplicate_vnums": all_collisions,
            "active_duplicate_vnums": active_collisions,
            "active_area_parse_errors": active_area_parse_errors,
            "active_area_broken_refs": act_broken,
            "unreferenced_area_broken_refs": unref_broken,
            "reset_door_issues": act_reset_doors + unref_reset_doors,
            "broken_special_names": act_specs + unref_specs,
            "mudprog_broken_refs": all_mp_broken,
            "missing_template_rooms": missing_template_rooms,
        },
        "space_audit": {
            "ship_lst_entries": ship_lst,
            "space_lst_entries": space_lst,
            "missing_from_ship_lst": missing_from_ship_lst,
            "missing_from_space_lst": missing_from_space_lst,
            "empty_files": zero_byte_files,
            "ship_files_scanned": ship_files_scanned,
            "ship_files_missing_hash_END": ship_missing_end,
            "ship_files_with_unhandled_legacy_keys_count": ship_files_with_unhandled,
            "unhandled_legacy_key_counts": ship_unhandled_keys,
            "active_ship_lst_broken_vnums": active_ship_broken,
            "unreferenced_ship_broken_against_all_areas": unref_ship_broken,
            "spaceobject_broken_vnums": spaceobj_broken,
            "active_spaceobject_broken_vnums": active_spaceobj_broken,
            "active_space_record_issues": active_space_file_issues,
        },
        "empty_and_list_files_audit": {
            "list_files": list_files_info,
            "manifest_issues": manifest_issues,
            "missing_from_planet_lst": missing_from_planet_lst,
            "missing_from_clan_lst": missing_from_clan_lst,
            "missing_from_boards_txt": missing_from_boards_txt,
            "summary_counts": {
                "list_files_inspected": len(list_files_info),
                "effectively_empty_list_files_count": effectively_empty_count,
            },
        },
        "passed": violation_count == 0,
    }


# ---------------------------------------------------------------------------
# Unified Orchestration & CLI
# ---------------------------------------------------------------------------

SCANNER_ALIASES: dict[str, str] = {
    "allocator": "allocator_mismatch",
    "allocator_mismatch": "allocator_mismatch",
    "flag": "flag_domain",
    "flag_domain": "flag_domain",
    "wiring": "command_skill_wiring",
    "command_skill_wiring": "command_skill_wiring",
    "const": "const_and_format",
    "const_and_format": "const_and_format",
    "persistence": "persistence_and_vnum",
    "persistence_and_vnum": "persistence_and_vnum",
}


def run_all_scanners(
    repo_path: str | Path,
    mode: str = "post-remediation",
    checks: list[str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_path)
    mk, files, _ = load_source_corpus(repo, mode=mode)

    selected_keys: list[str]
    if not checks or "all" in checks:
        selected_keys = [
            "allocator_mismatch",
            "flag_domain",
            "command_skill_wiring",
            "const_and_format",
            "persistence_and_vnum",
        ]
    else:
        selected_keys = []
        for c in checks:
            canon = SCANNER_ALIASES.get(c)
            if not canon:
                raise ValueError(f"Unknown scanner check {c!r}")
            if canon not in selected_keys:
                selected_keys.append(canon)

    results: dict[str, Any] = {}
    obj_vnums_cache: set[int] | None = None

    if "allocator_mismatch" in selected_keys:
        results["allocator_mismatch"] = run_allocator_scanner(repo, mk, files, mode)
    if "flag_domain" in selected_keys:
        results["flag_domain"] = run_flag_scanner(repo, mk, files, mode)
    if "command_skill_wiring" in selected_keys:
        obj_vnums_cache = _collect_area_object_vnums(repo, active_only=mode == "post-remediation")
        results["command_skill_wiring"] = run_wiring_scanner(
            repo, mk, files, mode, defined_object_vnums=obj_vnums_cache
        )
    if "const_and_format" in selected_keys:
        results["const_and_format"] = run_const_format_scanner(repo, mk, files, mode)
    if "persistence_and_vnum" in selected_keys:
        results["persistence_and_vnum"] = run_persistence_vnum_scanner(repo, mk, files, mode)

    all_passed = all(res["passed"] for res in results.values())
    return {
        "repo": str(repo),
        "mode": mode,
        "all_passed": all_passed,
        "scanners": results,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SWRIPMUD static audit verifiers")
    parser.add_argument("--repo", default="/mnt/d/SWRIPMUD", help="Path to SWRIPMUD repository")
    parser.add_argument(
        "--mode",
        choices=("baseline", "post-remediation"),
        default="post-remediation",
        help="Verification mode",
    )
    parser.add_argument("--json", dest="json_dir", default=None, help="Directory to write per-scanner JSON reports")
    parser.add_argument(
        "--check",
        action="append",
        choices=("all", "allocator", "flag", "wiring", "const", "persistence", "allocator_mismatch", "flag_domain", "command_skill_wiring", "const_and_format", "persistence_and_vnum"),
        default=None,
        help="Scanner check(s) to run (repeatable; defaults to all)",
    )
    args = parser.parse_args(argv)

    report = run_all_scanners(args.repo, mode=args.mode, checks=args.check)

    if args.json_dir:
        out_dir = Path(args.json_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, data in report["scanners"].items():
            out_path = out_dir / f"{key}_audit.json"
            out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    for key, data in report["scanners"].items():
        status = "PASS" if data["passed"] else "FAIL"
        print(f"[{status}] {key} ({args.mode})")
        for sk, sv in data["summary"].items():
            print(f"    {sk}: {sv}")

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(_main())
