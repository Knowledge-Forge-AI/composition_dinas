"""Non-mutating promotion qualification engine for registered public inputs."""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time
from typing import IO, Any, Callable
from urllib.parse import urlsplit, urlunsplit

from registry_contract import (
    RegistryError,
    get_module_by_input,
    load_registry,
    normalize_ref,
    normalize_url,
)

RESULT_SCHEMA = "composition-nd-promotion-result-v1"
PROMOTION_CONTRACT_SCHEMA = "composition-nd-public-promotion-v1"
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SYSTEM_RE = re.compile(r"^[a-z0-9_]+-[a-z0-9_]+$")
RAW_QUERY_RE = re.compile(r"^rev=[0-9a-f]{40}$")
DRV_PATH_PATTERN = re.compile(r"^/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+\.drv\Z")
MAX_DIAGNOSTIC_BYTES = 64 * 1024
MAX_JSON_BYTES = 10 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 900.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 7200.0
GRACE_TERMINATION_SECONDS = 1.0
READER_JOIN_TIMEOUT_SECONDS = 2.0
REAP_TIMEOUT_SECONDS = 2.0

REASON_EXECUTION_TIMEOUT = "execution-timeout"
REASON_DRAIN_TIMEOUT = "drain-timeout"
REASON_SURVIVING_DESCENDANTS = "surviving-descendants"
REASON_READER_FAILURE = "reader-failure"
REASON_INCOMPLETE_CLEANUP = "incomplete-cleanup"
REASON_INTERRUPTED = "interrupted"

COMMAND_FAILURE_REASONS = frozenset({
    REASON_EXECUTION_TIMEOUT,
    REASON_DRAIN_TIMEOUT,
    REASON_SURVIVING_DESCENDANTS,
    REASON_READER_FAILURE,
    REASON_INCOMPLETE_CLEANUP,
    REASON_INTERRUPTED,
})


def validate_timeout_seconds(timeout_seconds: Any) -> float:
    """Validate timeout is a finite number between MIN_TIMEOUT_SECONDS and MAX_TIMEOUT_SECONDS."""
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or math.isnan(timeout_seconds)
        or math.isinf(timeout_seconds)
        or timeout_seconds < MIN_TIMEOUT_SECONDS
        or timeout_seconds > MAX_TIMEOUT_SECONDS
    ):
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            f"timeout_seconds must be a finite number between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}, got {timeout_seconds!r}",
        )
    return float(timeout_seconds)


CATEGORY_REQUEST_VALIDATION = "request-validation"
CATEGORY_REGISTRY_CONTRACT = "registry-contract"
CATEGORY_PROMOTION_CONTRACT = "promotion-contract"
CATEGORY_CANDIDATE_RESOLUTION = "candidate-resolution"
CATEGORY_COMMAND_FAILURE = "command-failure"
CATEGORY_PROTECTED_FILE_MUTATION = "protected-file-mutation"
CATEGORY_MALFORMED_PROVIDER_OUTPUT = "malformed-provider-output"
CATEGORY_INTERNAL_ERROR = "internal-error"

FAILURE_CATEGORIES = frozenset({
    CATEGORY_REQUEST_VALIDATION,
    CATEGORY_REGISTRY_CONTRACT,
    CATEGORY_PROMOTION_CONTRACT,
    CATEGORY_CANDIDATE_RESOLUTION,
    CATEGORY_COMMAND_FAILURE,
    CATEGORY_PROTECTED_FILE_MUTATION,
    CATEGORY_MALFORMED_PROVIDER_OUTPUT,
    CATEGORY_INTERNAL_ERROR,
})

PROTECTED_FILES = ("flake.lock", "flake.nix", "registry.json")

PROMOTION_TOP_LEVEL_KEYS = frozenset({
    "schema",
    "version",
    "composition",
    "system",
    "modules",
    "releaseChannels",
    "packageSetIdentity",
    "hosts",
    "checks",
    "qualification",
    "promotableInputs",
})
COMPOSITION_KEYS = frozenset({"name", "repositoryUrl"})
QUALIFICATION_KEYS = frozenset({"entryPoint", "mode"})
PROMOTABLE_INPUTS_KEYS = frozenset({"rule", "names"})
RELEASE_CHANNELS_KEYS = frozenset({"policy", "refs"})
RELEASE_CHANNELS_POLICY = "explicit-head-refs-v1"
MODULE_RECORD_KEYS = frozenset({
    "name",
    "input",
    "url",
    "checkout",
    "bootstrapStrategy",
    "ownsMachineLocalConfiguration",
})


