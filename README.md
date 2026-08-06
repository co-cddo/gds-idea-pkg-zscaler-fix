# gds-idea-pkg-zscaler-fix

Fixes Python HTTPS failing behind the corporate Zscaler TLS-inspecting
proxy. **Development-only** - see [Why this must stay dev-only](#why-this-must-stay-dev-only).

## The problem

On a Zscaler-managed device, outbound HTTPS is intercepted and re-signed
with Zscaler's own root CA. The OS trusts that CA (via MDM), so `curl`,
`apt`, and browsers work fine - but Python's HTTP clients don't consult the
OS trust store the same way:

- `urllib`/`ssl` use the OS store, but Python 3.13+ enables strict X.509
  validation by default, which **rejects Zscaler's root CA** because its
  `basicConstraints` extension isn't marked critical.
- `requests`/`urllib3` don't use the OS store at all - they ship their own
  bundle (`certifi`), and apply the same strict check themselves,
  independent of Python version.

Either way: `CERTIFICATE_VERIFY_FAILED`, or if you've already tried adding
the cert the obvious way, `Basic Constraints of CA cert not marked
critical`.

## Usage

```bash
uv add --group dev gds-idea-pkg-zscaler-fix --index gds-idea=https://co-cddo.github.io/gds-idea-pypi/simple/
```

That's it. A `.pth` file activates the fix automatically on interpreter
startup, in every process - no code changes required. To disable at
runtime (e.g. while debugging an unrelated TLS issue):

```bash
export ZSCALER_FIX_DISABLE=1
```

## Why this must stay dev-only

The fix works by clearing `ssl.VERIFY_X509_STRICT`, which weakens
certificate validation for **every** TLS connection the process makes, not
just ones Zscaler intercepts. Production workloads are usually not behind
Zscaler at all, so this has pure downside there - install it only via a
dev/test dependency group, never as a runtime dependency.

## What this does NOT fix

- `curl`, `apt`, `git` - these need the CA in the OS/container trust store
  instead (`ADD` the cert + `update-ca-certificates` in your Dockerfile).
- `grpcio` - bundles its own BoringSSL; needs `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`.
- `psycopg`/`libpq` - has its own TLS handling for Postgres connections.
- `uv` itself - has its own Rust TLS stack; use `SSL_CERT_FILE` or
  `--native-tls` if it needs to reach an intercepted host.

## Why the cert is vendored, not fetched at runtime

The certificate in `src/zscaler_fix/zscaler-root-ca.pem` is vendored from
[alphagov/govuk-infrastructure](https://github.com/alphagov/govuk-infrastructure/blob/main/cacerts/zscaler-root-ca.pem)
rather than downloaded on every process start:

- Fetching a trust anchor over the network at runtime is a bootstrap
  problem - you'd need working TLS to fetch the fix for TLS.
- It would mean anyone who can tamper with that URL or the connection to
  it could install an arbitrary root CA into every process using this
  package.

A [scheduled workflow](.github/workflows/cert-drift.yml) checks weekly
whether alphagov's copy has changed, so we get their maintenance without
the runtime coupling.

## Why this doesn't use `truststore` / `pip-system-certs`

Those repoint Python's validator at the OS-native trust store wholesale.
On macOS that routes verification through Apple's stricter validator for
**every** connection, including ones that never touch Zscaler - which in
practice broke an otherwise-valid, unrelated AWS certificate reached over
an SSH tunnel. This package only ever *adds* a CA and relaxes one specific
check; it never replaces the validator, so anything that already verified
successfully continues to.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python package management
- [git](https://git-scm.com/)
- [gitleaks](https://github.com/gitleaks/gitleaks) for pre-commit secret scanning (`brew install gitleaks`)

## Getting started

1. Clone the repository:

   ```bash
   git clone git@github.com:co-cddo/gds-idea-pkg-zscaler-fix.git
   cd gds-idea-pkg-zscaler-fix
   ```

2. Install dependencies:

   ```bash
   uv sync
   ```

3. Set up pre-commit hooks:

   ```bash
   uv run pre-commit install
   ```

   This is done automatically when the project is first scaffolded.
   Pre-commit runs [ruff](https://docs.astral.sh/ruff/) on every commit
   to auto-fix lint issues and enforce formatting.

## Development

### Running tests

```bash
uv run pytest
```

### Running linting manually

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

### Pre-commit hooks

Pre-commit hooks run automatically on `git commit`. They will:

- **Auto-fix** lint issues detected by `ruff check --fix`
- **Auto-format** code with `ruff format`
- **Check** YAML/TOML syntax, trailing whitespace, merge conflicts
- **Scan** for leaked secrets with gitleaks
- **Prevent** direct commits to `main`

If files are modified by the hooks, the commit will be aborted.
Review the changes, `git add` them, and commit again.

To run hooks against all files manually:

```bash
uv run pre-commit run --all-files
```

## Versioning

This project uses [hatch-vcs](https://github.com/ofek/hatch-vcs) for
automatic versioning from git tags. Versions are never set manually.

On merge to `main`, the auto-release workflow creates a new tag based on
PR labels:

- `bump:major` — major version bump
- `bump:minor` — minor version bump
- (default) — patch version bump

## Licence

[MIT License](LICENCE)
