# gds-idea-pkg-zscaler-fix

On a Zscaler-managed device, the network sits between you and the
internet and re-signs HTTPS traffic so it can inspect it. Your machine
has been told to trust Zscaler's certificate for this - but Python hasn't,
so it refuses the connection. This package tells it to.

**Must never reach production** - see [Why this must never reach
production](#why-this-must-never-reach-production) before installing it
anywhere.

## Am I affected?

You'll see one of these from Python (not from `curl`, `apt`, or your
browser - those already work):

- `CERTIFICATE_VERIFY_FAILED`
- `unable to get local issuer certificate`
- `Basic Constraints of CA cert not marked critical`

To check without guessing:

```bash
uv run python -c "
import ssl
ctx = ssl.create_default_context()
cns = [dict(kv for rdn in c['subject'] for kv in rdn).get('commonName') for c in ctx.get_ca_certs()]
print('Zscaler already trusted:', any('Zscaler' in (cn or '') for cn in cns))
print('Strict validation on   :', bool(ctx.verify_flags & ssl.VERIFY_X509_STRICT))
"
```

For the fuller picture -- live connection tests against known clean and
known intercepted hosts, across `urllib`/`httpx`/`requests`, plus whether
`zscaler_fix` is already active -- run
[`scripts/diagnose.py`](scripts/diagnose.py) **via its raw URL, not a
local checkout**:

```bash
uv run --with httpx --with requests \
    https://raw.githubusercontent.com/co-cddo/gds-idea-pkg-zscaler-fix/main/scripts/diagnose.py
```

The URL form matters: `uv run` auto-installs whatever project it finds
above the current directory, so running the same script by path from
inside a clone of this repo installs `zscaler_fix` itself as a side
effect - `--isolated`/`--no-project` don't prevent that for a script path
under a project directory, only running via URL does, giving a genuinely
clean result for someone who hasn't installed anything.

Two people can get different results on the same network purely from
which Python version they're on (3.13+ enables strict validation by
default) -- to check that specifically:

```bash
for v in 3.11 3.12 3.13; do
    echo "=== Python $v ==="
    uv run --python "$v" --with httpx --with requests \
        https://raw.githubusercontent.com/co-cddo/gds-idea-pkg-zscaler-fix/main/scripts/diagnose.py
done
```

If `Zscaler already trusted` is `False`, or it's `True` but `Strict
validation on` is also `True`, you're affected. Two colleagues on the same
network can get different answers here - see [Why your error might differ
from a colleague's](#why-your-error-or-lack-of-one-might-differ-from-a-colleagues)
if you want to know why.

## Quick start

```bash
uv add --group zscaler gds-idea-pkg-zscaler-fix \
  --index gds-idea=https://co-cddo.github.io/gds-idea-pypi/simple/
```

> **Use `--group zscaler`, not `--group dev`.** A plain `uv sync` installs
> the `dev` group by default, and that group can end up in production
> Docker images - see [Why this must never reach
> production](#why-this-must-never-reach-production). `zscaler` is just a
> name; any group that isn't `dev` (or a default group) works.

That's it - installing it is the only step. A `.pth` file activates the
fix automatically whenever Python starts, in every process using that
environment. No code changes required.

**Confirm it worked**, without needing your own broken code running -
`public.ecr.aws` is a real, currently-Zscaler-intercepted endpoint (unlike
most AWS domains, which usually aren't):

```bash
cd ~   # run from a plain directory - see note below

# 1. Confirm the failure exists on your machine right now
uv run --with requests python -c "
import requests
requests.get('https://public.ecr.aws', timeout=10)
"
# Expect: requests.exceptions.SSLError (or CERTIFICATE_VERIFY_FAILED)

# 2. Now with the fix - same command, one extra --with
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

Run this from `~` or anywhere with no `pyproject.toml` of its own - `uv
run` auto-installs whatever project you're standing inside, which would
install this very package and make step 1 pass when it shouldn't.

