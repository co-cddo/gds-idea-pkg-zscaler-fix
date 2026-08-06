# gds-idea-pkg-zscaler-fix

Fixes Python HTTPS failing behind the corporate Zscaler TLS-inspecting
proxy. **Must never reach production** - see [Why this must never reach
production](#why-this-must-never-reach-production).

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

## Why your error (or lack of one) might differ from a colleague's

Two independent things determine what happens, and both vary by machine:

1. **Is the Zscaler CA already trusted by the OS default bundle Python
   loads?** This depends on which Python *distribution* you're running,
   not the version - `uv`-managed installs typically point at macOS's own
   ~128-cert bundle, which doesn't include it. Homebrew's Python links
   Homebrew's own OpenSSL bundle, which on some machines does include
   corporate CAs pushed by MDM into a Homebrew-managed cert store. `system
   python3` may differ again.
2. **Is strict X.509 validation on?** Only Python 3.13+ turns this on by
   default for `create_default_context()`. `requests`/`urllib3` apply it
   themselves regardless of Python version.

| CA trusted? | Strict validation on? | What you see |
|---|---|---|
| No | No | `unable to get local issuer certificate` |
| No | Yes | `unable to get local issuer certificate` |
| Yes | No | Works fine - no error at all |
| Yes | Yes | `Basic Constraints of CA cert not marked critical` |

So "it works on my machine" and "it's completely broken on mine" can both
be true for two people on the same network running different Python
installs, or the same install with different Python versions. This
package fixes all four combinations - it doesn't matter which one you're
in.

To check your own environment:

```bash
uv run python -c "
import ssl
ctx = ssl.create_default_context()
cns = [dict(kv for rdn in c['subject'] for kv in rdn).get('commonName') for c in ctx.get_ca_certs()]
print('Zscaler already trusted:', any('Zscaler' in (cn or '') for cn in cns))
print('Strict validation on   :', bool(ctx.verify_flags & ssl.VERIFY_X509_STRICT))
"
```

## Usage

**Do not add this to the `dev` group.** uv's `default-groups` setting
defaults to `["dev"]`, so `dev` gets installed by a plain `uv sync` -
and the `gds-idea-app-kit` web templates run a bare `uv sync` in their
`base` Docker stage, which `production` is built `FROM`. Anything in
`dev` ships to production.

Use a dedicated group instead - `zscaler` by convention:

```bash
uv add --group zscaler gds-idea-pkg-zscaler-fix \
  --index gds-idea=https://co-cddo.github.io/gds-idea-pypi/simple/
```

`uv add --group <name>` installs immediately (verified - it's not just a
`pyproject.toml`/lockfile edit), so no separate `uv sync` step is needed
right after adding it.

**One wrinkle:** a later bare `uv sync` performs an *exact* sync and will
uninstall it again, since `zscaler` isn't a default group (verified). If
that happens you'll just see the original SSL error return - re-run:

```bash
uv sync --group zscaler
```

`uv run` (as opposed to `uv sync`) does an *inexact* sync by default and
won't remove it, so this mostly only bites after an explicit `uv sync`.

Once installed, a `.pth` file activates the fix automatically on
interpreter startup, in every process using that environment - no code
changes required. To disable at runtime (e.g. while debugging an unrelated
TLS issue):

```bash
export ZSCALER_FIX_DISABLE=1
```

## Common scenarios

**In a uv project:** as above - `uv add --group zscaler ...`.

**A standalone script, no project:**

```bash
uv run --with gds-idea-pkg-zscaler-fix \
  --index gds-idea=https://co-cddo.github.io/gds-idea-pypi/simple/ \
  python your_script.py
```

or add it to the script's own PEP 723 inline metadata with `uv add
--script your_script.py --group zscaler ...`.

**A `gds-idea-app-kit` dev container:**

```bash
uv add --group zscaler gds-idea-pkg-zscaler-fix \
  --index gds-idea=https://co-cddo.github.io/gds-idea-pypi/simple/
```

in `app_src/`, then add `RUN uv sync --group zscaler` to the
**`development`** stage of `app_src/Dockerfile` (after `USER $USERNAME`) -
**not** the shared `base` stage.

`app_src/Dockerfile` is a tool-managed file (tracked in the
`[tool.gds-idea-app-kit.files]` manifest). Editing it means every future
`idea-app update` will skip it and write `app_src/Dockerfile.new` next to
it for manual merging, indefinitely. That's expected, not a bug - just
budget for it each time you update `gds-idea-app-kit`.

## Trying it out / confirming it fixes your problem

`public.ecr.aws` is a real, currently-Zscaler-intercepted endpoint (unlike
most AWS domains, which usually aren't), so it's a reliable way to
reproduce the failure and confirm the fix without needing your own broken
app running.

Run this from a plain directory with no `pyproject.toml` of its own (e.g.
`cd ~` first) - `uv run` auto-installs whatever project you're standing
inside as part of its own environment, which would install this very
package and make the "before" step pass when it shouldn't:

```bash
cd ~

# 1. Confirm the failure exists on your machine right now
uv run --with requests python -c "
import requests
requests.get('https://public.ecr.aws', timeout=10)
"
# Expect: requests.exceptions.SSLError (or CERTIFICATE_VERIFY_FAILED)

# 2. Now with the fix installed - same command, one extra --with
uv run --with requests \
  --with gds-idea-pkg-zscaler-fix \
  --index gds-idea=https://co-cddo.github.io/gds-idea-pypi/simple/ \
  python -c "
import requests
r = requests.get('https://public.ecr.aws', timeout=10)
print('SUCCESS:', r.status_code)
"
# Expect: SUCCESS 200
```

No project setup needed for this - `uv run --with` installs both packages
into a throwaway environment just for that one command. If step 1 doesn't
fail for you, you're one of the lucky "CA already trusted, strict
off" combinations above and don't have this problem to begin with; if it
fails with something other than an SSL/certificate error, that's a
different problem (check you're actually on the Zscaler-managed network).

## Why this must never reach production

The fix works by clearing `ssl.VERIFY_X509_STRICT`, which weakens
certificate validation for **every** TLS connection the process makes, not
just ones Zscaler intercepts. Production workloads are usually not behind
Zscaler at all, so this has pure downside there.

"Install it via a dev dependency group" isn't sufficient advice on its
own - see [Usage](#usage) above for why the `dev` group specifically must
be avoided in this org's Docker templates, and use a dedicated group
(`zscaler`) instead.

To verify it hasn't leaked into a production build:

```bash
uv pip list | grep -i zscaler   # should print nothing, after a bare `uv sync`
```

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
