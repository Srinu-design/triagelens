"""Unit tests verifying deployment files, CI workflow, compose, and smoke test safety."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "compose.prod.yaml"
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy.sh"
CI_FILE = ROOT / ".github" / "workflows" / "ci.yml"
SMOKE_FILE = ROOT / "app" / "smoke.py"


def test_compose_publishes_no_ports_and_joins_external_portfolio():
    assert COMPOSE_FILE.exists(), "deploy/compose.prod.yaml must exist"
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    # Plain text check: joins external portfolio network
    assert "portfolio" in content
    assert "external: true" in content

    # Plain text check: publishes no ports
    code_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in code_lines:
        assert not line.startswith("ports:"), f"compose file must not publish ports: {line}"


def test_deploy_script_passes_bash_syntax_check():
    assert DEPLOY_SCRIPT.exists(), "deploy/deploy.sh must exist"
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"deploy.sh syntax error: {result.stderr}"


def test_ci_workflow_offline_and_pgvector_version():
    assert CI_FILE.exists(), ".github/workflows/ci.yml must exist"
    content = CI_FILE.read_text(encoding="utf-8")

    assert "HF_HUB_OFFLINE" in content
    assert "pgvector/pgvector:0.8.7" in content


def test_smoke_script_never_posts_items():
    assert SMOKE_FILE.exists(), "app/smoke.py must exist"
    content = SMOKE_FILE.read_text(encoding="utf-8")

    # smoke.py must not write data or commit items
    assert "/api/items" not in content


def from_ssh(root: Path, word: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TRIAGELENS_ROOT": str(root), "SSH_ORIGINAL_COMMAND": word}
    return subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--from-ssh"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "word",
    ["", "main", "touch pwned", "a" * 40 + "; touch pwned", "$(touch pwned)", "--help", "A" * 40],
)
def test_from_ssh_refuses_anything_except_one_safe_word(tmp_path: Path, word: str) -> None:
    result = from_ssh(tmp_path, word)
    assert result.returncode == 2
    assert "expected a commit sha" in result.stderr
    assert list(tmp_path.iterdir()) == []
