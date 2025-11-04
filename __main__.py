"""
Pulumi program for managing GitHub organization repositories and automation.

This module orchestrates repository creation, configuration synchronization,
and workflow generation using declarative Pulumi stack configurations.
"""

import pulumi

from git_automation.GitRepositoryComponent import GitRepositoryComponent

_BUILD_PLATFORMS = {
    "docker": [
        {"os": "linux", "arch": "amd64", "runner": "ubuntu-latest"},
        {"os": "linux", "arch": "arm64", "runner": "ubuntu-24.04-arm"},
    ],
    "go": [
        {"os": "linux", "arch": "amd64", "runner": "ubuntu-latest"},
        {"os": "linux", "arch": "arm64", "runner": "ubuntu-24.04-arm"},
        {"os": "darwin", "arch": "amd64", "runner": "macos-13"},
        {"os": "darwin", "arch": "arm64", "runner": "macos-latest"},
        {"os": "windows", "arch": "amd64", "runner": "windows-latest"},
    ],
    "rust": [
        {"target": "x86_64-unknown-linux-gnu", "runner": "ubuntu-latest"},
        {"target": "x86_64-unknown-linux-musl", "runner": "ubuntu-latest"},
        {"target": "aarch64-unknown-linux-gnu", "runner": "ubuntu-24.04-arm"},
        {"target": "aarch64-unknown-linux-musl", "runner": "ubuntu-24.04-arm"},
        {"target": "x86_64-apple-darwin", "runner": "macos-13"},
        {"target": "aarch64-apple-darwin", "runner": "macos-latest"},
        {"target": "x86_64-pc-windows-msvc", "runner": "windows-latest"},
        {"target": "x86_64-pc-windows-gnu", "runner": "windows-latest"},
    ],
}

config = pulumi.Config()

author = config.get_object("author")
owner = pulumi.Config("github").require("owner")
global_renovate = config.get_object("renovatebot") or {}

if author is None:
    raise ValueError("Author cannot be None")

for repository_config in config.get_object("repositories", []):
    workflow = "workflow" in repository_config
    workflow_lint = (not workflow) or repository_config["workflow"].get("lint", True)
    workflow_test = (not workflow) or repository_config["workflow"].get("test", True)
    workflow_package = (not workflow) or repository_config["workflow"].get(
        "package", True
    )
    workflow_changelog = (not workflow) or repository_config["workflow"].get(
        "changelog", True
    )
    workflow_documentation = repository_config.get("workflow", {}).get(
        "documentation", False
    )

    repo_renovate = repository_config.get("renovatebot", {})
    renovate_enabled = bool(global_renovate or repo_renovate)

    package = bool(repository_config.get("package", False))
    package_name = repository_config.get("package_name", None)
    devcontainer = repository_config.get("devcontainer", False)
    helm_chart_name = repository_config.get("helm_chart_name", None)
    helm = helm_chart_name is not None
    docker = repository_config.get("docker", False)
    language = repository_config.get("language", None)
    versions = repository_config.get("versions", [])
    gitignore = repository_config.get("gitignore", False)
    application = repository_config.get("application", False)

    binary = language in ["go", "rust"]
    binary_platforms = _BUILD_PLATFORMS.get(language, None)
    docker_platforms = repository_config.get("docker_platforms", None)
    if docker and not docker_platforms:
        docker_platforms = _BUILD_PLATFORMS["docker"]

    repository = GitRepositoryComponent(
        owner=owner,
        name=repository_config["name"],
        default_branch_name=config.get("default_branch_name", "main"),
        branch_name=config.get("branch_name"),
        description=repository_config["description"],
        author_fullname=author["fullname"],
        author_email=author["email"],
        homepage_url=repository_config.get("homepage_url", None),
        topics=repository_config.get("topics", None),
        pages=repository_config.get("pages", None),
    )

    # Base files
    if "license" in repository_config and repository_config["license"]:
        repository.sync_license(repository_config["license"])

    funding = config.get_object("funding")
    if funding:
        repository.sync_funding(funding)

    repository.sync_pull_request_template()
    repository.sync_contributing()
    repository.sync_support()
    repository.sync_issue_template(language)
    repository.sync_codeowner()
    repository.sync_vscode_config(language)
    repository.sync_editor_config(language)
    repository.sync_gitattributes()

    if gitignore:
        repository.sync_gitignore(language, helm)

    contact_email = config.get("contact_email")
    if contact_email:
        repository.sync_code_of_conduct(contact_email)

    security_email = config.get("security_email")
    if security_email:
        repository.sync_security(security_email)

    # Labels only when explicitly enabled in repo config
    if "label" in repository_config and repository_config["label"]:
        repository.sync_label(language, docker, renovate_enabled)

    # Renovate
    if renovate_enabled:
        configs = list(repo_renovate.get("configs", []))
        if devcontainer and "devcontainers" not in configs:
            configs.append("devcontainers")
        if helm and "helm" not in configs:
            configs.append("helm")
        if docker and "docker" not in configs:
            configs.append("docker")
        if language and language not in configs:
            configs.append(language)

        repository.sync_renovatebot(
            (
                global_renovate.get("schedule")
                if isinstance(global_renovate, dict)
                else None
            )
            or repo_renovate.get("schedule"),
            language,
            configs,
            repo_renovate.get("additional_configs", []),
        )

    if "logo" in repository_config and bool(repository_config["logo"]):
        repository.sync_logo(repository_config["logo"])

    # Per-language scaffolding
    if language == "python":
        repository.sync_pyproject(
            package_name or repository_config["name"], repository_config["description"]
        )
        repository.sync_git_cliff()  # used by changelog flow

    # README
    dev = []
    if devcontainer:
        dev.append("devcontainer")

    repository.sync_readme(
        repository_config["title"],
        repository_config["description"],
        repository_config.get("documentation_url", None),
        "logo" in repository_config and repository_config["logo"],
        language,
        package_name,
        package,
        workflow_changelog,
        binary,
        workflow_lint,
        workflow_test,
        docker,
        helm,
        helm_chart_name,
        binary_platforms,
        docker_platforms,
        dev,
        application,
    )

    # Workflows
    if workflow:
        repository.sync_workflow(
            language,
            versions,
            binary,
            binary_platforms,
            workflow_lint,
            workflow_test,
            workflow_package,
            workflow_documentation,
            workflow_changelog,
            docker,
            docker_platforms,
        )
