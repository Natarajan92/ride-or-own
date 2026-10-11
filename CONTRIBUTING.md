# Contributing

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
git clone https://github.com/Natarajan92/ride-or-own.git
cd ride-or-own
uv sync --locked
uv run streamlit run app.py
```

Before opening a pull request, run:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Keep changes focused and describe the resulting behavior and validation. Update documentation when behavior changes. Keep `uv.lock` committed; update it with uv when changing dependencies.

Tests must use synthetic receipts and temporary workspaces. Never include real receipts, addresses, ride identifiers, financial records, databases, backups, screenshots of personal data, or credentials in commits, issues, or pull requests. The ignore rules exclude local data and review artifacts; review `git diff --cached` and `git status --short` before committing, and do not force-add ignored personal files.

The app starts with an empty workspace. Keep calculations deterministic and preserve the distinction between extracted values, user corrections, estimates, and missing inputs. Missing fares or distances must not silently become zero, and costs must not be counted twice.

For a suspected vulnerability, use the repository's private vulnerability reporting feature if enabled, or contact the maintainer privately. Use fictional reproductions and omit personal records from public reports.
