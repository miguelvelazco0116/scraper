from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_phase1_keeps_same_day_progress_checkpoint():
    source = _source("scripts/run_phase1_production.py")
    assert 'PROGRESS_PATH = ROOT / "output" / "fase1_progress.xlsx"' in source
    assert "def _load_existing_valid_results" in source
    assert "Sólo se ejecutarán casos pendientes o fallidos." in source


def test_phase2_does_not_delete_retailer_checkpoints():
    source = _source("scripts/run_phase2_production.py")
    child = source.split("def _run_child", 1)[1].split("def ", 1)[0]
    assert "output_path.unlink" not in child


def test_phase3_does_not_delete_retailer_checkpoints():
    source = _source("scripts/run_phase3_production.py")
    assert "BODEGA_OUTPUT.unlink" not in source
    assert "WALMART_OUTPUT.unlink" not in source


def test_bodega_and_walmart_reuse_same_day_successes():
    bodega = _source("scripts/test_bodega_aurrera.py")
    walmart = _source("scripts/test_walmart_phase3.py")
    assert "def _load_existing_valid_results" in bodega
    assert "CATEGORÍAS BODEGA YA VÁLIDAS HOY" in bodega
    assert "def _load_existing_valid_results" in walmart
    assert "CATEGORÍAS WALMART YA VÁLIDAS HOY" in walmart


def test_phase4_reuses_same_day_successes():
    san_pablo = _source("scripts/run_san_pablo_full.py")
    benavides = _source("scripts/test_benavides.py")
    assert "def _load_existing_valid_results" in san_pablo
    assert "CATEGORÍAS SAN PABLO YA VÁLIDAS HOY" in san_pablo
    assert "def _load_existing_valid_results" in benavides
    assert "CATEGORÍAS BENAVIDES YA VÁLIDAS HOY" in benavides
