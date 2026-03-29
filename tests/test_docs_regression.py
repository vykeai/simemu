import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_authoritative_simemu_docs_do_not_reference_legacy_commands() -> None:
    targets = [
        REPO_ROOT / "docs" / "adding-to-project.md",
        REPO_ROOT / "simemu" / "swift" / "Sources" / "SimEmuBar" / "SimEmuApp.swift",
    ]
    forbidden = [
        "simemu acquire",
        "simemu list ios",
        "simemu list android",
    ]

    for path in targets:
        content = path.read_text()
        for needle in forbidden:
            assert needle not in content, f"{needle!r} still present in {path}"


def test_local_product_docs_do_not_reference_legacy_simemu_usage() -> None:
    if os.environ.get("SIMEMU_CHECK_LOCAL_PRODUCT_DOCS") != "1":
        return

    targets = [
        Path("/Users/luke/dev/products/goala/AUTONOMOUS_EXECUTION.md"),
        Path("/Users/luke/dev/products/fitkind/AUTONOMOUS_EXECUTION.md"),
        Path("/Users/luke/dev/products/sitches/AUTONOMOUS_EXECUTION.md"),
        Path("/Users/luke/dev/products/univiirse/AUTONOMOUS_EXECUTION.md"),
        Path("/Users/luke/dev/products/goala/views/architecture.md"),
        Path("/Users/luke/dev/products/goala/keel/architecture/cloudy-execution-profile.md"),
    ]
    forbidden = [
        "simemu list",
        "simemu acquire",
        "Use `/tmp` for final proof screenshots",
    ]

    for path in targets:
        if not path.exists():
            continue
        content = path.read_text()
        for needle in forbidden:
            assert needle not in content, f"{needle!r} still present in {path}"