If step 1 doesn't fail for you, you don't have this problem on your
current setup - see [Am I affected?](#am-i-affected) above. If it fails
with something other than an SSL/certificate error, that's a different
problem (check you're actually on the Zscaler-managed network). To
disable the fix at runtime, e.g. while debugging something unrelated:

```bash
export ZSCALER_FIX_DISABLE=1
```

## What it actually does

- Tells Python to trust Zscaler's certificate - added alongside what
  Python already trusts, nothing is removed
- Switches off one specific over-strict check that would reject that
  certificate even once it's trusted (needed on Python 3.13+)
- Activates itself automatically on install - no imports, no config
- Only affects the Python environment you install it into

### In detail

Python's HTTP clients don't consult the same certificate list as `curl`
or your browser:

- `urllib`/`ssl` use the OS trust store, but Python 3.13+ enables strict
  X.509 validation by default, which **rejects Zscaler's root CA**
  because its `basicConstraints` extension isn't marked critical.
- `requests`/`urllib3` don't use the OS trust store at all - they ship
  their own bundle (`certifi`), and apply the same strict check
  themselves, independent of Python version.

This package wraps `ssl.SSLContext.wrap_socket`/`wrap_bio` - the point
every Python HTTP client eventually calls to perform a TLS handshake,
regardless of how it built its context - so that, right before each
handshake, it adds the Zscaler CA via `load_verify_locations(cadata=...)`
and clears `ssl.VERIFY_X509_STRICT`. Both steps only happen if the CA
actually loads; a context that can't be prepared is left untouched.

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

## Why this must never reach production

The fix works by clearing `ssl.VERIFY_X509_STRICT`, which weakens
certificate validation for **every** TLS connection the process makes, not
just ones Zscaler intercepts. Production workloads are usually not behind
Zscaler at all, so this has pure downside there.

"Install it via a dev dependency group" isn't sufficient advice on its
own: uv's `default-groups` setting defaults to `["dev"]`, so a plain `uv
sync` installs whatever's in `dev` - and the `gds-idea-app-kit` web
templates run a bare `uv sync` in their shared `base` Docker stage, which
`production` is built `FROM`. Use a dedicated, non-default group (`zscaler`
by convention) instead, as in [Quick start](#quick-start) above.

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

## Is anything besides Python affected?

Mostly no. Almost all developer tooling endpoints are on Zscaler's bypass
list - measured from a managed device:

| Clean | Intercepted |
|---|---|
| PyPI, all GitHub hosts, ghcr.io, Docker Hub, quay.io, `auth.docker.io`, npm registry, Terraform registry, HashiCorp releases, HuggingFace, Bedrock, Anthropic API, regional `*.amazonaws.com` | `public.ecr.aws`, general web browsing, `*.on.aws` (e.g. OpenSearch Serverless) |

So npm, Terraform, `uv`/`pip`, and `docker pull` from Hub/ghcr/quay are all
unaffected. The two non-Python cases that do break:

- **`docker pull` from `public.ecr.aws`** - fix the Docker VM's trust
  store, not the container's (e.g. a Colima `provision:` block that
  installs the CA and runs `update-ca-certificates`).
- **`curl`/`git`/`apt` inside a container** hitting an intercepted host -
  `ADD` the CA into `/usr/local/share/ca-certificates/` and `RUN
  update-ca-certificates` in that Dockerfile.

To check any host yourself:

```bash
echo | openssl s_client -connect HOST:443 -servername HOST 2>/dev/null \
  | openssl x509 -noout -issuer
```

Zscaler as the issuer means it's intercepted. This is a point-in-time
measurement from one machine - ICS can change the bypass list.

## Why your error (or lack of one) might differ from a colleague's

Two independent things determine what happens, and both vary by machine -
see [What it actually does](#what-it-actually-does) for what each one
means:

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
be true for two people on the same network, running different Python
installs or even the same install with different Python versions. This
package fixes all four combinations - it doesn't matter which one you're
in.

## Design decisions

**The cert is vendored, not fetched at runtime.** The certificate in
`src/zscaler_fix/zscaler-root-ca.pem` is vendored from
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

**This doesn't use `truststore` / `pip-system-certs` - though they do work
locally.** On macOS, `pip-system-certs` fixes this on both Python 3.12 and
3.13 (tested directly against a live intercepted endpoint). It works by
calling `truststore.inject_into_ssl()`, which routes verification through
Apple's `Security.framework` instead of OpenSSL - so OpenSSL's strict
check never gets a chance to apply.

Two reasons this package doesn't do the same thing:

1. **It can't work in Linux containers.** There's no keychain to read,
   and on Python 3.13+ the strict check rejects the CA even when it *is*
   installed in the container's own OS trust store (tested).
2. **Swapping the validator has broken a real connection.** Apple's
   validator is stricter than OpenSSL about some certificate formatting,
   and rejected a legitimate AWS wildcard certificate reached over an SSH
   tunnel - breaking a previously-working Neptune connection in another
   GDS IDEA project. This package only ever *adds* a CA and relaxes one
   specific check; it never replaces the validator, so anything that
   already verified successfully continues to.

If you only work locally on macOS with no tunnel-style connections,
`pip-system-certs` is a reasonable alternative on its own. This package
exists because it also covers containers, with one approach that behaves
the same way everywhere.

## Developing this package

This section is for people changing `zscaler_fix`'s code, not for people
using it - see [Quick start](#quick-start) above for that.

### Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python package management
- [git](https://git-scm.com/)
- [gitleaks](https://github.com/gitleaks/gitleaks) for pre-commit secret scanning (`brew install gitleaks`)

### Getting started

```bash
git clone git@github.com:co-cddo/gds-idea-pkg-zscaler-fix.git
cd gds-idea-pkg-zscaler-fix
uv sync
uv run pre-commit install   # done automatically on first scaffold
```

Pre-commit runs [ruff](https://docs.astral.sh/ruff/) on every commit to
auto-fix lint issues and enforce formatting.

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

If files are modified by the hooks, the commit will be aborted. Review
the changes, `git add` them, and commit again.

To run hooks against all files manually:

```bash
uv run pre-commit run --all-files
```

### Versioning

This project uses [hatch-vcs](https://github.com/ofek/hatch-vcs) for
automatic versioning from git tags. Versions are never set manually.

On merge to `main`, the auto-release workflow creates a new tag based on
PR labels:

- `bump:major` — major version bump
- `bump:minor` — minor version bump
- (default) — patch version bump

## Licence

[MIT License](LICENCE)