class PromotionQualificationError(Exception):
    """Raised when candidate qualification encounters a structured failure."""

    def __init__(
        self,
        category: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if category not in FAILURE_CATEGORIES:
            raise ValueError(f"Invalid failure category: {category}")
        self.category = category
        self.message = message
        self.details = details or {}


class CommandResult:
    """Bounded subprocess execution result with metadata, digests, and supervision state."""

    def __init__(
        self,
        argv: list[str],
        exit_code: int,
        stdout_bytes: int,
        stdout_sha256: str | None,
        stdout_truncated: bool,
        stdout_text: str,
        stderr_bytes: int,
        stderr_sha256: str | None,
        stderr_truncated: bool,
        stderr_text: str,
        timed_out: bool = False,
        timeout_seconds: float | None = None,
        cleanup_disposition: str | None = None,
        stdout_raw: bytes | None = None,
        stderr_raw: bytes | None = None,
        failure_reason: str | None = None,
        stdout_reader_error: str | None = None,
        stderr_reader_error: str | None = None,
    ) -> None:
        self.argv = argv
        self.exit_code = exit_code
        self.stdout_bytes = stdout_bytes
        self.stdout_sha256 = stdout_sha256
        self.stdout_truncated = stdout_truncated
        self.stdout_text = stdout_text
        self.stderr_bytes = stderr_bytes
        self.stderr_sha256 = stderr_sha256
        self.stderr_truncated = stderr_truncated
        self.stderr_text = stderr_text
        self.timed_out = timed_out
        self.timeout_seconds = timeout_seconds
        self.cleanup_disposition = cleanup_disposition
        self.stdout_raw = (
            stdout_raw
            if stdout_raw is not None
            else stdout_text.encode("utf-8", errors="replace")
        )
        self.stderr_raw = (
            stderr_raw
            if stderr_raw is not None
            else stderr_text.encode("utf-8", errors="replace")
        )
        self.failure_reason = failure_reason
        self.stdout_reader_error = stdout_reader_error
        self.stderr_reader_error = stderr_reader_error

    @property
    def status(self) -> str:
        return (
            "pass"
            if (
                self.exit_code == 0
                and not self.timed_out
                and self.failure_reason is None
                and self.cleanup_disposition not in ("incomplete", "uncontained")
                and self.stdout_reader_error is None
                and self.stderr_reader_error is None
            )
            else "fail"
        )

    def to_dict(self) -> dict[str, Any]:
        out_preview = (
            self.stdout_raw[:MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="replace")
            if self.stdout_raw
            else ""
        )
        err_preview = (
            self.stderr_raw[:MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="replace")
            if self.stderr_raw
            else ""
        )

        res: dict[str, Any] = {
            "argv": self.argv,
            "exitCode": self.exit_code,
            "status": self.status,
            "timedOut": self.timed_out,
            "timeoutSeconds": self.timeout_seconds,
            "cleanupDisposition": self.cleanup_disposition,
            "stdout": {
                "bytes": self.stdout_bytes,
                "sha256": self.stdout_sha256,
                "truncated": self.stdout_truncated,
                "preview": out_preview,
            },
            "stderr": {
                "bytes": self.stderr_bytes,
                "sha256": self.stderr_sha256,
                "truncated": self.stderr_truncated,
                "preview": err_preview,
            },
        }
        if self.failure_reason is not None:
            res["failureReason"] = self.failure_reason
        return res


class _StreamReaderState:
    """Supervised terminal state for a single pipe reader."""

    def __init__(self, stream_name: str, max_retained_bytes: int) -> None:
        self.stream_name = stream_name
        self.max_retained_bytes = max_retained_bytes
        self.bytes_read = 0
        self.digest = hashlib.sha256()
        self.retained_buffer = bytearray()
        self.truncated = False
        self.eof = False
        self.error: str | None = None


def _stream_reader_worker(
    pipe: IO[bytes] | None,
    state: _StreamReaderState,
) -> None:
    """Read pipe in small chunks, hashing continuously and recording terminal state."""
    if pipe is None:
        state.eof = True
        return
    chunk_size = 4096
    try:
        while True:
            chunk = pipe.read(chunk_size)
            if not chunk:
                state.eof = True
                break
            state.bytes_read += len(chunk)
            state.digest.update(chunk)
            if len(state.retained_buffer) < state.max_retained_bytes:
                available = state.max_retained_bytes - len(state.retained_buffer)
                state.retained_buffer.extend(chunk[:available])
                if len(chunk) > available:
                    state.truncated = True
            else:
                state.truncated = True
    except Exception as exc:
        state.error = str(exc)
        state.eof = False
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _is_process_group_alive(pgid: int) -> bool:
    """Check whether any process exists in the process group pgid."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def default_runner(
    argv: list[str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_stdout_retained_bytes: int = MAX_DIAGNOSTIC_BYTES,
) -> CommandResult:
    """Execute fixed argument vector in isolated session with streaming bounded capture and process containment."""
    if not argv or (argv[0] != "nix" and Path(argv[0]).name != "nix"):
        raise PromotionQualificationError(
            CATEGORY_COMMAND_FAILURE,
            f"disallowed command executable: {argv[0] if argv else 'empty'}",
        )
    if len(argv) < 2 or argv[1] not in ("flake", "eval", "build"):
        raise PromotionQualificationError(
            CATEGORY_COMMAND_FAILURE,
            f"disallowed Nix subcommand: {argv[1] if len(argv) > 1 else 'none'}",
        )
    if argv[1] == "flake" and (len(argv) < 3 or argv[2] != "metadata"):
        raise PromotionQualificationError(
            CATEGORY_COMMAND_FAILURE,
            f"disallowed Nix flake subcommand: {argv[2] if len(argv) > 2 else 'none'}",
        )

    is_main = threading.current_thread() is threading.main_thread()
    received_signal: list[int | None] = [None]

    def _sig_handler(signum: int, _frame: Any) -> None:
        received_signal[0] = signum

    installed_handlers: dict[int, Any] = {}
    if is_main:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                old_h = signal.signal(sig, _sig_handler)
                installed_handlers[sig] = old_h
            except (ValueError, TypeError, OSError):
                pass

    try:
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as err:
            raise PromotionQualificationError(
                CATEGORY_COMMAND_FAILURE,
                f"failed to spawn command {argv[0]}: {err}",
            ) from err

        pgid = proc.pid
        stdout_state = _StreamReaderState("stdout", max_stdout_retained_bytes)
        stderr_state = _StreamReaderState("stderr", MAX_DIAGNOSTIC_BYTES)

        t_out = threading.Thread(
            target=_stream_reader_worker,
            args=(proc.stdout, stdout_state),
            daemon=True,
        )
        t_err = threading.Thread(
            target=_stream_reader_worker,
            args=(proc.stderr, stderr_state),
            daemon=True,
        )
        t_out.start()
        t_err.start()

        deadline = time.monotonic() + timeout
        timed_out = False
        interrupted = False
        drain_timed_out = False
        surviving_descendants = False
        cleanup_disp: str | None = None
        failure_reason: str | None = None

        while True:
            if received_signal[0] is not None:
                interrupted = True
                failure_reason = REASON_INTERRUPTED
                break
            if proc.poll() is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                failure_reason = REASON_EXECUTION_TIMEOUT
                break
            time.sleep(0.02)

        if not timed_out and not interrupted:
            remaining_drain = max(0.5, deadline - time.monotonic())
            t_out.join(timeout=remaining_drain)
            remaining_drain = max(0.5, deadline - time.monotonic())
            t_err.join(timeout=remaining_drain)

            if t_out.is_alive() or t_err.is_alive():
                drain_timed_out = True
                failure_reason = REASON_DRAIN_TIMEOUT
            elif (
                stdout_state.error
                or not stdout_state.eof
                or stderr_state.error
                or not stderr_state.eof
            ):
                failure_reason = REASON_READER_FAILURE
            else:
                proc.poll()
                if _is_process_group_alive(pgid):
                    time.sleep(0.05)
                    proc.poll()
                    if _is_process_group_alive(pgid):
                        surviving_descendants = True
                        failure_reason = REASON_SURVIVING_DESCENDANTS

        needs_cleanup = (
            timed_out
            or interrupted
            or drain_timed_out
            or surviving_descendants
            or (failure_reason is not None)
            or (proc.returncode != 0)
        )

        if needs_cleanup:
            if _is_process_group_alive(pgid):
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    cleanup_disp = "sigterm"
                except (ProcessLookupError, PermissionError):
                    pass

                grace_deadline = time.monotonic() + GRACE_TERMINATION_SECONDS
                while time.monotonic() < grace_deadline:
                    proc.poll()
                    if not _is_process_group_alive(pgid):
                        break
                    time.sleep(0.05)

                proc.poll()
                if _is_process_group_alive(pgid):
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                        cleanup_disp = "sigkill"
                    except (ProcessLookupError, PermissionError):
                        pass

                    reap_deadline = time.monotonic() + REAP_TIMEOUT_SECONDS
                    while time.monotonic() < reap_deadline:
                        proc.poll()
                        if not _is_process_group_alive(pgid):
                            break
                        time.sleep(0.05)

            proc.poll()
            if proc.returncode is None:
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass

            try:
                if proc.stdout:
                    try:
                        os.close(proc.stdout.fileno())
                    except OSError:
                        pass
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    try:
                        os.close(proc.stderr.fileno())
                    except OSError:
                        pass
                    proc.stderr.close()
            except Exception:
                pass

            t_out.join(timeout=READER_JOIN_TIMEOUT_SECONDS)
            t_err.join(timeout=READER_JOIN_TIMEOUT_SECONDS)

            proc.poll()
            group_alive = _is_process_group_alive(pgid)
            readers_alive = t_out.is_alive() or t_err.is_alive()
            if group_alive or readers_alive or proc.returncode is None:
                cleanup_disp = "incomplete"
                if failure_reason is None:
                    failure_reason = REASON_INCOMPLETE_CLEANUP

        if received_signal[0] is not None and not interrupted:
            interrupted = True
            failure_reason = REASON_INTERRUPTED

        out_clean = stdout_state.eof and not stdout_state.error and not t_out.is_alive()
        err_clean = stderr_state.eof and not stderr_state.error and not t_err.is_alive()

        stdout_sha = stdout_state.digest.hexdigest() if out_clean else None
        stderr_sha = stderr_state.digest.hexdigest() if err_clean else None

        stdout_raw = bytes(stdout_state.retained_buffer)
        stderr_raw = bytes(stderr_state.retained_buffer)

        cmd_res = CommandResult(
            argv=argv,
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout_bytes=stdout_state.bytes_read,
            stdout_sha256=stdout_sha,
            stdout_truncated=stdout_state.truncated,
            stdout_text=stdout_raw[:MAX_DIAGNOSTIC_BYTES].decode(
                "utf-8", errors="replace"
            ),
            stderr_bytes=stderr_state.bytes_read,
            stderr_sha256=stderr_sha,
            stderr_truncated=stderr_state.truncated,
            stderr_text=stderr_raw[:MAX_DIAGNOSTIC_BYTES].decode(
                "utf-8", errors="replace"
            ),
            timed_out=timed_out,
            timeout_seconds=timeout if (timed_out or drain_timed_out) else None,
            cleanup_disposition=cleanup_disp,
            stdout_raw=stdout_raw,
            stderr_raw=stderr_raw,
            failure_reason=failure_reason,
            stdout_reader_error=stdout_state.error,
            stderr_reader_error=stderr_state.error,
        )

        if interrupted:
            sig = received_signal[0]
            sig_name = (
                "SIGINT"
                if sig == signal.SIGINT
                else "SIGTERM"
                if sig == signal.SIGTERM
                else f"signal {sig}"
            )
            if sig == signal.SIGINT:
                raise KeyboardInterrupt()
            raise PromotionQualificationError(
                CATEGORY_COMMAND_FAILURE,
                f"qualification interrupted by {sig_name}",
                details={"interrupted": True, "signal": sig_name},
            )

        return cmd_res

    finally:
        for sig, old_h in installed_handlers.items():
            try:
                signal.signal(sig, old_h if old_h is not None else signal.SIG_DFL)
            except (ValueError, TypeError, OSError):
                pass


def take_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Compute non-raising snapshot of protected files using lstat and safe descriptor loop reading."""
    snapshot: dict[str, dict[str, Any]] = {}
    for rel in PROTECTED_FILES:
        file_path = root / rel
        try:
            st = os.lstat(file_path)
            is_symlink = stat.S_ISLNK(st.st_mode)
            is_reg = stat.S_ISREG(st.st_mode)
            if is_reg and not is_symlink:
                content = bytearray()
                sha256_val: str | None = None
                valid = False
                open_flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    open_flags |= os.O_NOFOLLOW
                try:
                    fd = os.open(file_path, open_flags)
                    try:
                        fst = os.fstat(fd)
                        if (
                            fst.st_ino == st.st_ino
                            and fst.st_dev == st.st_dev
                            and stat.S_ISREG(fst.st_mode)
                        ):
                            while True:
                                chunk = os.read(fd, 65536)
                                if not chunk:
                                    break
                                content.extend(chunk)
                            fst2 = os.fstat(fd)
                            if (
                                fst2.st_size == fst.st_size
                                and fst2.st_mtime_ns == fst.st_mtime_ns
                                and fst2.st_ino == fst.st_ino
                                and fst2.st_dev == fst.st_dev
                                and len(content) == fst2.st_size
                            ):
                                sha256_val = hashlib.sha256(content).hexdigest()
                                valid = True
                    finally:
                        os.close(fd)
                except (FileNotFoundError, OSError, PermissionError):
                    valid = False
                    sha256_val = None

                snapshot[rel] = {
                    "present": True,
                    "isRegularFile": True,
                    "isSymlink": False,
                    "inode": st.st_ino,
                    "dev": st.st_dev,
                    "mode": st.st_mode,
                    "bytes": len(content) if valid else st.st_size,
                    "mtimeNs": st.st_mtime_ns,
                    "sha256": sha256_val,
                    "valid": valid,
                }
            else:
                snapshot[rel] = {
                    "present": True,
                    "isRegularFile": is_reg,
                    "isSymlink": is_symlink,
                    "inode": st.st_ino,
                    "dev": st.st_dev,
                    "mode": st.st_mode,
                    "bytes": st.st_size,
                    "mtimeNs": st.st_mtime_ns,
                    "sha256": None,
                    "valid": False,
                }
        except (FileNotFoundError, OSError, PermissionError):
            snapshot[rel] = {
                "present": False,
                "isRegularFile": False,
                "isSymlink": False,
                "inode": None,
                "dev": None,
                "mode": None,
                "bytes": 0,
                "mtimeNs": None,
                "sha256": None,
                "valid": False,
            }
    return snapshot


def is_snapshot_valid(snapshot: dict[str, dict[str, Any]]) -> bool:
    """Validate that snapshot contains valid entries for all PROTECTED_FILES."""
    if not isinstance(snapshot, dict):
        return False
    for rel in PROTECTED_FILES:
        info = snapshot.get(rel)
        if not info or not isinstance(info, dict):
            return False
        if not (
            info.get("present") is True
            and info.get("isRegularFile") is True
            and info.get("isSymlink") is False
            and info.get("valid") is True
            and isinstance(info.get("inode"), int)
            and isinstance(info.get("dev"), int)
            and isinstance(info.get("mode"), int)
            and isinstance(info.get("bytes"), int)
            and isinstance(info.get("mtimeNs"), int)
            and isinstance(info.get("sha256"), str)
            and len(info["sha256"]) == 64
        ):
            return False
    return True


def check_snapshot_identical(
    pre: dict[str, dict[str, Any]],
    post: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Compare pre and post snapshots. Returns (is_identical, list_of_mutated_files)."""
    mutations: list[str] = []
    for rel in PROTECTED_FILES:
        pre_info = pre.get(rel, {"present": False, "valid": False, "sha256": None})
        post_info = post.get(rel, {"present": False, "valid": False, "sha256": None})
        if not pre_info.get("valid") or not post_info.get("valid"):
            mutations.append(rel)
            continue
        if (
            post_info.get("isRegularFile") != pre_info.get("isRegularFile")
            or post_info.get("isSymlink") != pre_info.get("isSymlink")
            or post_info.get("mode") != pre_info.get("mode")
            or post_info.get("inode") != pre_info.get("inode")
            or post_info.get("dev") != pre_info.get("dev")
            or post_info.get("bytes") != pre_info.get("bytes")
            or post_info.get("mtimeNs") != pre_info.get("mtimeNs")
            or post_info.get("sha256") != pre_info.get("sha256")
        ):
            mutations.append(rel)
    return len(mutations) == 0, mutations


def _verify_checkpoint(
    root: Path,
    pre_snapshot: dict[str, dict[str, Any]],
    checkpoint_name: str,
) -> None:
    """Verify protected files match pre_snapshot at checkpoint boundary. Raises immediately on mismatch."""
    current_snapshot = take_snapshot(root)
    identical, mutations = check_snapshot_identical(pre_snapshot, current_snapshot)
    if not identical:
        raise PromotionQualificationError(
            CATEGORY_PROTECTED_FILE_MUTATION,
            f"protected files were mutated during qualification at checkpoint '{checkpoint_name}': {', '.join(mutations)}",
            details={
                "mutatedFiles": mutations,
                "checkpoint": checkpoint_name,
            },
        )


def parse_candidate_reference(candidate: object) -> tuple[str, str]:
    """Parse candidate flake URL and extract (normalized_repo_url, 40_hex_rev).

    Requires exact grammar: <canonical-repository-url>?rev=<40-lowercase-hex>
    Rejects fragments, extra query params, duplicate params, whitespace, non-hex, etc.
    """
    if not isinstance(candidate, str) or not candidate:
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            "candidate reference must be a non-empty string",
        )
    if candidate != candidate.strip() or any(c.isspace() for c in candidate):
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            f"candidate reference contains disallowed whitespace: {candidate!r}",
        )

    split = urlsplit(candidate)
    if split.fragment:
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            f"candidate reference must not contain a fragment: {candidate}",
        )
    if not split.query:
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            f"candidate reference is missing query string: {candidate}",
        )
    if not RAW_QUERY_RE.fullmatch(split.query):
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            f"candidate query must match exact 'rev=<40-lowercase-hex>' grammar: {split.query}",
        )

    rev = split.query[4:]  # Slice off 'rev='
    base_url = urlunsplit((split.scheme, split.netloc, split.path, "", ""))
    try:
        normalized_repo = normalize_url(base_url, "candidate repository URL")
    except RegistryError as err:
        raise PromotionQualificationError(
            CATEGORY_REQUEST_VALIDATION,
            f"invalid candidate repository URL: {err}",
        ) from err

    return normalized_repo, rev


def validate_promotion_contract(
    data: object,
    registry_entries: list[dict[str, Any]],
    requested_input: str,
) -> dict[str, Any]:
    """Strict decoder and validator for composition-nd-public-promotion-v1."""
    if not isinstance(data, dict):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion contract must be a JSON object",
        )

    actual_keys = set(data.keys())
    if actual_keys != PROMOTION_TOP_LEVEL_KEYS:
        missing = sorted(PROMOTION_TOP_LEVEL_KEYS - actual_keys)
        if missing:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion contract is missing required key: {missing[0]}",
            )
        extra = sorted(actual_keys - PROMOTION_TOP_LEVEL_KEYS)
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion contract has unexpected key: {extra[0]}",
        )

    if data["schema"] != PROMOTION_CONTRACT_SCHEMA:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"unsupported promotion schema: {data['schema']!r} (expected {PROMOTION_CONTRACT_SCHEMA!r})",
        )
    if (
        data["version"] != 2
        or not isinstance(data["version"], int)
        or isinstance(data["version"], bool)
    ):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"unsupported promotion version: {data['version']!r} (expected 2)",
        )

    comp = data["composition"]
    if not isinstance(comp, dict) or set(comp.keys()) != COMPOSITION_KEYS:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.composition must declare exactly 'name' and 'repositoryUrl'",
        )
    if comp["name"] != "composition-nd":
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.composition.name must be 'composition-nd', got {comp['name']!r}",
        )
    if not isinstance(comp["repositoryUrl"], str):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.composition.repositoryUrl must be a string, got {type(comp['repositoryUrl']).__name__}",
        )
    try:
        norm_comp_url = normalize_url(
            comp["repositoryUrl"], "promotion composition URL"
        )
    except RegistryError as err:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"invalid promotion.composition.repositoryUrl: {err}",
        ) from err

    if norm_comp_url != "ssh://git@github.com/Knowledge-Forge-AI/composition_dinas":
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.composition.repositoryUrl does not match canonical repository: {comp['repositoryUrl']!r}",
        )

    system = data["system"]
    if not isinstance(system, str) or not SYSTEM_RE.fullmatch(system):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.system must be a portable system identifier: {system!r}",
        )

    modules = data["modules"]
    if (
        not isinstance(modules, list)
        or len(modules) != len(registry_entries)
        or not modules
    ):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.modules length ({len(modules) if isinstance(modules, list) else 'non-list'}) does not match registry ({len(registry_entries)})",
        )
    for idx, mod in enumerate(modules):
        if not isinstance(mod, dict) or set(mod.keys()) != MODULE_RECORD_KEYS:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}] keys do not match expected module record keys",
            )
        reg = registry_entries[idx]
        if mod["name"] != reg["name"]:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].name ({mod['name']!r}) disagrees with registry ({reg['name']!r})",
            )
        if mod["input"] != reg["input"]:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].input ({mod['input']!r}) disagrees with registry ({reg['input']!r})",
            )
        if mod["checkout"] != reg["checkout"]:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].checkout ({mod['checkout']!r}) disagrees with registry ({reg['checkout']!r})",
            )
        if not isinstance(mod["url"], str):
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].url must be a string, got {type(mod['url']).__name__}",
            )
        try:
            norm_mod_url = normalize_url(mod["url"], f"modules[{idx}].url")
            norm_reg_url = normalize_url(reg["url"], f"registry[{idx}].url")
        except RegistryError as err:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].url cannot be normalized: {err}",
            ) from err

        if norm_mod_url != norm_reg_url:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].url ({mod['url']!r}) disagrees with registry ({reg['url']!r})",
            )
        reg_strategy = reg["bootstrap"]["strategy"]
        if mod["bootstrapStrategy"] != reg_strategy:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].bootstrapStrategy ({mod['bootstrapStrategy']!r}) disagrees with registry ({reg_strategy!r})",
            )
        expected_owns = reg_strategy != "none"
        if mod["ownsMachineLocalConfiguration"] is not expected_owns:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.modules[{idx}].ownsMachineLocalConfiguration ({mod['ownsMachineLocalConfiguration']!r}) disagrees with registry expected ({expected_owns!r})",
            )

    release_channels = data["releaseChannels"]
    if (
        not isinstance(release_channels, dict)
        or set(release_channels.keys()) != RELEASE_CHANNELS_KEYS
    ):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.releaseChannels must declare exactly 'policy' and 'refs'",
        )
    if release_channels["policy"] != RELEASE_CHANNELS_POLICY:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.releaseChannels.policy must be {RELEASE_CHANNELS_POLICY!r}",
        )
    refs = release_channels["refs"]
    expected_ref_keys = {entry["input"] for entry in registry_entries}
    if not isinstance(refs, dict) or set(refs.keys()) != expected_ref_keys:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.releaseChannels.refs keys must exactly match registered inputs",
        )
    for entry in registry_entries:
        input_name = entry["input"]
        try:
            normalized = normalize_ref(
                refs[input_name],
                f"promotion.releaseChannels.refs.{input_name}",
            )
        except RegistryError as err:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"invalid promotion release ref for {input_name}: {err}",
            ) from err
        if refs[input_name] != normalized:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion release ref for {input_name} must use full refs/heads/... form",
            )
        if normalized != entry["releaseRef"]:
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion release ref for {input_name} disagrees with registry",
            )
    if requested_input not in refs:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"requested input {requested_input!r} is not present in promotion.releaseChannels.refs",
        )

    pkg_id = data["packageSetIdentity"]
    if not isinstance(pkg_id, str) or not pkg_id.strip():
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.packageSetIdentity must be a non-empty string",
        )

    hosts = data["hosts"]
    if not isinstance(hosts, list) or not hosts:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.hosts must be a non-empty array of host names",
        )
    for hst in hosts:
        if not isinstance(hst, str) or not IDENTIFIER_RE.fullmatch(hst):
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.hosts entry must be a valid host identifier: {hst!r}",
            )
    if len(hosts) != len(set(hosts)):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.hosts must not contain duplicate host names",
        )

    checks = data["checks"]
    if not isinstance(checks, list) or not checks:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.checks must be a non-empty array of check names",
        )
    for chk in checks:
        if not isinstance(chk, str) or not IDENTIFIER_RE.fullmatch(chk):
            raise PromotionQualificationError(
                CATEGORY_PROMOTION_CONTRACT,
                f"promotion.checks entry must be a valid check identifier: {chk!r}",
            )
    if len(checks) != len(set(checks)):
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.checks must not contain duplicate check names",
        )

    qual = data["qualification"]
    if not isinstance(qual, dict) or set(qual.keys()) != QUALIFICATION_KEYS:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.qualification must declare exactly 'entryPoint' and 'mode'",
        )
    if qual["entryPoint"] != "tools/qualify-promotion":
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.qualification.entryPoint must be 'tools/qualify-promotion', got {qual['entryPoint']!r}",
        )
    if qual["mode"] != "exact-candidate":
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.qualification.mode must be 'exact-candidate', got {qual['mode']!r}",
        )

    prom = data["promotableInputs"]
    if not isinstance(prom, dict) or set(prom.keys()) != PROMOTABLE_INPUTS_KEYS:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            "promotion.promotableInputs must declare exactly 'rule' and 'names'",
        )
    if prom["rule"] != "registered-direct-inputs-only":
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.promotableInputs.rule must be 'registered-direct-inputs-only', got {prom['rule']!r}",
        )
    reg_input_names = [r["input"] for r in registry_entries]
    if prom["names"] != reg_input_names:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"promotion.promotableInputs.names ({prom['names']}) does not match registry inputs ({reg_input_names})",
        )
    if requested_input not in prom["names"]:
        raise PromotionQualificationError(
            CATEGORY_PROMOTION_CONTRACT,
            f"requested input {requested_input!r} is not present in promotion.promotableInputs.names",
        )

    return data


def _invoke_runner(
    runner: Callable[..., Any],
    cmd: list[str],
    timeout: float,
    max_stdout_retained_bytes: int,
) -> Any:
    """Inspect runner parameters and invoke runner exactly once."""
    supports_kwargs = False
    kwargs: dict[str, Any] = {}
    try:
        sig = inspect.signature(runner)
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        param_names = set(sig.parameters.keys())
        if has_var_keyword or "timeout" in param_names:
            kwargs["timeout"] = timeout
        if has_var_keyword or "max_stdout_retained_bytes" in param_names:
            kwargs["max_stdout_retained_bytes"] = max_stdout_retained_bytes
        supports_kwargs = bool(kwargs or has_var_keyword)
    except (ValueError, TypeError):
        supports_kwargs = False
        kwargs = {}

    if supports_kwargs:
        return runner(cmd, **kwargs)
    return runner(cmd)


def _run_command_helper(
    cmd: list[str],
    runner: Callable[..., Any],
    executed_commands: list[dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_stdout_retained_bytes: int = MAX_DIAGNOSTIC_BYTES,
) -> tuple[int, str, str, CommandResult | None]:
    """Execute command via runner, record to executed_commands, and return (rc, stdout, stderr, res_obj)."""
    res_obj = _invoke_runner(
        runner,
        cmd,
        timeout=timeout,
        max_stdout_retained_bytes=max_stdout_retained_bytes,
    )

    if isinstance(res_obj, CommandResult):
        res_dict = res_obj.to_dict()
        rc = res_obj.exit_code
        stdout_str = res_obj.stdout_text
        stderr_str = res_obj.stderr_text
        command_obj: CommandResult | None = res_obj
    elif isinstance(res_obj, tuple):
        if (
            len(res_obj) != 3
            or type(res_obj[0]) is not int
            or isinstance(res_obj[0], bool)
            or not isinstance(res_obj[1], str)
            or not isinstance(res_obj[2], str)
        ):
            raise PromotionQualificationError(
                CATEGORY_INTERNAL_ERROR,
                f"runner returned malformed tuple: expected (int, str, str), got {res_obj!r}",
            )
        rc, out, err = res_obj
        stdout_str = out
        stderr_str = err
        out_bytes = out.encode("utf-8")
        err_bytes = err.encode("utf-8")
        out_preview = (
            out_bytes[:MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="replace")
            if out_bytes
            else ""
        )
        err_preview = (
            err_bytes[:MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="replace")
            if err_bytes
            else ""
        )
        res_dict = {
            "argv": cmd,
            "exitCode": rc,
            "status": "pass" if rc == 0 else "fail",
            "timedOut": False,
            "timeoutSeconds": None,
            "cleanupDisposition": None,
            "stdout": {
                "bytes": len(out_bytes),
                "sha256": hashlib.sha256(out_bytes).hexdigest(),
                "truncated": False,
                "preview": out_preview,
            },
            "stderr": {
                "bytes": len(err_bytes),
                "sha256": hashlib.sha256(err_bytes).hexdigest(),
                "truncated": False,
                "preview": err_preview,
            },
        }
        command_obj = None
    else:
        raise PromotionQualificationError(
            CATEGORY_INTERNAL_ERROR,
            f"runner returned unsupported object type: {type(res_obj).__name__}",
        )

    executed_commands.append(res_dict)

    if command_obj is not None:
        if (
            command_obj.timed_out
            or command_obj.failure_reason is not None
            or command_obj.cleanup_disposition in ("incomplete", "uncontained")
        ):
            reason = command_obj.failure_reason or (
                "timed out" if command_obj.timed_out else "supervision failure"
            )
            raise PromotionQualificationError(
                CATEGORY_COMMAND_FAILURE,
                f"command failed ({reason}) after {timeout} seconds: {' '.join(cmd)}",
                details={
                    "timeoutSeconds": timeout,
                    "timedOut": command_obj.timed_out,
                    "failureReason": command_obj.failure_reason,
                    "cleanupDisposition": command_obj.cleanup_disposition,
                    "argv": cmd,
                    "exitCode": rc,
                    "stdoutBytes": command_obj.stdout_bytes,
                    "stdoutSha256": command_obj.stdout_sha256,
                    "stderrBytes": command_obj.stderr_bytes,
                    "stderrSha256": command_obj.stderr_sha256,
                    "stderr": stderr_str[:2000],
                },
            )

    return rc, stdout_str, stderr_str, command_obj


def observe_candidate_resolution(
    composition_root: Path,
    input_name: str,
    candidate_ref: str,
    registered_url: str,
    requested_rev: str,
    runner: Callable[..., Any],
    executed_commands: list[dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Observe candidate identity through Nix flake metadata on composition root with override-input."""
    meta_cmd = [
        "nix",
        "flake",
        "metadata",
        str(composition_root),
        "--override-input",
        input_name,
        candidate_ref,
        "--json",
        "--no-write-lock-file",
    ]
    res_rc, res_stdout, res_stderr, command_obj = _run_command_helper(
        meta_cmd,
        runner,
        executed_commands,
        timeout=timeout,
        max_stdout_retained_bytes=MAX_JSON_BYTES,
    )
    res_bytes = executed_commands[-1]["stdout"]["bytes"]
    res_trunc = executed_commands[-1]["stdout"]["truncated"]

    if res_rc != 0:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"nix flake metadata failed for input {input_name!r}: {res_stderr.strip()[:2000]}",
            details={"exitCode": res_rc, "stderr": res_stderr[:2000]},
        )

    if res_bytes > MAX_JSON_BYTES or res_trunc:
        raise PromotionQualificationError(
            CATEGORY_MALFORMED_PROVIDER_OUTPUT,
            f"nix flake metadata JSON output exceeded size limit ({res_bytes} > {MAX_JSON_BYTES})",
        )

    raw_payload = (
        command_obj.stdout_raw
        if command_obj is not None and command_obj.stdout_raw is not None
        else res_stdout.encode("utf-8")
    )
    try:
        decoded_text = raw_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as err:
        raise PromotionQualificationError(
            CATEGORY_MALFORMED_PROVIDER_OUTPUT,
            f"nix flake metadata returned invalid UTF-8: {err}",
            details={"byteOffset": err.start, "reason": err.reason},
        ) from err

    try:
        meta_json = json.loads(decoded_text)
    except json.JSONDecodeError as err:
        raise PromotionQualificationError(
            CATEGORY_MALFORMED_PROVIDER_OUTPUT,
            f"nix flake metadata returned malformed JSON: {err}",
        ) from err

    try:
        locks = meta_json["locks"]
        root_node_key = locks["root"]
        root_inputs = locks["nodes"][root_node_key]["inputs"]
        node_key = root_inputs[input_name]
        if not isinstance(node_key, str):
            raise TypeError(
                f"expected string node key for input {input_name!r}, got {type(node_key).__name__}"
            )
        node = locks["nodes"][node_key]
        locked = node["locked"]
    except (KeyError, TypeError) as err:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"nix flake metadata lock graph missing node for input {input_name!r}: {err}",
        ) from err

    locked_type = locked.get("type")
    if locked_type != "git":
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate type must be 'git', got {locked_type!r}",
        )

    locked_rev = locked.get("rev")
    if not isinstance(locked_rev, str) or not HEX_40.fullmatch(locked_rev):
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate locked.rev is missing or not a 40-hex Git revision: {locked_rev!r}",
        )

    locked_url = locked.get("url")
    if not isinstance(locked_url, str):
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate locked.url is missing: {locked_url!r}",
        )

    try:
        norm_locked_url = normalize_url(
            locked_url, "observed locked repository URL"
        )
    except RegistryError as err:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate locked.url cannot be normalized: {err}",
        ) from err

    try:
        norm_reg_url = normalize_url(
            registered_url, "registered repository URL"
        )
    except RegistryError as err:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"registered URL cannot be normalized: {err}",
        ) from err

    if norm_locked_url != norm_reg_url:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate repository URL ({locked_url!r}) does not match registered URL ({registered_url!r})",
        )

    if locked_rev != requested_rev:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate revision ({locked_rev}) does not match requested revision ({requested_rev})",
        )

    if "dir" in locked:
        raise PromotionQualificationError(
            CATEGORY_CANDIDATE_RESOLUTION,
            f"observed candidate has disallowed subdirectory selector: {locked['dir']!r}",
        )

    return {
        "url": candidate_ref,
        "requestedRev": requested_rev,
        "resolvedRev": locked_rev,
        "resolvedUrl": norm_locked_url,
        "revMatched": locked_rev == requested_rev,
        "urlMatched": norm_locked_url == norm_reg_url,
    }


