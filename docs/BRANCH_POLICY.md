# Branch policy

- `main` contains integrated development; `release` advances to a validated main commit.
- Develop on short-lived `feature/*` or `fix/*` branches and merge through pull requests.
- The required check is **Thinker validation**, produced by GitHub Actions (app 15368).
  It runs the Python regression suite, Go race tests, embedded-contract checks, and
  Python/Go parity checks without live provider credentials or real MLX inference.
- Pull requests must be up to date with their target branch and pass validation.
- Force pushes and branch deletion are prohibited for main and release, including
  administrators. No additional reviewer approval is required for this single-owner workflow.
- Deprecated branch snapshots and existing release tags remain preserved.

## GitHub enforcement

`.github/branch-protection.json` is the intended GitHub REST API body for both
`main` and `release`. GitHub does **not** apply it automatically from the repository.
An administrator must apply it to `PUT /repos/smartinfer/thinker/branches/{branch}/protection`
and verify the settings by reading the same endpoint.

Initial setup on 2026-10-13 was blocked by HTTP 403: the local GitHub token lacked
the repository Administration permission. Treat protection as pending until the
GitHub settings have been successfully applied and read back. A passing workflow
alone does not prohibit direct pushes, deletion, or force pushes.
