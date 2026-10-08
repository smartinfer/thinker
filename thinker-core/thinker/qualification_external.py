"""Bounded host-configured application validator subprocess; no provider credentials."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from .qualification_store import encode, strict_json, write_new


def invoke_validator(command, payload, timeout, directory):
    directory = Path(directory)
    write_new(directory / "validator-input.json", encode(payload))
    # Validators are trusted application code, not model-authored commands. Environment is
    # deliberately credential-free. Isolation from network requires OS policy, not this API.
    env = {
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LANG": "C.UTF-8",
    }
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            cwd=directory,
            env=env,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.communicate(encode(payload), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        finally:
            # Reap remaining descendants even if the main validator exited first.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout.seek(0)
        stderr.seek(0)
        out = stdout.read(4_000_001)
        err = stderr.read(4_000_001)
    write_new(directory / "validator-stdout.bin", out)
    write_new(directory / "validator-stderr.bin", err)
    if timed_out:
        raise ValueError("VALIDATOR_TIMED_OUT")
    if process.returncode != 0 or max(len(out), len(err)) > 4_000_000:
        raise ValueError("VALIDATOR_EXECUTION_FAILED")
    value = strict_json(out)
    if not isinstance(value, dict) or value.get("status") not in {"PASS", "FAIL", "REVIEW"}:
        raise ValueError("VALIDATOR_PROTOCOL_ERROR")
    if not isinstance(value.get("diagnostics"), list):
        raise ValueError("VALIDATOR_PROTOCOL_ERROR")  # noqa: TRY004 -- uniform validator protocol failure
    return value
