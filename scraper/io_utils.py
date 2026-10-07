from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def atomic_output_path(path: str | Path):
    """Yield a sibling temporary path and atomically replace the target.

    The caller writes the complete artifact to the yielded path. Only after
    the context exits successfully is the final path replaced. If writing
    fails, the previous target remains untouched and the temporary file is
    removed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.stem}.{os.getpid()}.{uuid4().hex}.tmp{target.suffix}"
    )

    try:
        yield temporary
        if not temporary.exists():
            raise FileNotFoundError(
                f"El artefacto temporal no fue creado: {temporary}"
            )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass



@contextmanager
def exclusive_file_lock(
    path: str | Path,
    *,
    timeout_seconds: float = 120.0,
    stale_seconds: float = 900.0,
    poll_seconds: float = 0.25,
):
    """Serialize read-modify-write access to a shared artifact.

    Uses an exclusive sibling lock file. A very old lock is considered stale
    so an interrupted process cannot block future runs forever.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    acquired = False

    while not acquired:
        try:
            fd = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age >= stale_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "No se pudo adquirir el lock de escritura para "
                    f"{target} en {timeout_seconds:.0f}s"
                )
            time.sleep(max(0.05, poll_seconds))
            continue

        try:
            payload = (
                f"pid={os.getpid()}\n"
                f"created={time.time():.6f}\n"
                f"target={target}\n"
            ).encode("utf-8")
            os.write(fd, payload)
        finally:
            os.close(fd)
        acquired = True

    try:
        yield lock_path
    finally:
        if acquired:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
