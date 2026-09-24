"""Build a source handoff archive from an explicit allowlist; no data or credentials."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "dist" / "mcp-backend-A.zip"
    output.parent.mkdir(exist_ok=True)
    files = [
        root / name
        for name in (
            "pyproject.toml",
            "requirements-dev.lock",
            "requirements-semantic.lock",
            "start_hub.cmd",
            "docs/HUB_RUNBOOK.md",
            "docs/MCP_ARCHITECTURE.md",
            "docs/DASHBOARD_HANDOFF.md",
            "docs/CHATGPT_CONNECTION.md",
            "docs/contracts/openapi.json",
            "docs/contracts/examples.json",
            "samples/hub_ingest.json",
            "samples/hub_sync.json",
            "tests/conftest.py",
            "deploy/package_hub.py",
            "deploy/verify_hub.py",
        )
    ]
    files += sorted((root / "src" / "research_agent").rglob("*.py"))
    files += sorted((root / "src" / "research_agent" / "web").glob("*.html"))
    files += sorted((root / "tests").glob("test_hub*.py"))
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.md",
            (
                "# Internal data MCP backend — A handoff\n\n"
                "Start with docs/HUB_RUNBOOK.md. Python 3.11+ required.\n"
                'Install: python -m pip install -c requirements-dev.lock -e ".[mcp]"\n'
                "Initialize: python -m research_agent.hub_cli init\n"
                "Run: python -m research_agent.hub_cli serve\n\n"
                "This package contains source, API contracts, tests, and synthetic examples. "
                "No internal documents, database, tokens, or virtual environment are included.\n"
            ),
        )
        for file in files:
            if not file.resolve().is_relative_to(root):
                raise ValueError("archive_source_outside_workspace")
            archive.write(file, file.relative_to(root).as_posix())
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert not any(
            ".local.json" in name or ".sqlite" in name or name.startswith("data/") for name in names
        )
        print(f"Created {output.name}: {len(names)} files, {output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
