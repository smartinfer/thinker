# Main branch consolidation — 2026-10-13

## Included work

The integration preserves history from every pre-consolidation local and fetched
remote branch. Starting points:

| Branch | Original tip | Included work |
| --- | --- | --- |
| main / feature/model-turn-reasoning-effort | f65c8e3308116d161e4a19bf85317e225ab7d07c | Provider reasoning effort, incomplete response handling, output-token floor, observer-only effort provenance |
| feature/provider-currency-v1 | dc81a6372c48b3ec32eca48de89544d60ab6fd82 | ThinkerQL packets, Go library, qualification, provider currency, tracing and metrics |
| feature/image-gateway-v0.3 | 1cc222501cb0a58780313a9c6ebe3a21b6e5cd2c | Typed image generation, adapters, budgets, durable completion ledger |
| oss-identity-alignment | 7736c3afcf96032ae6406a527a40ad7939618eed | Existing author, organization, and MIT licensing updates |

The MLX, model-turn-v1, and preserve-model-output branches were already ancestors
of these lines. An ancestry check confirmed that all original branch tips are
contained in the integration before promoting it to main.

## Conflict resolution and uncommitted work

- Retained route reasoning effort, output-token floor, token-limit field, and image
  settings together in the registry model.
- Regenerated model-turn request/response schemas from the combined canonical models.
- Combined the image and existing model catalogs, public exports, and resolvers.
- Preserved qualification constructor arguments and positional image-ledger support.
- Kept per-call adapter instances; image adapters use their parameterless constructors.
- Applied the saved GPT-4o registry patch, including the strong-chat alias. Retained
  the original patch in docs/proposals as provenance.
- Preserved PROVIDER_API_CURRENCY_20261011.md as a historical proposal. Its paths,
  implementation-status descriptions, and suggested future work reflect the original
  inspection; retaining it does not implement every proposed feature.
- Aligned the Python lockfile package version with the image branch's 0.3.0 metadata.
  Existing Go release tags remain unchanged.

## Verification

- Python suite: 513 passed, 2 skipped, 1 warning.
- The two skips are opt-in real MLX tests; no live model qualification was rerun.
- Existing warning: Anthropic live-verification provenance has no recorded date.
- Go: `go test -race ./...` passed.
- Embedded Go schema/error resources: `tools/sync_go_contract.py --check` passed.
- Offline Python/Go parity: 150 cases passed via `tools/check_go_parity.py`.
- Added four focused checks: per-call construction of all three image adapters and
  public image dispatch/replay through the actual adapter factory with HTTP mocked.
- `git diff --check` passed.

These checks cover local regression and integration behavior. They do not establish
current hosted-model availability, pricing, or live provider conformance.

## Recovery

Before consolidation, a verified Git bundle and uncommitted-file archive were saved
locally at:

`/Users/anjan/Tools/backups/thinker/pre-consolidation-20261013T175947Z`

The backup contains five original local branches, eight remote branches, four tags,
both untracked files, checksums, and restore instructions. Its temporary restore and
file comparison passed. It is a local backup, not an off-machine copy.

Old feature branches are retained for the subsequent cleanup step. No release tag
is created by this consolidation.
