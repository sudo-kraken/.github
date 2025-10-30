<div align="center">
<img src="src/git_automation/logo/logo.svg" align="center" width="144px" height="144px" alt="logo"/>

### GitHub Organisation Automation with Pulumi

_Automate organisation and repository configuration using Pulumi. Uses uv for Python tooling and the Pulumi CLI for stack operations._
</div>

<div align="center">

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python)
![Pulumi CLI](https://img.shields.io/badge/Pulumi-CLI-4B57A5?style=for-the-badge&logo=pulumi)

</div>

## Contents

- [Overview](#overview)
- [Architecture at a glance](#architecture-at-a-glance)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Access token permissions](#access-token-permissions)
- [Safety](#safety)
- [Setup](#setup)
- [Run](#run)
  - [Using different environments](#using-different-environments)
  - [Import an existing resource](#import-an-existing-resource)
  - [Delete a resource](#delete-a-resource)
  - [Manual Pulumi operations](#manual-pulumi-operations)
- [Troubleshooting](#troubleshooting)
- [Limitations and manual steps](#limitations-and-manual-steps)
- [Licence](#licence)
- [Security](#security)
- [Contributing](#contributing)
- [Support](#support)

## Overview

This repository contains Pulumi constructs and helper code that codify GitHub organisation and repository settings. Stacks are managed with the Pulumi CLI and repositories will be permanently deleted when removed from configuration.

This repository also contains reusable README templates and generator code under `src/git_automation/templates`. Use the included templates to render repository README files. See `Pulumi.stack.yaml.example` for an example stack configuration showing repository options.

## Architecture at a glance

- **Pulumi** defines GitHub resources.
- **Pulumi CLI** runs previews and applies for stacks.
- **uv** manages Python tooling used in the project.
- **Environment switching** via `PULUMI_STACK` or other environment variables for dev and prod.

## Features

- Declarative management of GitHub repositories and settings.
- Environment-aware configuration using `PULUMI_STACK`.
- Import support for bringing existing repositories under management.
- Template-based repository file synchronization and workflow generation.

## Prerequisites

- Python **3.11+** with **uv**.
- **Pulumi CLI**.

## Access token permissions

Create a fine-grained GitHub token with the following repository level permissions. Scope to all or to specific repositories as needed.

- Administration Read/Write
- Contents Read/Write
- Issues Read/Write for labels
- Workflows Read/Write for GitHub Actions

## Safety

> [!WARNING]
> Repositories will be **permanently deleted** when removed from your Pulumi configuration and running `pulumi up`. Always ensure you have backups before removing repositories from your stack configuration. To enable safer archiving instead of deletion, modify `archive_on_destroy=True` in `GitRepositoryComponent.py`.

## Setup

```sh
# Install Python dependencies via uv
uv sync

# Install Pulumi CLI
curl -fsSL https://get.pulumi.com | sh

# (optional) Install any local helper tooling you need
```

## Run

```sh
# Set environment variables
export GITHUB_TOKEN=xxxx
export PULUMI_STACK=dev
export PULUMI_CONFIG_PASSPHRASE=xxxx
export AWS_ACCESS_KEY_ID=xxxx
export AWS_SECRET_ACCESS_KEY=xxxx
export AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Select or create the stack, preview and apply
pulumi login 's3://etc' # see the pulumi docs for details on cloud backends
pulumi stack select $PULUMI_STACK || pulumi stack init $PULUMI_STACK
pulumi preview
pulumi up
```

### Using different environments

```sh
# Development
export PULUMI_STACK=dev
pulumi stack select $PULUMI_STACK
pulumi up

# Production
export PULUMI_STACK=prod
pulumi stack select $PULUMI_STACK
pulumi up
```

### Import an existing resource

```sh
# Example (resource type, name, id vary by provider)
pulumi import <resource-type> <name> <id>
# e.g. pulumi import github:index/repository:Repository repo_name owner/repo
```

### Delete a resource

```sh
# Remove repository from configuration in your stack file, then:
pulumi up  # This will permanently delete the repository

# WARNING: This is destructive and cannot be undone easily
```

### Manual Pulumi operations

```sh
# If you need to run Pulumi commands directly on a specific stack
pulumi stack select <stack>
pulumi preview
pulumi up
```

## Troubleshooting

- **401 or provider initialisation errors**

  Check `GITHUB_TOKEN` scope and that the token has the required permissions.

- **Changes not applied to expected environment**

  Confirm `PULUMI_STACK` is set to the desired stack before `pulumi up`.

- **TLS or proxy issues**

  If running behind a proxy or custom CA, set `AWS_CA_BUNDLE` or your platform equivalent for provider calls that require custom trust.

- **Import fails**

  Ensure the import ID matches the provider’s expected format. For repositories it is typically `owner/repo`.

## Limitations and manual steps

Some settings are not currently supported by the GitHub provider and require manual configuration.

In **Code security**:

- Enable **Dependabot alerts**.

- Disable **Dependabot security updates**.

In **Settings → Actions**:

- Set **Approval for running fork pull request workflows from contributors** to **Require approval for all external contributors**.

- In **Workflow permissions**, tick **Allow GitHub Actions to create and approve pull requests**.

- Tick **Require actions to be pinned to a full length commit SHA**.

In **Settings → Rules → Rulesets → automation-sync**:

- Under **Require status checks to pass**, tick **Require branches to be up to date before merging**.

User level limitation:

In **Settings → Installations**:

- Add the required GitHub Apps to your repositories.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Security

If you discover a security issue, please review and follow the guidance in [SECURITY.md](SECURITY.md), or open a private security-focused issue with minimal details and request a secure contact channel.

## Contributing

Open issues or submit pull requests for suggestions and improvements.

See [CONTRIBUTING.md](CONTRIBUTING.md)

## Support

Open an [issue](/../../issues) with as much detail as possible, including Pulumi and provider versions and relevant command output.
