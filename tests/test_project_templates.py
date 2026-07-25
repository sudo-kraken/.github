import tomllib

from git_automation.GitRepositoryComponent import env


def test_python_project_template_uses_configured_author() -> None:
    rendered = env.get_template("python/pyproject.toml.j2").render(
        name="example-project",
        description="Example project",
        owner="example",
        repo="project",
        author_fullname="Example Maintainer",
        author_email="maintainer@example.com",
    )
    project = tomllib.loads(rendered)["project"]

    assert project["authors"] == [{"name": "Example Maintainer", "email": "maintainer@example.com"}]
    assert {"pytest~=8.4.0", "pytest-cov~=7.0.0", "ruff~=0.14.0"} <= set(project["optional-dependencies"]["dev"])


def test_renovate_template_renders_with_strict_variables() -> None:
    rendered = env.get_template("renovatebot/renovate.json5.j2").render(
        schedule="before 9pm on friday",
        language="python",
        configs=["github-actions", "python"],
        additional_configs=[],
        repository_name="example/project",
    )

    assert "github>example/project//.github/renovate/python.json5" in rendered
    assert '"before 9pm on friday"' in rendered


def test_core_renovate_snippets_render_with_strict_variables() -> None:
    labels = env.get_template("renovatebot/config/labels.json5.j2").render(
        language="python",
        configs=["docker", "python"],
    )
    semantic_commits = env.get_template("renovatebot/config/semanticCommits.json5.j2").render(
        language="python", configs=["docker", "python"]
    )
    github_actions = env.get_template("renovatebot/config/github-actions.json5.j2").render(
        language="python", configs=["docker", "python"]
    )

    assert '"addLabels": ["renovate/python"]' in labels
    assert '"addLabels": ["renovate/docker"]' in labels
    assert "{{depName}}" in semantic_commits
    assert '"datasourceTemplate": "github-releases"' in github_actions
