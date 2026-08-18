"""What a visitor to the public repository can actually reach.

This defect class is invisible locally: a doc links to a file that exists on
your disk but is listed in ``.gitignore``, so the link works for you forever and
404s for every visitor. ``README.md`` shipped two of them (``docs/architecture.md``
and ``SECURITY.md``) for months.

These tests read git's own index rather than the filesystem, because the
filesystem is exactly what makes the bug invisible.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import app_info

REPO_ROOT = Path(__file__).resolve().parents[2]

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

# Docs a visitor lands on, plus everything under docs/ that they can browse to.
PUBLIC_DOC_ROOTS = ("README.md", "SECURITY.md", "THIRD_PARTY_MODELS.md", "CHANGELOG.md")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Deliberately not skipped when git is unavailable: a guard that quietly
    # stops checking looks exactly like coverage.
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
    )
    return result.stdout


def _tracked_paths() -> set[str]:
    tracked = set(_git("ls-files").splitlines())
    # A file staged for addition in this working tree counts as reachable.
    tracked |= set(
        _git("diff", "--cached", "--name-only", "--diff-filter=A").splitlines()
    )
    return {path for path in tracked if path}


def _public_docs() -> list[Path]:
    docs = [REPO_ROOT / name for name in PUBLIC_DOC_ROOTS]
    docs += sorted((REPO_ROOT / "docs").glob("*.md"))
    return [doc for doc in docs if doc.is_file()]


def test_no_public_doc_links_to_a_file_git_does_not_have():
    """A link to a gitignored file resolves on the author's disk and 404s for everyone else."""
    tracked = _tracked_paths()
    directories = {path.rsplit("/", 1)[0] for path in tracked if "/" in path}

    broken: list[str] = []
    for doc in _public_docs():
        source = doc.relative_to(REPO_ROOT).as_posix()
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in MARKDOWN_LINK.finditer(text):
            href = match.group(1)
            if href.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target = (doc.parent / href.split("#", 1)[0]).resolve()
            try:
                key = target.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                broken.append(f"{source} -> {href} (escapes the repository)")
                continue
            if not target.exists():
                broken.append(f"{source} -> {href} (missing on disk)")
            elif key not in tracked and key not in directories:
                broken.append(f"{source} -> {href} (on disk but NOT tracked by git)")

    assert not broken, "public docs link to files a visitor cannot fetch:\n  " + "\n  ".join(
        broken
    )


def test_github_can_find_the_security_policy_and_model_attribution():
    """GitHub reads these paths; if they are gitignored the features silently vanish.

    ``SECURITY.md`` is what enables the Security tab's "Report a vulnerability"
    flow, and ``THIRD_PARTY_MODELS.md`` is the model licensing and attribution
    statement for weights this project downloads but never redistributes.
    """
    tracked = _tracked_paths()
    for required in ("SECURITY.md", "THIRD_PARTY_MODELS.md"):
        assert required in tracked, f"{required} is not tracked, so GitHub cannot read it"

    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security/advisories/new" in security, (
        "SECURITY.md must point at private vulnerability reporting, not a public issue"
    )
    assert app_info.GITHUB_OWNER in security, (
        "the reporting link must target the current repository owner"
    )


def test_the_license_names_the_current_owner():
    """The copyright line is legal attribution; a renamed account leaves it wrong."""
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert f"Copyright (c) 2026 {app_info.GITHUB_OWNER}" in license_text
    assert "peter119lee" not in license_text, "LICENSE still names the old account"


def test_the_repository_ships_no_unrelated_vendor_agent_boilerplate():
    """`.github/copilot-instructions.md` held four lines about Azure MCP tools.

    It was tracked and therefore public, described a cloud platform this project
    does not touch, and would be read as project guidance by any agent that
    honours the path.
    """
    assert not (REPO_ROOT / ".github" / "copilot-instructions.md").exists()
    assert ".github/copilot-instructions.md" not in _tracked_paths()
