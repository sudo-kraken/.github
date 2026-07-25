from __future__ import annotations

from collections.abc import Iterable

import pytest
import yaml

from git_automation.GitRepositoryComponent import GitRepositoryComponent, env

GO_PLATFORMS = [
    {"os": "linux", "arch": "amd64", "runner": "ubuntu-latest"},
    {"os": "windows", "arch": "amd64", "runner": "windows-latest"},
]
RUST_PLATFORMS = [
    {"target": "x86_64-unknown-linux-gnu", "runner": "ubuntu-latest"},
    {"target": "x86_64-pc-windows-msvc", "runner": "windows-latest"},
]
DOCKER_PLATFORMS = [
    {"os": "linux", "arch": "amd64", "runner": "ubuntu-latest"},
    {"os": "linux", "arch": "arm64", "runner": "ubuntu-24.04-arm"},
]


def _load_workflow(rendered: str) -> dict:
    workflow = yaml.safe_load(rendered)
    assert isinstance(workflow, dict)
    assert workflow.get("name")
    assert "on" in workflow or True in workflow  # PyYAML uses YAML 1.1 booleans.
    assert isinstance(workflow.get("jobs"), dict)
    assert workflow["jobs"]
    _assert_valid_job_graph(workflow["jobs"])
    _assert_non_empty_matrices(workflow["jobs"])
    return workflow


