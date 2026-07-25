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
        {"os": "darwin", "arch": "amd64", "runner": "macos-15-intel"},
        {"os": "darwin", "arch": "arm64", "runner": "macos-latest"},
        {"os": "windows", "arch": "amd64", "runner": "windows-latest"},
    ],
    "rust": [
        {"target": "x86_64-unknown-linux-gnu", "runner": "ubuntu-latest"},
        {"target": "x86_64-unknown-linux-musl", "runner": "ubuntu-latest"},
        {"target": "aarch64-unknown-linux-gnu", "runner": "ubuntu-24.04-arm"},
        {"target": "aarch64-unknown-linux-musl", "runner": "ubuntu-24.04-arm"},
        {"target": "x86_64-apple-darwin", "runner": "macos-15-intel"},
        {"target": "aarch64-apple-darwin", "runner": "macos-latest"},
        {"target": "x86_64-pc-windows-msvc", "runner": "windows-latest"},
        {"target": "x86_64-pc-windows-gnu", "runner": "windows-latest"},
    ],
}


def _validate_platforms(
    repository_name: str,
    setting_name: str,
    platforms: object,
    required_fields: set[str],
) -> list[dict[str, str]]:
    """Validate build matrix entries before rendering strict templates."""
    if not isinstance(platforms, list) or not platforms:
        raise ValueError(f"{setting_name} for {repository_name} must be a non-empty list")

    for index, platform in enumerate(platforms):
        if not isinstance(platform, dict):
            raise ValueError(f"{setting_name}[{index}] for {repository_name} must be a mapping")
        missing_fields = required_fields - set(platform)
        if missing_fields:
            raise ValueError(
                f"{setting_name}[{index}] for {repository_name} is missing: {', '.join(sorted(missing_fields))}"
            )
        if any(not isinstance(platform[field], str) or not platform[field].strip() for field in required_fields):
            raise ValueError(f"{setting_name}[{index}] for {repository_name} must use non-empty strings")

    return platforms


config = pulumi.Config()

author = config.get_object("author")
owner = pulumi.Config("github").require("owner")
global_renovate = config.get_object("renovatebot") or {}
if not isinstance(global_renovate, dict):
    raise ValueError("renovatebot must be a mapping")
protect_repositories = config.get_bool("protect_repositories", True)
allow_empty_repositories = config.get_bool("allow_empty_repositories", False)
default_branch_name = config.get("default_branch_name", "main")
branch_name = config.require("branch_name")

if not isinstance(author, dict) or not {"email", "fullname"} <= set(author):
    raise ValueError("author must define email and fullname")
if not all(isinstance(author[field], str) and author[field].strip() for field in ("email", "fullname")):
    raise ValueError("author email and fullname must be non-empty strings")
if not branch_name.strip() or not default_branch_name.strip():
    raise ValueError("branch_name and default_branch_name cannot be empty")
if branch_name == default_branch_name:
    raise ValueError("branch_name must differ from default_branch_name")

repository_configs = config.require_object("repositories")
if not isinstance(repository_configs, list):
    raise ValueError("repositories must be a list")
if not repository_configs and not allow_empty_repositories:
    raise ValueError("repositories cannot be empty unless allow_empty_repositories is explicitly true")
if any(not isinstance(repository, dict) for repository in repository_configs):
    raise ValueError("Every repository entry must be a mapping")

repository_names = [repository.get("name") for repository in repository_configs]
if any(not isinstance(name, str) or not name.strip() for name in repository_names):
    raise ValueError("Every repository must define a non-empty name")
if len(repository_names) != len(set(repository_names)):
    raise ValueError("Repository names must be unique")

