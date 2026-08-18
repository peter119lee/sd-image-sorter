# Security Policy

## Reporting a vulnerability

Please report privately, not in a public issue:

- Use GitHub's **[Report a vulnerability](https://github.com/Rinne414/sd-image-sorter/security/advisories/new)** form (Security tab → Report a vulnerability).

Include what you did, what happened, and what you expected. A proof-of-concept
path or request is more useful than a description of a category.

## Supported versions

| Version | Supported |
| ------- | --------- |
| Latest release | Yes |
| Pre-release / beta | Fixes land in the next release, not backported |
| Anything older | No |

## Security model

**SD Image Sorter is a local-only desktop application.** It binds
`127.0.0.1:8487` by default and there is no authentication, because the trust
boundary is your own machine. It is not built for network deployment, multiple
users, shared servers, or internet-facing access.

That design choice is load-bearing, so the app enforces it rather than assuming
it:

- `localhost_only_middleware` (`backend/app_security.py`) rejects any request
  whose client IP is not a loopback address — even if the bind host is widened
  by configuration.
- CORS is restricted by regex to `localhost`, `127.0.0.1` and `[::1]` on any
  port. An ordinary web page you visit cannot reach the API.
- An in-memory per-client rate limit sits in front of the API.
- Every response carries `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.

### Do not expose it to a network

If you put this behind a reverse proxy, forward its port, or bind a public
interface, you are handing an unauthenticated file-management API to whoever can
reach it — including reading, moving and deleting images in any directory the
app can see. Don't.

## Defense in depth

Even with a trusted-local threat model, these are enforced in code:

| Concern | Where | What it does |
|---|---|---|
| Path traversal | `backend/utils/path_validation.py` | Rejects `..`, control characters, dot-only and whitespace-only names; caps path depth and length; resolves and re-validates symlink targets; compares with `Path.parents` rather than string prefixes |
| Extension allowlisting | `backend/config.py`, `path_validation.py` | Reads only `.png .jpg .jpeg .webp .gif .bmp .tif .tiff`; writes only `.png .jpg .jpeg .webp` |
| SQL injection | `backend/db_*.py` | Parameterized queries throughout; `LIKE` wildcards escaped with an explicit `ESCAPE` clause |
| XSS | `frontend/js/` | Vanilla JS, no templating or markdown rendering; user-derived text goes through `textContent` |
| Error disclosure | `backend/main.py` | A global handler logs the traceback server-side and returns a generic message to the client |
| Destructive operations | `services/image_service.move_file_to_trash` | Deletes go to the Recycle Bin, never `unlink`. Writes are atomic (temp sibling + fsync + `os.replace`) so an interrupted save cannot truncate your original |

## Dependency security

`scripts/security_check.py` runs `pip-audit` over the fully resolved dependency
tree and is a **blocking** step in CI (`scripts/run_ci.py`). A new, unreviewed
advisory fails the build on purpose.

```bash
python scripts/security_check.py
```

A small number of advisories are explicitly accepted in that script, each with a
written rationale, the date it was reviewed, and the condition that would make
it removable — mostly advisories that presume an untrusted network peer, which
the loopback-only guard already excludes. Read the rationale before assuming an
accepted id is an oversight.

## What the app does with your data

**Processed:** image files and their embedded metadata (prompts, negative
prompts, checkpoint, LoRA, VAE, seed, sampler).

**Stored** under the `data/` directory next to the app:

- `data/images.db` — image paths, parsed metadata, AI-generated tags, artist
  predictions, ratings, collections
- `data/state/` — session state, including the manual-sort session
- `data/models/`, `data/thumbnails/`, `data/cache/` — downloaded model weights
  and generated caches

**Not collected:** no telemetry, no analytics, no crash reporting, no account.
Models run locally. The app makes outbound network requests only when you ask it
to — downloading model weights, checking for an update, or calling a cloud VLM
provider whose API key you entered yourself.

### Privacy notes

Prompt metadata and generated tags can be sensitive, and tags can include NSFW
classifications. Treat `data/images.db` as sensitive: it is a plain SQLite file
with no encryption. On a shared machine, protect the folder at the OS level.

## For contributors

- Route every file path through `backend/utils/path_validation.py`.
- Parameterize every SQL query; never interpolate a value into SQL.
- Never render user-derived HTML; use `textContent`.
- Never send a stack trace or a full filesystem path to the client.
- No hardcoded secrets.
- A new network-facing feature needs a security review first.

---

**This is a local tool. Keep it local.**
