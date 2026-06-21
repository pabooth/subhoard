# Contributing

Contributions are welcome through GitHub issues and pull requests.

Participation in this project is governed by the
[Code of Conduct](./CODE_OF_CONDUCT.md).
Maintainer releases follow [RELEASING.md](./RELEASING.md).

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m camoufox fetch
```

Run the local checks before submitting a change:

```bash
python -m unittest discover -s tests -v
python -m py_compile subhoard.py tests/test_subhoard.py
ruff check subhoard.py tests
python -m build
```

Keep pull requests focused. Add tests for behavior changes and update the
README when changing command-line options or environment variables.

Do not include cookie exports, credentials, cached posts, generated archives,
or other content you do not have permission to redistribute.