for repository_config in repository_configs:
    missing_fields = {"description", "name", "title"} - set(repository_config)
    if missing_fields:
        raise ValueError(
            f"Repository {repository_config.get('name', '<unknown>')} is missing: {', '.join(sorted(missing_fields))}"
        )
    if not all(isinstance(repository_config[field], str) for field in ("description", "title")):
        raise ValueError(f"description and title for {repository_config['name']} must be strings")

    workflow_config = repository_config.get("workflow")
    if workflow_config is not None and not isinstance(workflow_config, dict):
        raise ValueError(f"workflow for {repository_config['name']} must be a mapping")
    workflow = workflow_config is not None
    workflow_config = workflow_config or {}
    for setting in ("lint", "test", "package", "documentation", "changelog"):
        if setting in workflow_config and not isinstance(workflow_config[setting], bool):
            raise ValueError(f"workflow.{setting} for {repository_config['name']} must be a boolean")
    workflow_lint = workflow and workflow_config.get("lint", True)
    workflow_test = workflow and workflow_config.get("test", True)
    workflow_changelog = workflow and workflow_config.get("changelog", True)
    workflow_documentation = workflow and workflow_config.get("documentation", False)

    repo_renovate = repository_config.get("renovatebot", {})
    if not isinstance(repo_renovate, dict):
        raise ValueError(f"renovatebot for {repository_config['name']} must be a mapping")
    renovate_enabled = bool(global_renovate or repo_renovate)

    package_setting = repository_config.get("package", False)
    if not isinstance(package_setting, (bool, str)):
        raise ValueError(f"package for {repository_config['name']} must be a boolean or string")
    package = bool(package_setting)
    package_name = repository_config.get("package_name")
    if isinstance(package_setting, str):
        package_name = package_name or package_setting
    elif package:
        package_name = package_name or repository_config["name"]
    if package_name is not None and (not isinstance(package_name, str) or not package_name.strip()):
        raise ValueError(f"package_name for {repository_config['name']} must be a non-empty string")
    workflow_package = workflow and package and workflow_config.get("package", True)
    devcontainer = repository_config.get("devcontainer", False)
    helm_chart_name = repository_config.get("helm_chart_name")
    helm = helm_chart_name is not None
    docker = repository_config.get("docker", False)
    language = repository_config.get("language")
    versions = repository_config.get("versions", [])
    gitignore = repository_config.get("gitignore", False)
    application = repository_config.get("application", False)
    repository_protect = repository_config.get("protect", protect_repositories)
    if not isinstance(repository_protect, bool):
        raise ValueError(f"protect for {repository_config['name']} must be a boolean")

    binary = bool(repository_config.get("binary", language in {"go", "rust"}))
    if workflow_package and language in {"go", "rust"} and not binary:
        raise ValueError(f"package workflows for {repository_config['name']} require binary builds")
    binary_platforms = repository_config.get("binary_platforms")
    if binary:
        if language not in {"go", "rust"}:
            raise ValueError(f"binary builds for {repository_config['name']} require language 'go' or 'rust'")
        binary_platforms = _validate_platforms(
            repository_config["name"],
            "binary_platforms",
            binary_platforms or _BUILD_PLATFORMS[language],
            {"os", "arch", "runner"} if language == "go" else {"target", "runner"},
        )
    docker_platforms = repository_config.get("docker_platforms")
    if docker:
        docker_platforms = _validate_platforms(
            repository_config["name"],
            "docker_platforms",
            docker_platforms or _BUILD_PLATFORMS["docker"],
            {"os", "arch", "runner"},
        )

    repository = GitRepositoryComponent(
        owner=owner,
        name=repository_config["name"],
        default_branch_name=default_branch_name,
        branch_name=branch_name,
        description=repository_config["description"],
        author_fullname=author["fullname"],
        author_email=author["email"],
        homepage_url=repository_config.get("homepage_url", None),
        topics=repository_config.get("topics", None),
        pages=repository_config.get("pages", None),
        protect=repository_protect,
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
        configs = list(
            dict.fromkeys(
                [
                    *global_renovate.get("configs", []),
                    *repo_renovate.get("configs", []),
                ]
            )
        )
        additional_configs = list(
            dict.fromkeys(
                [
                    *global_renovate.get("additional_configs", []),
                    *repo_renovate.get("additional_configs", []),
                ]
            )
        )
        if devcontainer and "devcontainers" not in configs:
            configs.append("devcontainers")
        if helm and "helm" not in configs:
            configs.append("helm")
        if docker and "docker" not in configs:
            configs.append("docker")
        if language and language not in configs:
            configs.append(language)

        repository.sync_renovatebot(
            repo_renovate.get("schedule")
            or (global_renovate.get("schedule") if isinstance(global_renovate, dict) else None),
            language,
            configs,
            additional_configs,
        )

    if "logo" in repository_config and bool(repository_config["logo"]):
        repository.sync_logo(repository_config["logo"])

    # Per-language scaffolding
    if language == "python":
        if repository_config.get("scaffold", False):
            repository.sync_pyproject(package_name or repository_config["name"], repository_config["description"])
        else:
            repository.preserve_repository_file("pyproject.toml")
    if workflow and workflow_changelog:
        repository.sync_git_cliff()

    # README
    dev = []
    if devcontainer:
        dev.append("devcontainer")

    if repository_config.get("readme", True):
        repository.sync_readme(
            repository_config["title"],
            repository_config["description"],
            repository_config.get("documentation_url"),
            "logo" in repository_config and repository_config["logo"],
            language,
            package_name,
            package,
            workflow_changelog,
            binary and workflow_package,
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
    else:
        repository.preserve_repository_file("README.md")

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
