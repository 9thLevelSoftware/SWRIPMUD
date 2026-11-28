"""Raw TCP + Telnet FSM client supporting TTYPE, NAWS, SGA, EOR, LINEMODE, MSSP, MCCP2, and GMCP."""

from __future__ import annotations

import codecs
import json
import re
import socket
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

# Telnet commands
IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
GA = 249
NOP = 241
SE = 240
EOR_CMD = 239

# Telnet options
TELOPT_ECHO = 1
TELOPT_SGA = 3
TELOPT_TTYPE = 24
TELOPT_EOR = 25
TELOPT_NAWS = 31
TELOPT_LINEMODE = 34
TELOPT_MSSP = 70
TELOPT_COMPRESS2 = 86  # MCCP2
TELOPT_GMCP = 201

# MSSP subnegotiation markers
MSSP_VAR = 1
MSSP_VAL = 2

# TTYPE subnegotiation markers
TTYPE_IS = 0
TTYPE_SEND = 1

DEFAULT_PROMPT_RE = re.compile(
    r"(?:Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>|^\s*> )",
    re.MULTILINE,
)

# Strip ANSI CSI escape sequences for reliable pattern matching when ANSI is enabled.
ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return ANSI_CSI_RE.sub("", text)


def _map_stripped_offset_to_raw(text: str, stripped_offset: int) -> int:
    """Map an end offset in `strip_ansi(text)` back to the exact raw offset in `text`."""
    if stripped_offset <= 0:
        return 0
    raw_pos = 0
    stripped_pos = 0
    for ansi_m in ANSI_CSI_RE.finditer(text):
        plain_len = ansi_m.start() - raw_pos
        if stripped_pos + plain_len >= stripped_offset:
            return raw_pos + (stripped_offset - stripped_pos)
        stripped_pos += plain_len
        raw_pos = ansi_m.end()
    return min(len(text), raw_pos + (stripped_offset - stripped_pos))


@dataclass
class GmcpEvent:
    module: str
    raw_body: str
    data: Any
    json_valid: bool
    error: str | None = None
    timestamp: float = field(default_factory=time.monotonic)


