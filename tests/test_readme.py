from __future__ import annotations

from types import SimpleNamespace

import pytest

from git_automation.GitRepositoryComponent import GitRepositoryComponent, env


def _component() -> GitRepositoryComponent:
    component = object.__new__(GitRepositoryComponent)
    component.owner = "example"
    component.name = "project"
    component.default_branch_name = "main"
    component.branch_name = "automation-sync"
    return component


def _readme_context() -> dict:
    return {
        "repository_name": "example/project",
        "repository_basename": "project",
        "repository_title": "Example",
        "repository_description": "Example project",
        "default_branch_name": "main",
        "documentation_url": None,
        "logo": False,
        "language": "python",
        "package_name": "example-project",
        "package": True,
        "changelog": True,
        "binary": False,
        "lint": True,
        "test": True,
        "docker": False,
        "helm": False,
        "helm_chart_name": None,
        "dev": [],
        "application": False,
        "name": "project",
        "docker_platforms": None,
        "binary_platforms": None,
        "owner": "example",
    }


def test_readme_user_content_is_not_evaluated_as_jinja() -> None:
    existing = """User expression: {{ 7 * 7 }}
<!-- template:begin:header -->
old generated content
<!-- template:end:header -->
"""

    rendered = _component()._render_managed_readme_sections(existing, _readme_context())

    assert "User expression: {{ 7 * 7 }}" in rendered
    assert "### Example" in rendered
    assert "old generated content" not in rendered


def test_canonical_readme_renders_with_strict_variables() -> None:
    rendered = env.get_template("readme/readme.md.j2").render(**_readme_context())

    assert "### Example" in rendered
    assert "pip install example-project" in rendered


@pytest.mark.parametrize(
    ("language", "quick_start"),
    [
        ("go", "go run ."),
        ("rust", "cargo run"),
    ],
)
def test_canonical_readme_uses_language_specific_quick_start(
    language: str,
    quick_start: str,
) -> None:
    context = _readme_context()
    context.update(
        {
            "language": language,
            "package": False,
            "package_name": None,
            "binary": True,
            "application": True,
        }
    )

    rendered = env.get_template("readme/readme.md.j2").render(**context)

    assert quick_start in rendered
    assert "flask --app" not in rendered
    assert "install None" not in rendered


def test_documentation_link_does_not_hide_usage_or_development() -> None:
    context = _readme_context()
    context["documentation_url"] = "https://docs.example.com"

    rendered = env.get_template("readme/readme.md.j2").render(**context)

    assert "- [Documentation](#documentation)" in rendered
    assert "See https://docs.example.com." in rendered
    assert "## Usage" in rendered
    assert "## Configuration" in rendered
    assert "## Development" in rendered


def test_readme_rejects_unknown_managed_section() -> None:
    existing = """<!-- template:begin:unknown -->
content
<!-- template:end:unknown -->
"""

    with pytest.raises(ValueError, match="Unknown managed README section"):
        _component()._render_managed_readme_sections(existing, _readme_context())


def test_readme_404_is_the_only_missing_file_response(monkeypatch) -> None:
    component = _component()
    monkeypatch.setattr(
        "git_automation.GitRepositoryComponent.requests.get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=404, text=""),
    )

    assert component._safe_get_readme() is None


def test_readme_auth_failure_aborts_instead_of_overwriting(monkeypatch) -> None:
    component = _component()
    monkeypatch.setattr(
        "git_automation.GitRepositoryComponent.requests.get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=403, text=""),
    )
    monkeypatch.setattr("git_automation.GitRepositoryComponent.time.sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="Unable to safely fetch"):
        component._safe_get_readme()


def test_readme_falls_back_from_working_branch_to_default_branch(monkeypatch) -> None:
    component = _component()
    requested_refs = []

    def get_readme(*_args, **kwargs):
        requested_refs.append(kwargs["params"]["ref"])
        if kwargs["params"]["ref"] == "automation-sync":
            return SimpleNamespace(status_code=404, text="")
        return SimpleNamespace(status_code=200, text="# Existing README")

    monkeypatch.setattr(
        "git_automation.GitRepositoryComponent.requests.get",
        get_readme,
    )

    assert component._safe_get_readme() == "# Existing README"
    assert requested_refs == ["automation-sync", "main"]
