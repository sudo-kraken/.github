# Contributing

Contributions are welcome through issues and pull requests.

Before opening a pull request:

1. Create a focused branch and use a conventional commit message.
2. Run `uv sync --all-extras --locked`.
3. Run `uv run ruff format --check .`, `uv run ruff check .`, and `uv run pytest`.
4. Describe the problem, the chosen approach, and any operational impact in the pull request.

Never include tokens, Pulumi secrets, or private stack configuration in commits or test output.
