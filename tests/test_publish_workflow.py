"""Release publication remains exact-tag and environment approval gated."""

from pathlib import Path


def test_pypi_publication_requires_exact_tag_and_environment() -> None:
    workflow = Path(".github/workflows/publish.yml").read_text(encoding="utf-8")

    assert 'test "$GITHUB_REF" = "refs/tags/v$VERSION"' in workflow
    assert "scripts/check_release_metadata.py --publication" in workflow
    assert "name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow
    assert "environment:" in workflow.split("Publish to PyPI", maxsplit=1)[1]
