from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_example_stack_uses_namespaced_keys_and_safe_defaults() -> None:
    stack = yaml.safe_load((ROOT / "Pulumi.stack.yaml.example").read_text())
    config = stack["config"]

    assert all(":" in key for key in config)
    assert config[".github:branch_name"] != config[".github:default_branch_name"]
    assert config[".github:protect_repositories"] is True

    repositories = config[".github:repositories"]
    assert repositories
    assert len({repository["name"] for repository in repositories}) == len(repositories)

    python_repository = next(repository for repository in repositories if repository["language"] == "python")
    assert all(tuple(map(int, version.split("."))) >= (3, 11) for version in python_repository["versions"])


def test_repository_ci_has_dedicated_lint_and_test_jobs() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())

    assert set(workflow["jobs"]) == {"lint", "test"}
    assert workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"] == [
        "3.11",
        "3.12",
        "3.13",
    ]


def test_configured_docker_platforms_include_runners() -> None:
    stack = yaml.safe_load((ROOT / "Pulumi.dev.yaml").read_text())
    repositories = stack["config"][".github:repositories"]
    docker_repository = next(repository for repository in repositories if repository.get("docker"))

    assert all({"os", "arch", "runner"} <= set(platform) for platform in docker_repository["docker_platforms"])
