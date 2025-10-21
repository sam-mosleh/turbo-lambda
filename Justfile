@_:
    just --list

_cov *args:
    uv run -m coverage {{ args }}

_psr *args:
    GIT_COMMIT_AUTHOR="$(git config user.name) <$(git config user.email)>" \
    uvx --from=python-semantic-release semantic-release {{ args }}

# Run tests
[group('qa')]
test *args:
    uv run -m pytest {{ args }}

# Run tests and measure coverage
[group('qa')]
@cov *args:
    just _cov erase
    just _cov run -m pytest
    just _cov combine
    just _cov report {{ args }}
    just _cov xml
    just _cov html

# Run linters
[group('qa')]
lint:
    uvx ruff check
    uvx ruff format --check

# Check types
[group('qa')]
typing:
    uv run mypy --strict src tests

# Run automated fixes
[group('qa')]
fix:
    uvx ruff check --fix
    uvx ruff format

# Perform all checks
[group('qa')]
check-all: lint typing (cov '--fail-under=100')

# Update dependencies
[group('lifecycle')]
update:
    uv sync --upgrade

# Initialize project for local development
[group('lifecycle')]
_bootstrap:
    uv run ipython kernel install --user --env VIRTUAL_ENV $(pwd)/.venv --name=$(uv version | awk {'print $1'})
    pre-commit install
    touch .env

# Ensure project virtualenv is up to date
[group('lifecycle')]
install:
    @if ! {{ path_exists(".env") }}; then just _bootstrap; fi
    uv sync

# Remove temporary files
[group('lifecycle')]
clean:
    rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
    find . -type d -name "__pycache__" -exec rm -r {} +

# Recreate project virtualenv from nothing
[group('lifecycle')]
fresh: clean install

# Release project
[group('lifecycle')]
release *args:
    @just _psr -v version {{ args }}
    uv build
    uv publish

# Check release output
[group('lifecycle')]
release-check *args:
    @just _psr -vv --noop version {{ args }}
