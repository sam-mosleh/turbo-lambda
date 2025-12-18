PYTHON_VERSIONS := "3.12 3.13"
PYTHON_PLATFORMS := "x86_64 arm64"

@_:
    just --list

# Run tests
[group('qa')]
test *args:
    uv run -m pytest {{ args }}

_cov *args:
    uv run -m coverage {{ args }}

# Run tests and measure coverage
[group('qa')]
@cov *args:
    just _cov erase
    just _cov run -m pytest
    just _cov combine
    just _cov xml
    just _cov html
    just _cov report {{ args }}

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

_psr *args:
    GIT_COMMIT_AUTHOR="$(git config user.name) <$(git config user.email)>" \
    uvx --from=python-semantic-release semantic-release {{ args }}

# Release project
[group('lifecycle')]
release *args:
    @just _psr -v version {{ args }}
    uv publish
    just publish

# Check release output
[group('lifecycle')]
release-check *args:
    @just _psr -vv --noop version {{ args }}

_build_zip python_platform python_version:
    uv pip install \
        --refresh \
        --no-installer-metadata \
        --no-compile-bytecode \
        --python-platform {{ if python_platform == "arm64" { "aarch64-manylinux2014" } else { "x86_64-manylinux2014" } }} \
        --python-version {{ python_version }} \
        --target ./build/opt-{{ python_platform }}-{{ python_version }}/python \
        --requirement ./build/requirements.txt
    cd ./build/opt-{{ python_platform }}-{{ python_version }} && uvx --from deterministic-zip-go deterministic-zip -r ../opt-{{ python_platform }}-{{ python_version }}.zip .

# Build layer
[group('lifecycle')]
build:
    rm -rf build
    mkdir build
    uv sync
    uv export --frozen --no-group dev --no-editable -o ./build/requirements.txt
    @for python_platform in {{ PYTHON_PLATFORMS }}; do \
        for python_version in {{ PYTHON_VERSIONS }}; do \
            just _build_zip $python_platform $python_version; \
        done \
    done

_layer_name python_platform python_version:
    @echo turbo_lambda-{{ replace(`uv version --short`, '.', '-')}}-{{ python_platform }}-python{{ replace(python_version, '.', '') }}

_publish_layer_version region python_platform python_version:
    aws lambda publish-layer-version \
        --region {{ region }} \
        --layer-name $(just _layer_name {{ python_platform }} {{ python_version }}) \
        --description 'TurboLambda layer' \
        --compatible-runtimes python{{ python_version }} \
        --compatible-architectures {{ python_platform }} \
        --license-info MIT-0 \
        --zip-file fileb://build/opt-{{ python_platform }}-{{ python_version }}.zip \
        --query 'Version' \
        --output text

_add_layer_version_permission region python_platform python_version:
    aws lambda add-layer-version-permission \
        --region {{ region }} \
        --layer-name $(just _layer_name {{ python_platform }} {{ python_version }}) \
        --version-number $(just _publish_layer_version {{ region }} {{ python_platform }} {{ python_version }}) \
        --statement-id 'PublicLayer' \
        --action lambda:GetLayerVersion \
        --principal '*'

# Publish layer
[group('lifecycle')]
publish: build
    @for python_platform in {{ PYTHON_PLATFORMS }}; do \
        for python_version in {{ PYTHON_VERSIONS }}; do \
            just _add_layer_version_permission us-east-1 $python_platform $python_version; \
        done \
    done