def _as_list(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _assert_valid_job_graph(jobs: dict) -> None:
    dependencies = {job_name: _as_list(job.get("needs")) for job_name, job in jobs.items()}
    for job_name, needs in dependencies.items():
        assert job_name not in needs
        assert set(needs) <= set(jobs), f"{job_name} has unknown dependencies: {needs}"

    visited: set[str] = set()
    active: set[str] = set()

    def visit(job_name: str) -> None:
        if job_name in active:
            raise AssertionError(f"Workflow dependency cycle includes {job_name}")
        if job_name in visited:
            return
        active.add(job_name)
        for dependency in dependencies[job_name]:
            visit(dependency)
        active.remove(job_name)
        visited.add(job_name)

    for job_name in jobs:
        visit(job_name)


def _assert_non_empty_matrices(jobs: dict) -> None:
    for job in jobs.values():
        matrix = job.get("strategy", {}).get("matrix")
        if not matrix:
            continue
        if "include" in matrix:
            assert matrix["include"]
        for key, values in matrix.items():
            if key != "include" and isinstance(values, list):
                assert values, f"Matrix {key} cannot be empty"


@pytest.mark.parametrize(
    ("context", "expected_jobs"),
    [
        (
            {
                "language": "python",
                "versions": ["3.11", "3.12", "3.13"],
                "lint": True,
                "test": True,
                "binary": False,
                "binary_platforms": None,
                "docker": False,
                "docker_platforms": None,
            },
            {"lint", "test"},
        ),
        (
            {
                "language": "go",
                "versions": ["1.24", "1.25"],
                "lint": True,
                "test": True,
                "binary": True,
                "binary_platforms": GO_PLATFORMS,
                "docker": False,
                "docker_platforms": None,
            },
            {"lint", "test", "build-binary"},
        ),
        (
            {
                "language": "rust",
                "versions": [],
                "lint": True,
                "test": True,
                "binary": True,
                "binary_platforms": RUST_PLATFORMS,
                "docker": False,
                "docker_platforms": None,
            },
            {"lint", "test", "build-binary"},
        ),
        (
            {
                "language": None,
                "versions": [],
                "lint": False,
                "test": False,
                "binary": False,
                "binary_platforms": None,
                "docker": True,
                "docker_platforms": DOCKER_PLATFORMS,
            },
            {"build-docker"},
        ),
    ],
)
def test_ci_workflows_render_complete_jobs(context: dict, expected_jobs: set[str]) -> None:
    rendered = env.get_template("workflow/ci.yml.j2").render(**context)
    workflow = _load_workflow(rendered)
    assert set(workflow["jobs"]) == expected_jobs


@pytest.mark.parametrize(
    ("context", "expected_jobs"),
    [
        (
            {
                "language": "python",
                "package": True,
                "documentation": False,
                "changelog": True,
                "docker": False,
                "binary_platforms": None,
                "docker_platforms": None,
            },
            {
                "changelog",
                "create-draft-release",
                "build-package",
                "publish-package",
                "publish-release",
            },
        ),
        (
            {
                "language": "python",
                "package": True,
                "documentation": False,
                "changelog": False,
                "docker": False,
                "binary_platforms": None,
                "docker_platforms": None,
            },
            {
                "create-draft-release",
                "build-package",
                "publish-package",
                "publish-release",
            },
        ),
        (
            {
                "language": "go",
                "package": True,
                "documentation": False,
                "changelog": True,
                "docker": False,
                "binary_platforms": GO_PLATFORMS,
                "docker_platforms": None,
            },
            {
                "changelog",
                "create-draft-release",
                "build-artifact",
                "sign-artifact",
                "verify-artifact",
                "publish-artifact",
                "publish-release",
            },
        ),
        (
            {
                "language": "rust",
                "package": True,
                "documentation": False,
                "changelog": True,
                "docker": False,
                "binary_platforms": RUST_PLATFORMS,
                "docker_platforms": None,
            },
            {
                "changelog",
                "create-draft-release",
                "build-artifact",
                "sign-artifact",
                "verify-artifact",
                "publish-artifact",
                "publish-release",
            },
        ),
        (
            {
                "language": "python",
                "package": False,
                "documentation": False,
                "changelog": True,
                "docker": True,
                "binary_platforms": None,
                "docker_platforms": DOCKER_PLATFORMS,
            },
            {
                "changelog",
                "create-draft-release",
                "build-docker",
                "publish-docker",
                "sign-docker",
                "verify-docker",
                "publish-release",
            },
        ),
    ],
)
def test_release_workflows_have_valid_dependency_graphs(context: dict, expected_jobs: set[str]) -> None:
    rendered = env.get_template("workflow/release.yml.j2").render(**context)
    workflow = _load_workflow(rendered)
    assert set(workflow["jobs"]) == expected_jobs
    _assert_valid_job_graph(workflow["jobs"])
    draft_script = workflow["jobs"]["create-draft-release"]["steps"][1]["run"]
    assert "gh release view" in draft_script

    if context["language"] in {"go", "rust"}:
        jobs = workflow["jobs"]
        verify_script = "\n".join(step.get("run", "") for step in jobs["verify-artifact"]["steps"])
        publish_script = "\n".join(step.get("run", "") for step in jobs["publish-artifact"]["steps"])
        assert jobs["verify-artifact"]["needs"] == ["sign-artifact"]
        assert set(jobs["publish-artifact"]["needs"]) == {
            "create-draft-release",
            "verify-artifact",
        }
        assert "releases/download" not in verify_script
        assert '--certificate "./${binary_basename}.pem"' in verify_script
        assert "--clobber" in publish_script

    if context["docker"]:
        verify_docker = workflow["jobs"]["verify-docker"]
        assert verify_docker["permissions"]["packages"] == "read"
        assert any(step.get("uses", "").startswith("docker/login-action@") for step in verify_docker["steps"])


@pytest.mark.parametrize(
    ("template_name", "context"),
    [
        ("workflow/validate-pr-title.yml.j2", {}),
        ("workflow/stale.yml.j2", {}),
        ("workflow/scorecard.yml.j2", {"default_branch_name": "trunk"}),
        ("workflow/dependency-review.yml.j2", {}),
        (
            "workflow/codeql.yml.j2",
            {"default_branch_name": "trunk", "language": "python"},
        ),
        (
            "workflow/automation-sync-pr.yml.j2",
            {
                "branch_name": "automation-sync",
                "default_branch_name": "trunk",
                "repository_name": "example/project",
            },
        ),
    ],
)
def test_standalone_workflows_are_complete(template_name: str, context: dict) -> None:
    _load_workflow(env.get_template(template_name).render(**context))


def test_rust_codeql_uses_supported_build_mode() -> None:
    rendered = env.get_template("workflow/codeql.yml.j2").render(
        default_branch_name="main",
        language="rust",
    )
    workflow = _load_workflow(rendered)

    assert {"language": "rust", "build-mode": "none"} in workflow["jobs"]["analyze"]["strategy"]["matrix"]["include"]


def test_sync_workflow_only_writes_complete_workflows() -> None:
    component = object.__new__(GitRepositoryComponent)
    component.default_branch_name = "main"
    written: dict[str, str] = {}
    component._repository_file = (  # type: ignore[method-assign]
        lambda _resource_type, path, content: written.setdefault(path, content)
    )

    component.sync_workflow(
        language="python",
        versions=["3.11", "3.12", "3.13"],
        binary=False,
        binary_platforms=None,
        lint=True,
        test=True,
        package=True,
        documentation=False,
        changelog=True,
        docker=False,
        docker_platforms=None,
    )

    assert set(written) == {
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/dependency-review.yml",
        ".github/workflows/release.yml",
        ".github/workflows/scorecard.yml",
        ".github/workflows/stale.yml",
        ".github/workflows/validate-pr-title.yml",
    }
    for rendered in written.values():
        _load_workflow(rendered)


def test_sync_workflow_rejects_empty_test_matrix() -> None:
    component = object.__new__(GitRepositoryComponent)
    component.default_branch_name = "main"

    with pytest.raises(ValueError, match="versions cannot be empty"):
        component.sync_workflow(
            language="python",
            versions=[],
            binary=False,
            binary_platforms=None,
            lint=True,
            test=True,
            package=False,
            documentation=False,
            changelog=False,
            docker=False,
            docker_platforms=None,
        )


def test_sync_workflow_rejects_unscaffolded_documentation_mode() -> None:
    component = object.__new__(GitRepositoryComponent)
    component.default_branch_name = "main"

    with pytest.raises(ValueError, match="documentation workflows are not currently generated"):
        component.sync_workflow(
            language="python",
            versions=["3.13"],
            binary=False,
            binary_platforms=None,
            lint=True,
            test=True,
            package=False,
            documentation=True,
            changelog=True,
            docker=False,
            docker_platforms=None,
        )
