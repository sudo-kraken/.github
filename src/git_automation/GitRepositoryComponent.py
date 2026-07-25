"""
GitHub repository component for Pulumi infrastructure management.

Provides a component resource that manages GitHub repositories with
automated file synchronization, workflow generation, and configuration
management through Jinja2 templates.
"""

import os
import re
import time
from importlib import resources
from typing import Any, Awaitable, Mapping

import pulumi
import pulumi_github as github
import requests
from jinja2 import Environment, PackageLoader, StrictUndefined, TemplateNotFound
from pulumi.output import Output

PACKAGE_NAME = __name__.split(".")[0]

# API Configuration
GITHUB_API_TIMEOUT = 10
README_FETCH_RETRIES = 3
RETRY_BACKOFF_BASE = 1

env = Environment(
    loader=PackageLoader(PACKAGE_NAME, "templates"),
    keep_trailing_newline=True,
    extensions=["jinja2.ext.do"],
    undefined=StrictUndefined,
)


def _get_template(*path_parts: str):
    """Load a package template using Jinja's platform-independent path syntax."""
    return env.get_template("/".join(path_parts))


class GitRepositoryComponent(pulumi.ComponentResource):
    def __init__(
        self,
        owner: str,
        name: str,
        default_branch_name: str,
        description: str,
        author_fullname: str,
        author_email: str,
        branch_name: str | None = None,
        homepage_url: str | None = None,
        topics: list[str] | None = None,
        pages: dict[str, str] | None = None,
        props: Mapping[str, Any | Awaitable[Any] | Output[Any]] | None = None,
        opts: pulumi.ResourceOptions | None = None,
        protect: bool = True,
    ) -> None:
        """Repository component used to manage GitHub repository."""
        self.owner = owner
        self.name = name
        self.default_branch_name = default_branch_name
        self.author_fullname = author_fullname
        self.author_email = author_email
        self.branch_name = branch_name

        component_options = pulumi.ResourceOptions.merge(opts, pulumi.ResourceOptions(protect=protect))
        super().__init__("pkg:index:GitRepositoryComponent", name, props, component_options)

        # Pages (optional)
        if pages:
            gh_pages = github.RepositoryPagesArgs(
                source=github.RepositoryPagesSourceArgs(
                    branch=pages.get("branch", "gh-pages"),
                    path=pages.get("path", "/"),
                ),
                build_type=pages.get("build_type", "legacy"),
                cname=pages.get("cname"),
            )
        else:
            gh_pages = None

        # Repository
        self.repository = github.Repository(
            name,
            auto_init=True,
            allow_auto_merge=True,
            allow_merge_commit=False,
            allow_rebase_merge=True,
            allow_squash_merge=True,
            allow_update_branch=True,
            delete_branch_on_merge=True,
            description=description,
            has_discussions=False,
            has_issues=True,
            has_projects=False,
            has_wiki=False,
            homepage_url=homepage_url,
            is_template=False,
            name=name,
            pages=gh_pages,
            security_and_analysis=github.RepositorySecurityAndAnalysisArgs(
                secret_scanning=github.RepositorySecurityAndAnalysisSecretScanningArgs(status="enabled"),
                secret_scanning_push_protection=github.RepositorySecurityAndAnalysisSecretScanningPushProtectionArgs(
                    status="enabled"
                ),
            ),
            topics=topics,
            visibility="public",
            vulnerability_alerts=True,
            archive_on_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Branches + default
        self.branch = github.Branch(
            f"{name}-branch",
            repository=self.repository.name,
            branch=self.default_branch_name,
            source_branch="main",
            opts=pulumi.ResourceOptions(
                depends_on=[self.repository],
                deleted_with=self.repository,
                parent=self,
            ),
        )

        self.default_branch = github.BranchDefault(
            f"{name}-default-branch",
            repository=self.repository.name,
            branch=self.default_branch_name,
            opts=pulumi.ResourceOptions(
                depends_on=[self.branch],
                deleted_with=self.repository,
                parent=self,
            ),
        )

        # Working branch (PR mode) or default branch
        if self.is_pr_mode():
            self.working_branch = github.Branch(
                f"{name}-working-branch",
                repository=self.repository.name,
                branch=self.branch_name,
                source_branch=self.default_branch_name,
                opts=pulumi.ResourceOptions(
                    depends_on=[self.default_branch],
                    deleted_with=self.repository,
                    parent=self,
                ),
            )

        else:
            self.working_branch = self.branch

        # File operations should wait for repo/branch creation
        self._file_depends_on = [self.repository, self.branch, self.default_branch]
        if self.is_pr_mode():
            self._file_depends_on.append(self.working_branch)

        # Branch protection
        if self._detect_owner_is_org(self.owner):
            self.branch_protection = github.BranchProtectionV3(
                f"{name}-branch-protection",
                repository=self.repository.name,
                branch=self.default_branch_name,
                enforce_admins=True,
                require_conversation_resolution=True,
                require_signed_commits=True,
                required_pull_request_reviews=github.BranchProtectionV3RequiredPullRequestReviewsArgs(
                    dismiss_stale_reviews=True,
                    required_approving_review_count=1,
                ),
                required_status_checks=github.BranchProtectionV3RequiredStatusChecksArgs(
                    strict=True,
                    checks=[],
                ),
                restrictions=None,
                opts=pulumi.ResourceOptions(
                    depends_on=[self.default_branch],
                    deleted_with=self.repository,
                    parent=self,
                ),
            )
        else:
            # Classic BranchProtection
            self.branch_protection = github.BranchProtection(
                f"{name}-branch-protection",
                repository_id=self.repository.node_id,
                pattern=self.default_branch_name,
                enforce_admins=True,
                allows_deletions=False,
                allows_force_pushes=False,
                lock_branch=False,
                required_linear_history=False,
                require_conversation_resolution=True,
                required_status_checks=[
                    github.BranchProtectionRequiredStatusCheckArgs(
                        strict=True,
                        contexts=[],
                    )
                ],
                required_pull_request_reviews=[
                    github.BranchProtectionRequiredPullRequestReviewArgs(
                        dismiss_stale_reviews=True,
                        required_approving_review_count=1,
                        restrict_dismissals=False,
                    )
                ],
                opts=pulumi.ResourceOptions(
                    depends_on=[self.default_branch],
                    deleted_with=self.repository,
                    parent=self,
                ),
            )

        self.register_outputs(
            {
                "repository": self.repository.name,
                "working_branch": self.working_branch.branch,
            }
        )

    # ---------- helpers ----------

    def _detect_owner_is_org(self, owner: str) -> bool:
        """Return True if the owner is an organisation, else False."""
        try:
            headers = {"Accept": "application/vnd.github+json"}
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            response = requests.get(
                f"https://api.github.com/users/{owner}",
                headers=headers,
                timeout=GITHUB_API_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("type") == "Organization"
            elif response.status_code == 404:
                pulumi.log.warn(f"Owner '{owner}' not found on GitHub")
            elif response.status_code == 401:
                pulumi.log.warn("GitHub authentication failed - check GITHUB_TOKEN")
            else:
                pulumi.log.warn(f"GitHub API returned status {response.status_code} for owner '{owner}'")
        except requests.exceptions.Timeout:
            pulumi.log.warn(f"Timeout while checking if '{owner}' is an organization")
        except requests.exceptions.RequestException as e:
            pulumi.log.warn(f"Network error while checking owner type: {e}")
        except (KeyError, ValueError) as e:
            pulumi.log.warn(f"Error parsing GitHub API response: {e}")
        return False

    def get_working_branch(self) -> github.Branch:
        return self.working_branch

    def _render_managed_readme_sections(self, readme_contents: str, context: dict[str, Any]) -> str:
        """Render marked sections while treating all repository text as opaque."""
        pattern = re.compile(
            r"<!-- template:begin:(?P<name>.*?) -->.*?"
            r"<!-- template:end:(?P=name) -->",
            re.DOTALL,
        )
        aliases = {"usage/cargo": "usage/rust"}

        def render_section(match: re.Match[str]) -> str:
            section_name = match.group("name")
            if not re.fullmatch(r"[a-z0-9_/-]+", section_name):
                raise ValueError(f"Invalid managed README section: {section_name}")

            template_name = aliases.get(section_name, section_name)
            try:
                template = _get_template("readme", "sections", f"{template_name}.md.j2")
            except TemplateNotFound as error:
                raise ValueError(f"Unknown managed README section: {section_name}") from error
            return template.render(**context)

        return pattern.sub(render_section, readme_contents)

    def _safe_get_repository_file(self, file_path: str) -> str | None:
        """Fetch a file, returning None only when GitHub reports it missing."""
        url = f"https://api.github.com/repos/{self.owner}/{self.name}/contents/{file_path}"
        headers = {"Accept": "application/vnd.github.raw+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        working_ref = self.branch_name if self.is_pr_mode() else self.default_branch_name
        refs = list(dict.fromkeys([working_ref, self.default_branch_name]))

        for ref in refs:
            last_error: Exception | None = None
            missing = False
            for attempt in range(README_FETCH_RETRIES):
                try:
                    response = requests.get(
                        url,
                        headers=headers,
                        params={"ref": ref},
                        timeout=GITHUB_API_TIMEOUT,
                    )
                    if response.status_code == 200:
                        return response.text
                    if response.status_code == 404:
                        pulumi.log.info(f"{file_path} not found for {self.owner}/{self.name} at {ref}")
                        missing = True
                        break

                    message = (
                        f"GitHub API returned status {response.status_code} while "
                        f"fetching {file_path} for {self.owner}/{self.name} at {ref}"
                    )
                    last_error = RuntimeError(message)
                    pulumi.log.warn(f"{message} (attempt {attempt + 1}/{README_FETCH_RETRIES})")
                except requests.exceptions.RequestException as error:
                    last_error = error
                    pulumi.log.warn(
                        f"Network error fetching {file_path} at {ref} "
                        f"(attempt {attempt + 1}/{README_FETCH_RETRIES}): {error}"
                    )

                if attempt < README_FETCH_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_BASE + attempt)

            if missing:
                continue
            raise RuntimeError(
                f"Unable to safely fetch {file_path} for {self.owner}/{self.name} at {ref}"
            ) from last_error

        return None

    def _safe_get_readme(self) -> str | None:
        """Fetch README.md from the working branch, then the default branch."""
        return self._safe_get_repository_file("README.md")

    def is_pr_mode(self) -> bool:
        return bool(self.branch_name and self.branch_name != self.default_branch_name)

    def _repository_file(
        self,
        resource_name_type: str,
        file: str,
        content: str,
        *,
        retain_on_delete: bool = False,
    ) -> github.RepositoryFile:
        return github.RepositoryFile(
            f"{self.name}-{file}",
            repository=self.name,
            branch=self.get_working_branch().branch,
            file=file,
            content=content,
            commit_message=f"""\
chore(automation): sync {resource_name_type}

Automated synchronization from infrastructure repository.
Source: https://github.com/{self.owner}/.github

Signed-off-by: {self.author_fullname} <{self.author_email}>""",
            commit_author=self.author_fullname,
            commit_email=self.author_email,
            overwrite_on_create=True,
            opts=pulumi.ResourceOptions(
                depends_on=self._file_depends_on,
                deleted_with=self.repository,
                parent=self,
                retain_on_delete=retain_on_delete,
            ),
        )

    def preserve_repository_file(self, file_path: str) -> None:
        """Keep an existing file unchanged while safely transitioning ownership."""
        existing = self._safe_get_repository_file(file_path)
        if existing is not None:
            self._repository_file(
                file_path,
                file_path,
                existing,
                retain_on_delete=True,
            )

    # ---------- sync: top-level files ----------

    def sync_license(self, license_name: str):
        license_dir = resources.files(PACKAGE_NAME).joinpath("license", license_name)
        for license_file in license_dir.iterdir():
            with license_file.open() as file:
                license_content = file.read()
            self._repository_file("license", license_file.name, license_content)

    def sync_funding(self, fundings: list[str]):
        template = _get_template("misc", "FUNDING.yml.j2")
        self._repository_file(
            ".github/FUNDING.yml",
            ".github/FUNDING.yml",
            template.render(fundings=fundings),
        )

    def sync_pull_request_template(self):
        template = _get_template("misc", "PULL_REQUEST_TEMPLATE.md.j2")
        self._repository_file(
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            template.render(repository_name=self.name),
        )

    def sync_issue_template(self, language: str):
        issue_config_dir = resources.files(PACKAGE_NAME).joinpath("templates", "issue")
        for issue_file in issue_config_dir.iterdir():
            with issue_file.open() as file:
                issue_content = file.read()
            out_file = f".github/ISSUE_TEMPLATE/{issue_file.name[:-3]}"
            if language in {"python", "go"} and out_file.endswith(".yml"):
                out_file = out_file.replace(".yml", ".yaml")
            self._repository_file(
                out_file,
                out_file,
                env.from_string(issue_content).render(assignees=[self.owner], language=language),
            )

    def sync_code_of_conduct(self, contact_email: str):
        template = _get_template("misc", "CODE_OF_CONDUCT.md.j2")
        self._repository_file("CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.md", template.render(contact_email=contact_email))

    def sync_contributing(self):
        with resources.files(PACKAGE_NAME).joinpath("misc", "CONTRIBUTING.md").open() as f:
            self._repository_file("CONTRIBUTING.md", "CONTRIBUTING.md", f.read())

    def sync_editor_config(self, language: str):
        template = _get_template("misc", "editorconfig.j2")
        self._repository_file(".editorconfig", ".editorconfig", template.render(language=language))

    def sync_gitignore(self, language: str, helm: bool):
        template = _get_template("misc", "gitignore.j2")
        self._repository_file(".gitignore", ".gitignore", template.render(language=language, helm=helm))

    def sync_gitattributes(self):
        with resources.files(PACKAGE_NAME).joinpath("misc", "gitattributes").open() as f:
            self._repository_file(".gitattributes", ".gitattributes", f.read())

    def sync_codeowner(self, owner: str | None = None):
        template = _get_template("misc", "CODEOWNERS.j2")
        self._repository_file(".github/CODEOWNERS", ".github/CODEOWNERS", template.render(owner=owner or self.owner))

    def sync_label(self, language: str, docker: bool, renovatebot: bool):
        template = _get_template("misc", "labels.yml.j2")
        self._repository_file(
            ".github/labels.yml",
            ".github/labels.yml",
            template.render(language=language, docker=docker, renovatebot=renovatebot),
        )

    def sync_security(self, security_email: str):
        template = _get_template("misc", "SECURITY.md.j2")
        self._repository_file("SECURITY.md", "SECURITY.md", template.render(security_email=security_email))

    def sync_support(self):
        with resources.files(PACKAGE_NAME).joinpath("misc", "SUPPORT.md").open() as f:
            self._repository_file("SUPPORT.md", "SUPPORT.md", f.read())

    def sync_logo(self, logo: str):
        with resources.files(PACKAGE_NAME).joinpath("logo", logo).open() as f:
            self._repository_file("docs/assets/logo.svg", "docs/assets/logo.svg", f.read())

    def sync_git_cliff(self):
        """Copy git-cliff configuration to .github/cliff.toml."""
        with resources.files(PACKAGE_NAME).joinpath("git-cliff", "cliff.toml").open() as f:
            self._repository_file(".github/cliff.toml", ".github/cliff.toml", f.read())

    def sync_pyproject(self, package_name: str | None, description: str):
        template = _get_template("python", "pyproject.toml.j2")
        content = template.render(
            name=package_name or self.name,
            description=description,
            owner=self.owner,
            repo=self.name,
            author_fullname=self.author_fullname,
            author_email=self.author_email,
        )
        self._repository_file(
            "pyproject.toml",
            "pyproject.toml",
            content,
            retain_on_delete=True,
        )

    def sync_vscode_config(self, language: str):
        template = _get_template("vscode", "launch.json.j2")
        self._repository_file(".vscode/launch.json", ".vscode/launch.json", template.render(language=language))

    # ---------- README and workflows ----------

    def sync_readme(
        self,
        repository_title: str,
        repository_description: str,
        documentation_url: str | None,
        logo: bool,
        language: str,
        package_name: str | None,
        package: bool,
        changelog: bool,
        binary: bool,
        lint: bool,
        test: bool,
        docker: bool,
        helm: bool,
        helm_chart_name: str | None,
        binary_platforms: list[dict[str, str]] | None,
        docker_platforms: list[dict[str, str]] | None,
        dev: list[str] | None,
        application: bool,
    ):
        existing = self._safe_get_readme()
        has_markers = bool(existing and "<!-- template:begin:" in existing)
        context = {
            "documentation_url": documentation_url,
            "repository_name": f"{self.owner}/{self.name}",
            "repository_basename": self.name,
            "default_branch_name": self.default_branch_name,
            "repository_title": repository_title,
            "repository_description": repository_description,
            "logo": logo,
            "language": language,
            "package_name": package_name,
            "package": package,
            "changelog": changelog,
            "binary": binary,
            "lint": lint,
            "test": test,
            "docker": docker,
            "helm": helm,
            "helm_chart_name": helm_chart_name,
            "dev": dev or [],
            "application": application,
            "name": self.name,
            "docker_platforms": docker_platforms,
            "binary_platforms": binary_platforms,
            "owner": self.owner,
        }

        if has_markers:
            content = self._render_managed_readme_sections(existing, context)
        elif existing is not None:
            pulumi.log.warn(
                f"Preserving README.md for {self.owner}/{self.name} because it "
                "does not contain managed template markers"
            )
            content = existing
        else:
            content = _get_template("readme", "readme.md.j2").render(**context)

        self._repository_file(
            "README.md",
            "README.md",
            content,
            retain_on_delete=True,
        )

    def sync_workflow(
        self,
        language: str,
        versions: list[str],
        binary: bool,
        binary_platforms: list[dict[str, str]] | None,
        lint: bool,
        test: bool,
        package: bool,
        documentation: bool,
        changelog: bool,
        docker: bool,
        docker_platforms: list[dict[str, str]] | None,
    ):
        supported_languages = {"go", "python", "rust"}
        if any((lint, test, binary, package, documentation)) and language not in supported_languages:
            raise ValueError(
                "language must be one of 'go', 'python', or 'rust' when language-specific workflows are enabled"
            )
        if test and language in {"go", "python"} and not versions:
            raise ValueError(f"versions cannot be empty when {language} tests are enabled")
        if binary and not binary_platforms:
            raise ValueError("binary_platforms cannot be empty when binary builds are enabled")
        if docker and not docker_platforms:
            raise ValueError("docker_platforms cannot be empty when Docker builds are enabled")
        if documentation:
            raise ValueError("documentation workflows are not currently generated; deploy documentation separately")

        complete_workflows = {
            "validate-pr-title.yml": _get_template("workflow", "validate-pr-title.yml.j2").render(),
            "stale.yml": _get_template("workflow", "stale.yml.j2").render(),
            "scorecard.yml": _get_template("workflow", "scorecard.yml.j2").render(
                default_branch_name=self.default_branch_name
            ),
            "dependency-review.yml": _get_template("workflow", "dependency-review.yml.j2").render(),
            "codeql.yml": _get_template("workflow", "codeql.yml.j2").render(
                default_branch_name=self.default_branch_name,
                language=language,
            ),
        }
        for filename, content in complete_workflows.items():
            path = f".github/workflows/{filename}"
            self._repository_file(path, path, content)

        context = {
            "language": language,
            "versions": versions,
            "lint": lint,
            "test": test,
            "package": package,
            "binary": binary,
            "binary_platforms": binary_platforms,
            "documentation": documentation,
            "changelog": changelog,
            "docker": docker,
            "docker_platforms": docker_platforms,
        }

        if lint or test or binary or docker:
            path = ".github/workflows/ci.yml"
            content = _get_template("workflow", "ci.yml.j2").render(**context)
            self._repository_file(path, path, content)

        if package or documentation or changelog or docker:
            path = ".github/workflows/release.yml"
            content = _get_template("workflow", "release.yml.j2").render(**context)
            self._repository_file(path, path, content)

    # ---------- Renovate ----------

    def sync_renovatebot(
        self,
        schedule: str | None,
        language: str,
        configs: list[str],
        additional_configs: list[str],
    ):
        # Main renovate file
        template = _get_template("renovatebot", "renovate.json5.j2")
        self._repository_file(
            ".github/renovate.json5",
            ".github/renovate.json5",
            template.render(
                schedule=schedule,
                language=language,
                configs=configs,
                additional_configs=additional_configs,
                repository_name=f"{self.owner}/{self.name}",
            ),
        )

        # Core snippets (always)
        core_snippets = ["labels", "semanticCommits", "github-actions"]
        for snippet in core_snippets:
            cfg_template = _get_template("renovatebot", "config", f"{snippet}.json5.j2")
            self._repository_file(
                f".github/renovate/{snippet}.json5",
                f".github/renovate/{snippet}.json5",
                cfg_template.render(language=language, configs=configs),
            )

        # Optional per-tool configs by name
        cfg_root = resources.files(PACKAGE_NAME).joinpath("templates", "renovatebot", "config")
        requested = set(configs) | set([x.replace(".json5", "") for x in additional_configs])
        for entry in cfg_root.iterdir():
            name = entry.name.replace(".json5.j2", "")
            if name in requested and name not in core_snippets:
                cfg_template = _get_template("renovatebot", "config", entry.name)
                self._repository_file(
                    f".github/renovate/{name}.json5",
                    f".github/renovate/{name}.json5",
                    cfg_template.render(),
                )
