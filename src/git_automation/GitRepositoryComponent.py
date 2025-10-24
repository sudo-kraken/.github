import os
import time
from importlib import resources
from typing import Mapping, Awaitable, Any
import re

import requests
from jinja2 import Environment, PackageLoader
import pulumi
from pulumi.output import Output
import pulumi_github as github

PACKAGE_NAME = __name__.split(".")[0]

env = Environment(
    loader=PackageLoader(PACKAGE_NAME, "templates"),
    keep_trailing_newline=True,
    extensions=["jinja2.ext.do"],
)


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
        dependency: bool = False,
    ) -> None:
        """Repository component used to manage GitHub repository."""
        self.owner = owner
        self.name = name
        self.default_branch_name = default_branch_name
        self.author_fullname = author_fullname
        self.author_email = author_email
        self.branch_name = branch_name

        super().__init__(
            "pkg:index:GitRepositoryComponent", name, props, opts, dependency
        )

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
                secret_scanning=github.RepositorySecurityAndAnalysisSecretScanningArgs(
                    status="enabled"
                ),
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
            opts=pulumi.ResourceOptions(depends_on=[self.repository], parent=self),
        )

        self.default_branch = github.BranchDefault(
            f"{name}-default-branch",
            repository=self.repository.name,
            branch=self.default_branch_name,
            opts=pulumi.ResourceOptions(depends_on=[self.branch], parent=self),
        )

        # Working branch (PR mode) or default branch
        if self.is_pr_mode():
            self.working_branch = github.Branch(
                f"{name}-working-branch",
                repository=self.repository.name,
                branch=self.branch_name,
                source_branch=self.default_branch_name,
                opts=pulumi.ResourceOptions(
                    depends_on=[self.default_branch], parent=self
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
                    depends_on=[self.default_branch], parent=self
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
                    depends_on=[self.default_branch], parent=self
                ),
            )

    # ---------- helpers ----------

    def _detect_owner_is_org(self, owner: str) -> bool:
        """Return True if the owner is an organisation, else False."""
        try:
            headers = {"Accept": "application/vnd.github+json"}
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = requests.get(
                f"https://api.github.com/users/{owner}", headers=headers, timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("type") == "Organization"
        except Exception:
            pass
        return False

    def get_working_branch(self) -> github.Branch:
        return self.working_branch

    def regenerate_readme_template(self, readme_contents: str) -> str:
        pattern = r"<!-- template:begin:(.*?) -->(.*?)<!-- template:end:\1 -->"
        matches = re.findall(pattern, readme_contents, re.DOTALL)
        if not matches:
            return readme_contents
        for template_name, section_contents in matches:
            actual_contents = (
                f"<!-- template:begin:{template_name} -->"
                f"{section_contents}"
                f"<!-- template:end:{template_name} -->"
            )
            new_contents = (
                f"{{% include 'readme/sections/{template_name}.md.j2' %}}"
            )
            readme_contents = readme_contents.replace(actual_contents, new_contents)
        return readme_contents

    def _safe_get_readme(self) -> str | None:
        """Fetch README.md with retry. Return None if not available."""
        url = f"https://api.github.com/repos/{self.owner}/{self.name}/contents/README.md"
        headers = {"Accept": "application/vnd.github.raw+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for attempt in range(3):
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200 and r.text is not None:
                    return r.text
                if r.status_code in {403, 404}:
                    time.sleep(1 + attempt)
            except Exception:
                time.sleep(1 + attempt)
        return None

    def is_pr_mode(self) -> bool:
        return bool(self.branch_name and self.branch_name != self.default_branch_name)

    def _repository_file(
        self, ressource_name_type: str, file: str, content: str
    ) -> github.RepositoryFile:
        return github.RepositoryFile(
            f"{self.name}-{file}",
            repository=self.name,
            branch=self.get_working_branch().branch,
            file=file,
            content=content,
            commit_message=f"""\
chore(git-sync): auto-applied {ressource_name_type}

this file was auto-applied from pulumi
located here:
    - https://github.com/{self.owner}/.github

Signed-off-by: {self.author_fullname} <{self.author_email}>""",
            commit_author=self.author_fullname,
            commit_email=self.author_email,
            overwrite_on_create=True,
            opts=pulumi.ResourceOptions(
                depends_on=self._file_depends_on, parent=self
            ),
        )

    # ---------- sync: top-level files ----------

    def sync_licence(self, licence_name: str):
        license_dir = resources.files(PACKAGE_NAME).joinpath("license", licence_name)
        for license_file in license_dir.iterdir():
            with license_file.open() as file:
                license_content = file.read()
            self._repository_file("license", license_file.name, license_content)

    def sync_funding(self, fundings: list[str]):
        template = env.get_template(os.path.join("misc", "FUNDING.yml.j2"))
        self._repository_file(
            ".github/FUNDING.yml",
            ".github/FUNDING.yml",
            template.render(fundings=fundings),
        )

    def sync_pull_request_template(self):
        template = env.get_template(os.path.join("misc", "PULL_REQUEST_TEMPLATE.md.j2"))
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
                env.from_string(issue_content).render(
                    assignees=[self.owner], language=language
                ),
            )

    def sync_code_of_conduct(self, contact_email: str):
        template = env.get_template(os.path.join("misc", "CODE_OF_CONDUCT.md.j2"))
        self._repository_file(
            "CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.md", template.render(contact_email=contact_email)
        )

    def sync_contributing(self):
        with resources.files(PACKAGE_NAME).joinpath("misc", "CONTRIBUTING.md").open() as f:
            self._repository_file("CONTRIBUTING.md", "CONTRIBUTING.md", f.read())

    def sync_editor_config(self, language: str):
        template = env.get_template(os.path.join("misc", "editorconfig.j2"))
        self._repository_file(
            ".editorconfig", ".editorconfig", template.render(language=language)
        )

    def sync_gitignore(self, language: str, helm: bool):
        template = env.get_template(os.path.join("misc", "gitignore.j2"))
        self._repository_file(
            ".gitignore", ".gitignore", template.render(language=language, helm=helm)
        )

    def sync_gitattributes(self):
        with resources.files(PACKAGE_NAME).joinpath("misc", "gitattributes").open() as f:
            self._repository_file(".gitattributes", ".gitattributes", f.read())

    def sync_codeowner(self, owner: str | None = None):
        template = env.get_template(os.path.join("misc", "CODEOWNERS.j2"))
        self._repository_file(
            ".github/CODEOWNERS", ".github/CODEOWNERS", template.render(owner=owner or self.owner)
        )

    def sync_label(self, language: str, docker: bool, renovatebot: bool):
        template = env.get_template(os.path.join("misc", "labels.yml.j2"))
        self._repository_file(
            ".github/labels.yml",
            ".github/labels.yml",
            template.render(language=language, docker=docker, renovatebot=renovatebot),
        )

    def sync_security(self, security_email: str):
        template = env.get_template(os.path.join("misc", "SECURITY.md.j2"))
        self._repository_file(
            "SECURITY.md", "SECURITY.md", template.render(security_email=security_email)
        )

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
        # Try to render from template; fallback to a minimal pyproject
        try:
            template = env.get_template(os.path.join("python", "pyproject.toml.j2"))
            content = template.render(
                name=package_name or self.name,
                description=description,
                owner=self.owner,
                repo=self.name,
                author_fullname=self.author_fullname,
                author_email=self.author_email,
            )
        except Exception:
            content = f"""[project]
name = "{package_name or self.name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.11"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""
        self._repository_file("pyproject.toml", "pyproject.toml", content)

    def sync_vscode_config(self, language: str):
        template = env.get_template(os.path.join("vscode", "launch.json.j2"))
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
        if has_markers:
            template = env.from_string(self.regenerate_readme_template(existing))
        else:
            template = env.get_template(os.path.join("readme", "readme.md.j2"))

        self._repository_file(
            "README.md",
            "README.md",
            template.render(
                documentation_url=documentation_url,
                repository_name=f"{self.owner}/{self.name}",
                repository_basename=self.name,
                default_branch_name=self.default_branch_name,
                repository_title=repository_title,
                repository_description=repository_description,
                logo=logo,
                language=language,
                package_name=package_name,
                package=package,
                changelog=changelog,
                binary=binary,
                lint=lint,
                test=test,
                docker=docker,
                helm=helm,
                helm_chart_name=helm_chart_name,
                dev=dev or [],
                application=application,
                name=self.name,
                docker_platforms=docker_platforms,
                binary_platforms=binary_platforms,
                owner=self.owner,
            ),
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
        # Always present
        template = env.get_template(os.path.join("workflow", "validate-pr-title.yml.j2"))
        self._repository_file(".github/workflows/validate-pr-title.yml", ".github/workflows/validate-pr-title.yml", template.render())

        template = env.get_template(os.path.join("workflow", "stale.yml.j2"))
        self._repository_file(".github/workflows/stale.yml", ".github/workflows/stale.yml", template.render())

        template = env.get_template(os.path.join("workflow", "scorecard.yml.j2"))
        self._repository_file(".github/workflows/scorecard.yml", ".github/workflows/scorecard.yml", template.render())

        if documentation and language == "python":
            template = env.get_template(os.path.join("workflow", "python", "documentation.yml.j2"))
            self._repository_file(".github/workflows/documentation.yml", ".github/workflows/documentation.yml", template.render(default_branch_name=self.default_branch_name, branch_name=self.branch_name))

        if changelog:
            template = env.get_template(os.path.join("workflow", "changelog", "main.yml.j2"))
            self._repository_file(".github/workflows/changelog.yml", ".github/workflows/changelog.yml", template.render())

        if lint or test or binary or docker:
            template = env.get_template(os.path.join("workflow", "ci.yml.j2"))
            self._repository_file(".github/workflows/ci.yml", ".github/workflows/ci.yml", template.render(
                language=language,
                package=package,
                binary=binary,
                binary_platforms=binary_platforms,
                documentation=documentation,
                changelog=changelog,
                docker=docker,
                docker_platforms=docker_platforms,
            ))

        if docker and docker_platforms:
            template = env.get_template(os.path.join("workflow", "docker", "build.yml.j2"))
            self._repository_file(".github/workflows/docker-build.yml", ".github/workflows/docker-build.yml", template.render(default_branch_name=self.default_branch_name, branch_name=self.branch_name, platforms=docker_platforms))
            template = env.get_template(os.path.join("workflow", "docker", "package.yml.j2"))
            self._repository_file(".github/workflows/docker-package.yml", ".github/workflows/docker-package.yml", template.render(default_branch_name=self.default_branch_name, branch_name=self.branch_name, platforms=docker_platforms, tag_prefix="v" if language == "go" else ""))

        if language == "python":
            if lint:
                template = env.get_template(os.path.join("workflow", "python", "lint.yml.j2"))
                self._repository_file(".github/workflows/python-lint.yml", ".github/workflows/python-lint.yml", template.render())
            if test:
                template = env.get_template(os.path.join("workflow", "python", "test.yml.j2"))
                self._repository_file(".github/workflows/python-test.yml", ".github/workflows/python-test.yml", template.render())
            if package:
                template = env.get_template(os.path.join("workflow", "python", "package.yml.j2"))
                self._repository_file(".github/workflows/python-package.yml", ".github/workflows/python-package.yml", template.render())

        if language == "go":
            if lint:
                template = env.get_template(os.path.join("workflow", "go", "lint.yml.j2"))
                self._repository_file(".github/workflows/go-lint.yml", ".github/workflows/go-lint.yml", template.render())
            if test:
                template = env.get_template(os.path.join("workflow", "go", "test.yml.j2"))
                self._repository_file(".github/workflows/go-test.yml", ".github/workflows/go-test.yml", template.render())
            if binary:
                template = env.get_template(os.path.join("workflow", "go", "build.yml.j2"))
                self._repository_file(".github/workflows/go-build.yml", ".github/workflows/go-build.yml", template.render(platforms=binary_platforms))
            if package:
                template = env.get_template(os.path.join("workflow", "go", "package.yml.j2"))
                self._repository_file(".github/workflows/go-package.yml", ".github/workflows/go-package.yml", template.render())

        if language == "rust":
            if lint:
                template = env.get_template(os.path.join("workflow", "rust", "lint.yml.j2"))
                self._repository_file(".github/workflows/rust-lint.yml", ".github/workflows/rust-lint.yml", template.render())
            if test:
                template = env.get_template(os.path.join("workflow", "rust", "test.yml.j2"))
                self._repository_file(".github/workflows/rust-test.yml", ".github/workflows/rust-test.yml", template.render())
            if binary:
                template = env.get_template(os.path.join("workflow", "rust", "build.yml.j2"))
                self._repository_file(".github/workflows/rust-build.yml", ".github/workflows/rust-build.yml", template.render(platforms=binary_platforms))
            if package:
                template = env.get_template(os.path.join("workflow", "rust", "package.yml.j2"))
                self._repository_file(".github/workflows/rust-package.yml", ".github/workflows/rust-package.yml", template.render())

    # ---------- Renovate ----------

    def sync_renovatebot(
        self,
        schedule: str | None,
        language: str,
        configs: list[str],
        additionnal_configs: list[str],
    ):
        # Main renovate file
        template = env.get_template(os.path.join("renovatebot", "renovate.json5.j2"))
        self._repository_file(
            ".github/renovate.json5",
            ".github/renovate.json5",
            template.render(
                schedule=schedule,
                language=language,
                configs=configs,
                additionnal_configs=additionnal_configs,
                repository_name=f"{self.owner}/{self.name}",
            ),
        )

        # Core snippets (always)
        core_snippets = ["labels", "semanticCommits", "github-actions"]
        for snippet in core_snippets:
            cfg_template = env.get_template(os.path.join("renovatebot", "config", f"{snippet}.json5.j2"))
            self._repository_file(
                f".github/renovate/{snippet}.json5",
                f".github/renovate/{snippet}.json5",
                cfg_template.render(),
            )

        # Optional per-tool configs by name
        cfg_root = resources.files(PACKAGE_NAME).joinpath("templates", "renovatebot", "config")
        requested = set(configs) | set([x.replace(".json5", "") for x in additionnal_configs])
        for entry in cfg_root.iterdir():
            name = entry.name.replace(".json5.j2", "")
            if name in requested and name not in core_snippets:
                cfg_template = env.get_template(os.path.join("renovatebot", "config", entry.name))
                self._repository_file(
                    f".github/renovate/{name}.json5",
                    f".github/renovate/{name}.json5",
                    cfg_template.render(),
                )