class TelnetClient:
    """Opaque-box TCP/Telnet client with byte-level protocol instrumentation."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4000,
        timeout: float = 10.0,
        *,
        auto_negotiate_ttype: bool = True,
        auto_negotiate_naws: bool = True,
        auto_negotiate_sga: bool = True,
        auto_negotiate_eor: bool = True,
        ttype_list: list[str] | None = None,
        naws_size: tuple[int, int] = (120, 40),
    ) -> None:
        self.host = host
        self.port = port
        self.default_timeout = timeout

        self.auto_negotiate_ttype = auto_negotiate_ttype
        self.auto_negotiate_naws = auto_negotiate_naws
        self.auto_negotiate_sga = auto_negotiate_sga
        self.auto_negotiate_eor = auto_negotiate_eor
        self.ttype_list = list(ttype_list or ["SWRIP-E2E-UTF8", "xterm-256color", "ANSI"])
        self._ttype_idx = 0
        self.naws_size = naws_size

        self.sock: socket.socket | None = None
        self.eof: bool = False
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        # FSM state
        self._state = "DATA"
        self._iac_cmd = 0
        self._sb_opt = 0
        self._sb_buf = bytearray()

        # MCCP2 state
        self.mccp2_active = False
        self.mccp2_activations = 0
        self.mccp2_deactivations = 0
        self._decomp: Any = None

        # Option state & telemetry
        self.server_options: dict[int, bool] = {}
        self.client_options: dict[int, bool] = {}
        self.eor_enabled = False
        self.will_eor_count = 0
        self.eor_count = 0
        self.null_byte_count = 0
        self.raw_null_byte_count = 0
        self.raw_frames: list[bytes] = []
        self.iac_events: list[tuple[Any, ...]] = []
        self.raw_text_bytes = bytearray()

        # Text buffer + cursor
        self._text_chunks: list[str] = []
        self._full_text = ""
        self._cursor = 0

        # MSSP & GMCP captures
        self.mssp_data: dict[str, str] = {}
        self.mssp_multi: dict[str, list[str]] = {}
        self.mssp_raw: bytes = b""
        self.gmcp_events: list[GmcpEvent] = []

        self.connect()

    def connect(self) -> None:
        if self.sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.default_timeout)
        sock.connect((self.host, self.port))
        self.sock = sock
        self.eof = False

    def close(self) -> None:
        self.eof = True
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> TelnetClient:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @property
    def all_text(self) -> str:
        return self._full_text

    def send_raw(self, data: bytes) -> None:
        if self.sock is None:
            raise RuntimeError("TelnetClient socket is closed")
        self.sock.sendall(data)

    def send_line(self, text: str) -> None:
        self.send_raw((text + "\r\n").encode("utf-8"))

    def send_iac(self, cmd: int, opt: int) -> None:
        self.send_raw(bytes((IAC, cmd, opt)))

    def send_sb(self, opt: int, payload: bytes) -> None:
        escaped = bytearray()
        for b in payload:
            if b == IAC:
                escaped.append(IAC)
            escaped.append(b)
        self.send_raw(bytes((IAC, SB, opt)) + bytes(escaped) + bytes((IAC, SE)))

    def send_naws(self, width: int, height: int) -> None:
        self.send_sb(TELOPT_NAWS, struct.pack("!HH", width & 0xFFFF, height & 0xFFFF))

    def send_will_linemode(self) -> None:
        self.send_iac(WILL, TELOPT_LINEMODE)

    def enable_mccp2(self, wait: bool = True, timeout: float = 3.0) -> bool:
        self.send_iac(DO, TELOPT_COMPRESS2)
        if not wait:
            return self.mccp2_active
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.mccp2_active and not self.eof:
            self._pump_once(min(0.1, max(0.01, deadline - time.monotonic())))
        return self.mccp2_active

    def disable_mccp2(self, wait: bool = True, timeout: float = 3.0) -> bool:
        self.send_iac(DONT, TELOPT_COMPRESS2)
        if not wait:
            return not self.mccp2_active
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.mccp2_active and not self.eof:
            self._pump_once(min(0.1, max(0.01, deadline - time.monotonic())))
        return not self.mccp2_active

    def enable_gmcp(self) -> None:
        self.send_iac(DO, TELOPT_GMCP)

    def send_gmcp(self, module: str, payload: Any = None) -> None:
        if payload is None:
            msg = module
        elif isinstance(payload, str):
            msg = f"{module} {payload}"
        else:
            msg = f"{module} {json.dumps(payload, separators=(',', ':'))}"
        self.send_sb(TELOPT_GMCP, msg.encode("utf-8"))

    def request_mssp(self, timeout: float = 5.0) -> dict[str, str]:
        self.mssp_data = {}
        self.mssp_multi = {}
        self.mssp_raw = b""
        self.send_iac(DO, TELOPT_MSSP)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.mssp_raw and not self.eof:
            self._pump_once(min(0.15, max(0.01, deadline - time.monotonic())))
        return dict(self.mssp_data)

    def _emit_text_bytes(self, data: bytes) -> None:
        if not data:
            return
        self.raw_text_bytes.extend(data)
        decoded = self._utf8_decoder.decode(data, final=False)
        if decoded:
            self._full_text += decoded

    def _handle_negotiation(self, cmd: int, opt: int) -> None:
        self.iac_events.append((cmd, opt))
        if cmd == WILL:
            self.server_options[opt] = True
            if opt == TELOPT_EOR:
                self.will_eor_count += 1
                if self.auto_negotiate_eor:
                    self.eor_enabled = True
                    self.send_iac(DO, TELOPT_EOR)
            elif opt == TELOPT_SGA:
                if self.auto_negotiate_sga:
                    self.send_iac(DO, TELOPT_SGA)
        elif cmd == WONT:
            self.server_options[opt] = False
            if opt == TELOPT_EOR:
                self.eor_enabled = False
        elif cmd == DO:
            self.client_options[opt] = True
            if opt == TELOPT_TTYPE and self.auto_negotiate_ttype:
                self.send_iac(WILL, TELOPT_TTYPE)
            elif opt == TELOPT_NAWS and self.auto_negotiate_naws:
                self.send_iac(WILL, TELOPT_NAWS)
                self.send_naws(*self.naws_size)
        elif cmd == DONT:
            self.client_options[opt] = False

    def _handle_subnegotiation(self, opt: int, payload: bytes) -> None:
        self.iac_events.append(("SB", opt, bytes(payload)))
        if opt == TELOPT_TTYPE:
            if payload == bytes((TTYPE_SEND,)) and self.auto_negotiate_ttype:
                idx = min(self._ttype_idx, len(self.ttype_list) - 1)
                term = self.ttype_list[idx].encode("ascii", errors="replace")
                if self._ttype_idx < len(self.ttype_list) - 1:
                    self._ttype_idx += 1
                self.send_sb(TELOPT_TTYPE, bytes((TTYPE_IS,)) + term)
        elif opt == TELOPT_MSSP:
            self.mssp_raw = bytes(payload)
            self._parse_mssp(payload)
        elif opt == TELOPT_GMCP:
            self._parse_gmcp(payload)

    def _parse_mssp(self, payload: bytes) -> None:
        parsed: dict[str, str] = {}
        multi: dict[str, list[str]] = {}
        i = 0
        n = len(payload)
        while i < n:
            if payload[i] != MSSP_VAR:
                i += 1
                continue
            i += 1
            var_start = i
            while i < n and payload[i] not in (MSSP_VAR, MSSP_VAL):
                i += 1
            var_name = payload[var_start:i].decode("utf-8", errors="replace")
            if i < n and payload[i] == MSSP_VAL:
                i += 1
                val_start = i
                while i < n and payload[i] not in (MSSP_VAR, MSSP_VAL):
                    i += 1
                val_str = payload[val_start:i].decode("utf-8", errors="replace")
            else:
                val_str = ""
            if var_name:
                parsed[var_name] = val_str
                multi.setdefault(var_name, []).append(val_str)
        self.mssp_data = parsed
        self.mssp_multi = multi

    def _parse_gmcp(self, payload: bytes) -> None:
        text = payload.decode("utf-8", errors="replace")
        if " " in text:
            module, raw_body = text.split(" ", 1)
        else:
            module, raw_body = text, ""
        raw_body_stripped = raw_body.strip()
        if not raw_body_stripped:
            self.gmcp_events.append(
                GmcpEvent(module=module, raw_body=raw_body, data=None, json_valid=True)
            )
            return

        def _reject_nan(constant: str) -> Any:
            raise ValueError(f"Non-RFC8259 JSON constant: {constant}")

        try:
            data = json.loads(raw_body_stripped, parse_constant=_reject_nan)
            self.gmcp_events.append(
                GmcpEvent(module=module, raw_body=raw_body, data=data, json_valid=True)
            )
        except Exception as exc:
            self.gmcp_events.append(
                GmcpEvent(
                    module=module,
                    raw_body=raw_body,
                    data=None,
                    json_valid=False,
                    error=str(exc),
                )
            )

    def _feed_bytes(self, data: bytes) -> None:
        idx = 0
        n = len(data)
        while idx < n:
            if self.mccp2_active:
                assert self._decomp is not None
                chunk = data[idx:]
                try:
                    plain = self._decomp.decompress(chunk)
                except zlib.error as exc:
                    raise RuntimeError(f"MCCP2 zlib decompression failure: {exc}") from exc
                if plain:
                    self._feed_telnet_plain(plain)
                if self._decomp.eof:
                    unused = self._decomp.unused_data
                    self.mccp2_active = False
                    self.mccp2_deactivations += 1
                    self._decomp = None
                    if unused:
                        data = unused
                        idx = 0
                        n = len(data)
                        continue
                break
            else:
                consumed = self._feed_telnet_until_mccp(data[idx:])
                idx += consumed

    def _feed_telnet_until_mccp(self, data: bytes) -> int:
        """Process plaintext Telnet bytes; stops immediately if MCCP2 activates mid-buffer."""
        plain_out = bytearray()
        i = 0
        n = len(data)

        while i < n:
            b = data[i]
            i += 1

            if self._state == "DATA":
                if b == IAC:
                    if plain_out:
                        self._emit_text_bytes(bytes(plain_out))
                        plain_out.clear()
                    self._state = "IAC"
                else:
                    if b == 0:
                        self.null_byte_count += 1
                    plain_out.append(b)
            elif self._state == "IAC":
                if b == IAC:
                    plain_out.append(IAC)
                    self._state = "DATA"
                elif b in (WILL, WONT, DO, DONT):
                    self._iac_cmd = b
                    self._state = "OPT"
                elif b == SB:
                    self._state = "SB_OPT"
                elif b == EOR_CMD:
                    self.eor_count += 1
                    self.iac_events.append(("EOR",))
                    self._state = "DATA"
                elif b in (GA, NOP):
                    self.iac_events.append((b,))
                    self._state = "DATA"
                else:
                    self.iac_events.append((b,))
                    self._state = "DATA"
            elif self._state == "OPT":
                self._handle_negotiation(self._iac_cmd, b)
                self._state = "DATA"
            elif self._state == "SB_OPT":
                self._sb_opt = b
                self._sb_buf.clear()
                self._state = "SB_DATA"
            elif self._state == "SB_DATA":
                if b == IAC:
                    self._state = "SB_IAC"
                else:
                    self._sb_buf.append(b)
            elif self._state == "SB_IAC":
                if b == IAC:
                    self._sb_buf.append(IAC)
                    self._state = "SB_DATA"
                elif b == SE:
                    opt = self._sb_opt
                    payload = bytes(self._sb_buf)
                    self._sb_buf.clear()
                    self._state = "DATA"
                    self._handle_subnegotiation(opt, payload)
                    if opt == TELOPT_COMPRESS2 and not self.mccp2_active:
                        self.mccp2_active = True
                        self.mccp2_activations += 1
                        self._decomp = zlib.decompressobj()
                        if plain_out:
                            self._emit_text_bytes(bytes(plain_out))
                            plain_out.clear()
                        return i
                else:
                    self._sb_buf.append(b)
                    self._state = "SB_DATA"

        if plain_out:
            self._emit_text_bytes(bytes(plain_out))
        return i

    def _feed_telnet_plain(self, data: bytes) -> None:
        idx = 0
        while idx < len(data):
            consumed = self._feed_telnet_until_mccp(data[idx:])
            if consumed == 0:
                break
            idx += consumed

    def _pump_once(self, timeout: float) -> bool:
        """Read one socket chunk if available within timeout. Returns True if bytes read."""
        if self.sock is None:
            self.eof = True
            return False
        old_timeout = self.sock.gettimeout()
        try:
            self.sock.settimeout(max(0.005, timeout))
            chunk = self.sock.recv(8192)
        except socket.timeout:
            return False
        except OSError:
            self.eof = True
            return False
        finally:
            if self.sock is not None:
                try:
                    self.sock.settimeout(old_timeout)
                except OSError:
                    pass

        if not chunk:
            self.eof = True
            rem = self._utf8_decoder.decode(b"", final=True)
            if rem:
                self._full_text += rem
            return False
        self.raw_frames.append(chunk)
        self.raw_null_byte_count += chunk.count(0)
        self._feed_bytes(chunk)
        return True

    def drain(self, seconds: float = 0.25) -> str:
        """Read any pending output for up to `seconds` and return newly decoded text."""
        start_cursor = self._cursor
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self.eof:
            remaining = max(0.01, deadline - time.monotonic())
            got = self._pump_once(min(0.05, remaining))
            if self.eof:
                break
            if not got and time.monotonic() >= deadline:
                break
        out = self._full_text[start_cursor:]
        self._cursor = len(self._full_text)
        return out

    def read_until(
        self,
        pattern: str | re.Pattern[str],
        timeout: float = 10.0,
        *,
        strip_colors: bool = True,
    ) -> str:
        """Wait until `pattern` matches unread text; returns text up to match end."""
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        deadline = time.monotonic() + timeout

        while True:
            unread = self._full_text[self._cursor :]
            haystack = strip_ansi(unread) if strip_colors else unread
            m = regex.search(haystack)
            if m:
                raw_end = (
                    _map_stripped_offset_to_raw(unread, m.end())
                    if strip_colors
                    else m.end()
                )
                end_pos = self._cursor + raw_end
                chunk = self._full_text[self._cursor : end_pos]
                self._cursor = end_pos
                return chunk

            if self.eof:
                raise TimeoutError(
                    f"Connection closed (EOF) while waiting for {regex.pattern!r}; "
                    f"unread tail={unread[-400:]!r}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out after {timeout:.1f}s waiting for {regex.pattern!r}; "
                    f"unread tail={unread[-400:]!r}"
                )
            self._pump_once(min(0.1, remaining))

    def cmd(
        self,
        text: str,
        wait_prompt: bool = True,
        timeout: float = 10.0,
        prompt_pattern: str | re.Pattern[str] = DEFAULT_PROMPT_RE,
        *,
        require_prompt: bool | None = None,
    ) -> str:
        """Send an in-game command and wait for prompt/EOR.

        When ``require_prompt`` is None, it defaults to True if a custom
        ``prompt_pattern`` is supplied and False when using ``DEFAULT_PROMPT_RE``
        (allowing either ``DEFAULT_PROMPT_RE`` or ``IAC EOR`` to terminate).
        Noisy-room tests should pass ``require_prompt=True`` or an explicit
        ``prompt_pattern``.
        """
        if require_prompt is None:
            require_prompt = prompt_pattern is not DEFAULT_PROMPT_RE
        self._cursor = len(self._full_text)
        eor_before = self.eor_count
        self.send_line(text)
        if not wait_prompt:
            return self.drain(seconds=0.15)

        regex = re.compile(prompt_pattern) if isinstance(prompt_pattern, str) else prompt_pattern
        deadline = time.monotonic() + timeout
        while True:
            unread = self._full_text[self._cursor :]
            clean = strip_ansi(unread)
            m = regex.search(clean)
            if m:
                self._cursor = len(self._full_text)
                return clean
            if not require_prompt and self.eor_enabled and self.eor_count > eor_before and clean.strip():
                self._cursor = len(self._full_text)
                return clean
            if self.eof:
                raise TimeoutError(
                    f"Connection closed (EOF) while waiting for prompt after {text!r}; "
                    f"output={clean[-400:]!r}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Command {text!r} timed out after {timeout:.1f}s waiting for prompt; "
                    f"output={clean[-400:]!r}"
                )
            self._pump_once(min(0.1, remaining))

    def login(
        self, name: str, password: str, timeout: float = 15.0, *, wait_for_prompt: bool = True
    ) -> str:
        """Authenticate through nanny. Wait for a stock prompt unless only the playing state is needed."""
        transcript: list[str] = []
        transcript.append(
            self.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=timeout)
        )
        self.send_line(name)
        transcript.append(self.read_until(r"Password:\s*", timeout=timeout))
        self.send_line(password)

        # show_title() emits either "Press enter..." (PCFLAG_NOINTRO) or mudtitle.ans ("Welcome to Rise in Power !!")
        # or "Press [ENTER]"
        stage = self.read_until(
            r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Wrong password\.|Reconnecting\.)",
            timeout=timeout,
        )
        transcript.append(stage)
        clean_stage = strip_ansi(stage)
        if "Wrong password." in clean_stage:
            raise RuntimeError(f"Login failed for {name!r}: Wrong password")
        if "Reconnecting." in clean_stage:
            # An existing session enters CON_PLAYING without a title or MOTD.
            if wait_for_prompt:
                transcript.append(self.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
            return "".join(transcript)

        self.send_line("")

        # A saved prompt is configurable. The game-entry banner reliably marks a
        # fresh authenticated transition after the title and MOTD.
        welcome = r"Welcome to Rise in Power\.\.\."
        terminal = DEFAULT_PROMPT_RE if wait_for_prompt else re.compile(welcome)
        stage2 = self.read_until(
            re.compile(r"Press \[ENTER\]|" + terminal.pattern, re.MULTILINE),
            timeout=timeout,
        )
        transcript.append(stage2)
        if "Press [ENTER]" in strip_ansi(stage2):
            self.send_line("")
            transcript.append(self.read_until(terminal, timeout=timeout))

        return "".join(transcript)

    def create_character(
        self,
        name: str,
        password: str,
        sex: str = "M",
        race: str = "Human",
        ability: str = "combat",
        ansi: str = "A",
        timeout: float = 20.0,
    ) -> str:
        """Walk the full new-character creation flow in `nanny` until `CON_PLAYING`."""
        transcript: list[str] = []
        transcript.append(
            self.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=timeout)
        )
        self.send_line(name)
        transcript.append(
            self.read_until(r"Did I get that right,.*\?\s*", timeout=timeout)
        )
        self.send_line("Y")
        transcript.append(
            self.read_until(r"Pick a good password for .*?:\s*", timeout=timeout)
        )
        self.send_line(password)
        transcript.append(
            self.read_until(r"Please retype the password to confirm:\s*", timeout=timeout)
        )
        self.send_line(password)
        transcript.append(
            self.read_until(r"What is your sex \(M/F/N\)\?\s*", timeout=timeout)
        )
        self.send_line(sex)
        transcript.append(self.read_until(r":\s*$", timeout=timeout))
        self.send_line(race)
        transcript.append(self.read_until(r":\s*$", timeout=timeout))
        self.send_line(ability)
        transcript.append(self.read_until(r"Are these stats OK\?", timeout=timeout))
        self.send_line("Y")
        transcript.append(
            self.read_until(
                r"Would you like ANSI or no graphic/color support, \(A/N\)\?\s*",
                timeout=timeout,
            )
        )
        self.send_line(ansi)

        # CON_GET_MSP auto-runs "n" in comm.c and calls show_title()
        stage = self.read_until(
            r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\])",
            timeout=timeout,
        )
        transcript.append(stage)
        self.send_line("")

        stage2 = self.read_until(
            re.compile(
                r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"
            ),
            timeout=timeout,
        )
        transcript.append(stage2)
        if "Press [ENTER]" in strip_ansi(stage2):
            self.send_line("")
            transcript.append(self.read_until(DEFAULT_PROMPT_RE, timeout=timeout))

        return "".join(transcript)