def build_empty_result(
    input_name: str,
    candidate_ref: str,
    failure_category: str = CATEGORY_INTERNAL_ERROR,
    failure_message: str = "qualification did not run",
    details: dict[str, Any] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Construct an empty, well-formed result dictionary with a failure record."""
    return {
        "schema": RESULT_SCHEMA,
        "input": input_name,
        "registry": None,
        "candidate": {
            "url": candidate_ref,
            "requestedRev": None,
            "resolvedRev": None,
            "resolvedUrl": None,
            "revMatched": False,
            "urlMatched": False,
        },
        "qualification": {
            "entryPoint": "tools/qualify-promotion",
            "mode": "exact-candidate",
            "timeoutSeconds": timeout_seconds,
        },
        "commands": [],
        "checks": {},
        "hosts": {},
        "lock": {
            "preSha256": None,
            "postSha256": None,
            "byteIdentical": False,
        },
        "tree": {
            "flakeNixPreSha256": None,
            "flakeNixPostSha256": None,
            "registryJsonPreSha256": None,
            "registryJsonPostSha256": None,
            "byteIdentical": False,
        },
        "outcome": "fail",
        "failure": {
            "category": failure_category,
            "message": failure_message,
            "details": details or {},
        },
    }


def qualify_candidate(
    composition_root: Path,
    input_name: str,
    candidate_ref: str,
    runner: Callable[..., Any] = default_runner,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Non-mutatively qualify a candidate promotion for a registered direct module input."""
    executed_commands: list[dict[str, Any]] = []
    checks_disposition: dict[str, str] = {}
    hosts_disposition: dict[str, dict[str, Any]] = {}
    registry_info: dict[str, Any] | None = None
    candidate_info: dict[str, Any] = {
        "url": candidate_ref,
        "requestedRev": None,
        "resolvedRev": None,
        "resolvedUrl": None,
        "revMatched": False,
        "urlMatched": False,
    }
    failure_record: dict[str, Any] | None = None

    pre_snapshot: dict[str, dict[str, Any]] = {}
    post_snapshot: dict[str, dict[str, Any]] = {}
    is_identical = False
    root = composition_root

    try:
        # Validate timeout_seconds
        timeout_seconds = validate_timeout_seconds(timeout_seconds)

        # Step 0: Resolve composition root and take entry snapshot
        try:
            root = composition_root.resolve(strict=True)
        except Exception as err:
            raise PromotionQualificationError(
                CATEGORY_REQUEST_VALIDATION,
                f"composition root cannot be resolved: {err}",
            ) from err

        pre_snapshot = take_snapshot(root)
        if not is_snapshot_valid(pre_snapshot):
            for rel in PROTECTED_FILES:
                info = pre_snapshot.get(rel, {})
                if not info.get("present"):
                    raise PromotionQualificationError(
                        CATEGORY_REQUEST_VALIDATION,
                        f"protected file '{rel}' is missing from composition root '{root}'",
                    )
                if info.get("isSymlink") or not info.get("isRegularFile"):
                    raise PromotionQualificationError(
                        CATEGORY_REQUEST_VALIDATION,
                        f"protected file '{rel}' must be an ordinary regular file, not a symlink",
                    )
                if not info.get("valid") or info.get("sha256") is None:
                    raise PromotionQualificationError(
                        CATEGORY_REQUEST_VALIDATION,
                        f"protected file '{rel}' failed snapshot validation (descriptor/read error)",
                    )
            raise PromotionQualificationError(
                CATEGORY_REQUEST_VALIDATION,
                f"entry snapshot of protected files in '{root}' is invalid",
            )

        # Step 1: Parse candidate reference
        norm_candidate_url, candidate_rev = parse_candidate_reference(
            candidate_ref
        )
        candidate_info["requestedRev"] = candidate_rev

        # Step 2: Load registry and validate requested input
        registry_path = root / "registry.json"
        try:
            registry_entries = load_registry(registry_path)
        except RegistryError as reg_err:
            raise PromotionQualificationError(
                CATEGORY_REGISTRY_CONTRACT,
                f"invalid registry.json contract: {reg_err}",
            ) from reg_err
        except Exception as err:
            raise PromotionQualificationError(
                CATEGORY_REGISTRY_CONTRACT,
                f"failed to read registry.json: {err}",
            ) from err

        module_entry = get_module_by_input(registry_entries, input_name)
        if module_entry is None:
            raise PromotionQualificationError(
                CATEGORY_REQUEST_VALIDATION,
                f"input {input_name!r} is not a registered direct module in {registry_path}",
            )

        registered_url = module_entry["url"]
        try:
            norm_reg_url = normalize_url(
                registered_url, f"registry module {module_entry['name']!r}"
            )
        except RegistryError as reg_err:
            raise PromotionQualificationError(
                CATEGORY_REGISTRY_CONTRACT,
                f"invalid registered module URL: {reg_err}",
            ) from reg_err

        if norm_candidate_url != norm_reg_url:
            raise PromotionQualificationError(
                CATEGORY_REQUEST_VALIDATION,
                f"candidate repository URL ({candidate_ref}) does not match registered URL ({registered_url})",
            )

        registry_info = {
            "name": module_entry["name"],
            "input": module_entry["input"],
            "url": module_entry["url"],
            "checkout": module_entry["checkout"],
            "bootstrapStrategy": module_entry["bootstrap"]["strategy"],
        }

        # Step 3: Observe candidate resolution via Nix metadata on root with override-input
        observed_candidate = observe_candidate_resolution(
            composition_root=root,
            input_name=input_name,
            candidate_ref=candidate_ref,
            registered_url=registered_url,
            requested_rev=candidate_rev,
            runner=runner,
            executed_commands=executed_commands,
            timeout=timeout_seconds,
        )
        candidate_info.update(observed_candidate)
        _verify_checkpoint(root, pre_snapshot, "observe-candidate-resolution")

        # Step 4: Evaluate and validate lib.promotion
        eval_cmd = [
            "nix",
            "eval",
            "--json",
            "--no-write-lock-file",
            "--override-input",
            input_name,
            candidate_ref,
            f"{root}#lib.promotion",
        ]
        rc, out, err, eval_cmd_obj = _run_command_helper(
            eval_cmd,
            runner,
            executed_commands,
            timeout=timeout_seconds,
            max_stdout_retained_bytes=MAX_JSON_BYTES,
        )
        if rc != 0:
            raise PromotionQualificationError(
                CATEGORY_COMMAND_FAILURE,
                f"evaluation of lib.promotion failed: {err.strip()[:2000]}",
                details={"exitCode": rc, "stderr": err[:2000]},
            )

        eval_bytes = executed_commands[-1]["stdout"]["bytes"]
        eval_trunc = executed_commands[-1]["stdout"]["truncated"]
        if eval_bytes > MAX_JSON_BYTES or eval_trunc:
            raise PromotionQualificationError(
                CATEGORY_MALFORMED_PROVIDER_OUTPUT,
                f"lib.promotion JSON output exceeded size limit ({eval_bytes} > {MAX_JSON_BYTES})",
            )

        raw_eval_payload = (
            eval_cmd_obj.stdout_raw
            if eval_cmd_obj is not None and eval_cmd_obj.stdout_raw is not None
            else out.encode("utf-8")
        )
        try:
            decoded_eval_text = raw_eval_payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as err_utf8:
            raise PromotionQualificationError(
                CATEGORY_MALFORMED_PROVIDER_OUTPUT,
                f"lib.promotion returned invalid UTF-8: {err_utf8}",
                details={"byteOffset": err_utf8.start, "reason": err_utf8.reason},
            ) from err_utf8

        try:
            raw_promotion_data = json.loads(decoded_eval_text)
        except json.JSONDecodeError as err_json:
            raise PromotionQualificationError(
                CATEGORY_MALFORMED_PROVIDER_OUTPUT,
                f"lib.promotion returned malformed JSON: {err_json}",
            ) from err_json

        promotion_data = validate_promotion_contract(
            raw_promotion_data,
            registry_entries,
            input_name,
        )

        _verify_checkpoint(root, pre_snapshot, "eval-lib-promotion")

        declared_checks: list[str] = promotion_data["checks"]
        declared_hosts: list[str] = promotion_data["hosts"]
        system: str = promotion_data["system"]

        for chk in declared_checks:
            checks_disposition[chk] = "pending"
        for hst in declared_hosts:
            hosts_disposition[hst] = {"status": "pending", "drvPath": ""}

        # Step 5: Build declared checks
        for chk in declared_checks:
            build_chk_cmd = [
                "nix",
                "build",
                "--no-link",
                "--no-write-lock-file",
                "--override-input",
                input_name,
                candidate_ref,
                f"{root}#checks.{system}.{chk}",
            ]
            rc_chk, out_chk, err_chk, _ = _run_command_helper(
                build_chk_cmd,
                runner,
                executed_commands,
                timeout=timeout_seconds,
                max_stdout_retained_bytes=MAX_DIAGNOSTIC_BYTES,
            )
            if rc_chk != 0:
                checks_disposition[chk] = "fail"
                for remaining_chk in declared_checks:
                    if checks_disposition[remaining_chk] == "pending":
                        checks_disposition[remaining_chk] = "not-run"
                for remaining_hst in declared_hosts:
                    if hosts_disposition[remaining_hst]["status"] == "pending":
                        hosts_disposition[remaining_hst]["status"] = "not-run"
                raise PromotionQualificationError(
                    CATEGORY_COMMAND_FAILURE,
                    f"public check {chk!r} failed: {err_chk.strip()[:2000]}",
                    details={
                        "check": chk,
                        "exitCode": rc_chk,
                        "stderr": err_chk[:2000],
                    },
                )
            _verify_checkpoint(root, pre_snapshot, f"check-{chk}")
            checks_disposition[chk] = "pass"

        # Step 6: Build declared hosts and query drvPath
        for hst in declared_hosts:
            build_hst_cmd = [
                "nix",
                "build",
                "--no-link",
                "--no-write-lock-file",
                "--override-input",
                input_name,
                candidate_ref,
                f"{root}#darwinConfigurations.{hst}.system",
            ]
            rc_hst, out_hst, err_hst, _ = _run_command_helper(
                build_hst_cmd,
                runner,
                executed_commands,
                timeout=timeout_seconds,
                max_stdout_retained_bytes=MAX_DIAGNOSTIC_BYTES,
            )
            if rc_hst != 0:
                hosts_disposition[hst]["status"] = "fail"
                for remaining_hst in declared_hosts:
                    if hosts_disposition[remaining_hst]["status"] == "pending":
                        hosts_disposition[remaining_hst]["status"] = "not-run"
                raise PromotionQualificationError(
                    CATEGORY_COMMAND_FAILURE,
                    f"host build {hst!r} failed: {err_hst.strip()[:2000]}",
                    details={
                        "host": hst,
                        "exitCode": rc_hst,
                        "stderr": err_hst[:2000],
                    },
                )
            _verify_checkpoint(root, pre_snapshot, f"host-build-{hst}")

            eval_drv_cmd = [
                "nix",
                "eval",
                "--raw",
                "--no-write-lock-file",
                "--override-input",
                input_name,
                candidate_ref,
                f"{root}#darwinConfigurations.{hst}.system.drvPath",
            ]
            rc_drv, out_drv, err_drv, cmd_res_drv = _run_command_helper(
                eval_drv_cmd,
                runner,
                executed_commands,
                timeout=timeout_seconds,
                max_stdout_retained_bytes=MAX_DIAGNOSTIC_BYTES,
            )
            if rc_drv != 0:
                hosts_disposition[hst]["status"] = "fail"
                raise PromotionQualificationError(
                    CATEGORY_COMMAND_FAILURE,
                    f"querying drvPath for host {hst!r} failed: {err_drv.strip()[:2000]}",
                    details={
                        "host": hst,
                        "exitCode": rc_drv,
                        "stderr": err_drv[:2000],
                    },
                )
            if cmd_res_drv and cmd_res_drv.stdout_raw is not None:
                try:
                    raw_drv_str = cmd_res_drv.stdout_raw.decode("utf-8", errors="strict").strip()
                except UnicodeDecodeError as err:
                    hosts_disposition[hst]["status"] = "fail"
                    raise PromotionQualificationError(
                        CATEGORY_MALFORMED_PROVIDER_OUTPUT,
                        f"querying drvPath for host {hst!r} produced invalid UTF-8: {err}",
                        details={"host": hst},
                    ) from err
            else:
                raw_drv_str = out_drv.strip()

            if not DRV_PATH_PATTERN.match(raw_drv_str):
                hosts_disposition[hst]["status"] = "fail"
                raise PromotionQualificationError(
                    CATEGORY_MALFORMED_PROVIDER_OUTPUT,
                    f"querying drvPath for host {hst!r} returned invalid store-path derivation: {raw_drv_str!r}",
                    details={"host": hst, "output": raw_drv_str},
                )
            drv_path = raw_drv_str
            _verify_checkpoint(root, pre_snapshot, f"host-drv-{hst}")
            hosts_disposition[hst] = {
                "status": "pass",
                "drvPath": drv_path,
            }

    except PromotionQualificationError as err:
        failure_record = {
            "category": err.category,
            "message": err.message,
            "details": err.details,
        }
    except Exception as err:
        failure_record = {
            "category": CATEGORY_INTERNAL_ERROR,
            "message": f"unexpected internal error: {err}",
            "details": {"type": type(err).__name__},
        }
    finally:
        for chk, status in list(checks_disposition.items()):
            if status == "pending":
                checks_disposition[chk] = "not-run"
        for hst, info in list(hosts_disposition.items()):
            if info["status"] == "pending":
                hosts_disposition[hst]["status"] = "not-run"

        if is_snapshot_valid(pre_snapshot):
            post_snapshot = take_snapshot(root)
            is_identical, mutations = check_snapshot_identical(
                pre_snapshot, post_snapshot
            )
            if not is_identical:
                if failure_record is not None:
                    if failure_record.get("category") != CATEGORY_PROTECTED_FILE_MUTATION:
                        initiating_failure = failure_record
                        failure_record = {
                            "category": CATEGORY_PROTECTED_FILE_MUTATION,
                            "message": f"protected files were mutated during qualification: {', '.join(mutations)}",
                            "details": {
                                "mutatedFiles": mutations,
                                "initiatingFailure": initiating_failure,
                            },
                        }
                else:
                    failure_record = {
                        "category": CATEGORY_PROTECTED_FILE_MUTATION,
                        "message": f"protected files were mutated during qualification: {', '.join(mutations)}",
                        "details": {"mutatedFiles": mutations},
                    }
        else:
            is_identical = False

    snapshots_valid = is_snapshot_valid(pre_snapshot) and is_snapshot_valid(post_snapshot)

    lock_pre = pre_snapshot.get("flake.lock", {}).get("sha256")
    lock_post = post_snapshot.get("flake.lock", {}).get("sha256")
    lock_identical = (
        snapshots_valid
        and (lock_pre is not None)
        and (lock_post is not None)
        and (lock_pre == lock_post)
    )

    flake_pre = pre_snapshot.get("flake.nix", {}).get("sha256")
    flake_post = post_snapshot.get("flake.nix", {}).get("sha256")
    reg_pre = pre_snapshot.get("registry.json", {}).get("sha256")
    reg_post = post_snapshot.get("registry.json", {}).get("sha256")
    tree_identical = (
        snapshots_valid
        and (flake_pre is not None)
        and (flake_post is not None)
        and (reg_pre is not None)
        and (reg_post is not None)
        and (flake_pre == flake_post)
        and (reg_pre == reg_post)
    )

    outcome = (
        "pass"
        if (
            failure_record is None
            and is_identical
            and snapshots_valid
            and lock_identical
            and tree_identical
        )
        else "fail"
    )

    return {
        "schema": RESULT_SCHEMA,
        "input": input_name,
        "registry": registry_info,
        "candidate": candidate_info,
        "qualification": {
            "entryPoint": "tools/qualify-promotion",
            "mode": "exact-candidate",
            "timeoutSeconds": timeout_seconds,
        },
        "commands": executed_commands,
        "checks": checks_disposition,
        "hosts": hosts_disposition,
        "lock": {
            "preSha256": lock_pre,
            "postSha256": lock_post,
            "byteIdentical": lock_identical,
        },
        "tree": {
            "flakeNixPreSha256": flake_pre,
            "flakeNixPostSha256": flake_post,
            "registryJsonPreSha256": reg_pre,
            "registryJsonPostSha256": reg_post,
            "byteIdentical": tree_identical,
        },
        "outcome": outcome,
        "failure": failure_record,
    }
