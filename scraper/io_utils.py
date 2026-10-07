from __future__ import annotations

import os
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
