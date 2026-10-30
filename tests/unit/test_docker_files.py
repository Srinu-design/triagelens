from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_configuration():
    dockerfile_path = ROOT / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist at repository root"
    content = dockerfile_path.read_text(encoding="utf-8")

    # Check non-root user
    assert "USER app" in content, "Dockerfile must switch to USER app"
    assert "10001" in content, "Dockerfile must use uid 10001 for app user"

    # Check offline embeddings env var
    assert "HF_HUB_OFFLINE=1" in content, "Dockerfile must set HF_HUB_OFFLINE=1"

    # Check uvicorn runs with exactly one worker
    assert "--workers 1" in content or '"--workers", "1"' in content, (
        "Dockerfile must run uvicorn with one worker"
    )


def test_dockerignore_configuration():
    dockerignore_path = ROOT / ".dockerignore"
    assert dockerignore_path.exists(), ".dockerignore must exist at repository root"
    content = dockerignore_path.read_text(encoding="utf-8")
    lines = [
        line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")
    ]

    # Check .env is excluded
    assert any(".env" in line for line in lines), ".dockerignore must exclude .env files"

    # Check test directories, git, and venv are excluded
    assert any(".venv" in line for line in lines)
    assert any(".git" in line for line in lines)
    assert any("tests" in line for line in lines)
    assert any("docs" in line for line in lines)
    assert any("bench" in line for line in lines)
