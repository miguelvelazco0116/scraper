"""Guard Windows PowerShell project-root resolution in every CLI entrypoint.

A default parameter expression that references $PSScriptRoot can evaluate
before automatic script variables are populated; when invoked via -File from
the Streamlit worker, Split-Path throws before the Python integrator runs.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
OLD_DEFAULT = re.compile(
    r'\[string\]\s*\$ProjectDir\s*=\s*\(\s*Split-Path\s+-Parent\s+\$PSScriptRoot\s*\)',
    re.IGNORECASE,
)
SAFE_DEFAULT = re.compile(r'\[string\]\s*\$ProjectDir\s*=\s*""', re.IGNORECASE)


def test_no_script_uses_psscriptroot_as_param_default():
    all_scripts = sorted(SCRIPT_DIR.glob("*.ps1"))
    assert all_scripts, "No PowerShell scripts were checked"
    bad = [script.name for script in all_scripts
           if OLD_DEFAULT.search(script.read_text(encoding="utf-8-sig"))]
    assert not bad, ("PowerShell evaluates $PSScriptRoot too early: " + ", ".join(bad))


def test_all_updated_wrappers_resolve_root_after_param():
    scripts = sorted(SCRIPT_DIR.glob("*.ps1"))
    for script in scripts:
        text = script.read_text(encoding="utf-8-sig")
        if not SAFE_DEFAULT.search(text):
            continue
        assert '$MyInvocation.MyCommand.Path' in text, script.name
        assert 'if ([string]::IsNullOrWhiteSpace($ProjectDir))' in text, script.name
        assert text.index('$MyInvocation.MyCommand.Path') > text.index(')', text.index('param(')), script.name


def test_integrate_existing_and_streamlit_explicit_project_directory():
    from dashboard.execution import ALL, RUNNERS, public_command
    text = (ROOT / "dashboard" / "execution.py").read_text(encoding="utf-8")
    assert 'if mode == "solo_integracion":' in text
    assert 'args.extend(["-ProjectDir", str(root.resolve())])' in text
    for phase in (1, 2, 3, 4, 5, 6):
        command = public_command(ROOT, phase, ALL, "solo_integracion")
        assert command and "integrate" in command
        name = ("integrate_phase2_postal.ps1" if phase == 2 else
                "integrate_phase6_yza.ps1" if phase == 6 else
                "integrate_existing_phases.ps1")
        source = (SCRIPT_DIR / name).read_text(encoding="utf-8")
        assert '[string]$ProjectDir = ""' in source
        assert '$ProjectDir = Split-Path -Parent' in source
