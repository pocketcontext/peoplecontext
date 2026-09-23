# PeopleContext production deployment

Date: 2026-09-24. Origin: https://people.pocketcontext.com. Dashboard: `/_/` (separate operator login). No application frontend or real employee seed data.

## Initial verified release

Application implementation `4769fa0`, CI gate follow-up `c8f60d4`.
Image: `ghcr.io/pocketcontext/peoplecontext@sha256:574d4ecfa0e03972ae5cd0f4473efbf30cc6e79cbc8020fe0bbf60ba02f3c54d`.
Pinned server: `381f81042586afdaa6498b8c0e2a78229a55bdff`.
[Release workflow](https://github.com/pocketcontext/peoplecontext/actions/runs/35928777496) passed HR privacy/integration, Google JIT/linking, portable skill, OAuth callback, deployment configuration/orchestration, container configuration, smoke/restore, and native AMD64/ARM64 publication. Tests use isolated synthetic records. The initial deploy job was intentionally disabled until service and safe wrapper provisioning.

A targeted DNS plan added only `people.pocketcontext.com` at the existing ONCE host. The unversioned `../once-pocketcontext/colors.yml` declares the service and maps private variables. Build and dry-run passed; no full convergence or new compute was needed. Deployment uses one CPU, 512 MiB, `/storage`, and disabled automatic updates.

The dedicated replica is R2 bucket `peoplecontext-backup`, prefix `once-pocketcontext/peoplecontext`, using the existing EU endpoint. Bucket access and an empty prefix were checked before deployment. Never reuse another application's replica prefix or start two writers against one replica.

Initial live checks verified public `/up`, Google provider enablement and client ID, password availability, `agents.authToken.duration=604800`, `authRule=disabled = false`, internal OAuth-only signup rule, and domain `pocketcontext.com`. Employee, account, link, role and private HR collections were empty. Existing sibling containers retained their running states.

## Continuous deployment

A dedicated SSH key and GitHub environment `once-pocketcontext` were provisioned without changing sibling keys. Its forced command invokes `/usr/local/sbin/deploy-peoplecontext`, installed by `deploy/install.py`. The wrapper locks `/run/lock/deploy-peoplecontext.lock`, pre-pulls the image, gracefully stops the sole old container, checks clean exit, then updates with automatic updates disabled. Enable repository variable `COLORS_PROFILE=once-pocketcontext` only after the initial service is healthy. Reinstall/reconcile this configuration after any scaffold convergence that rewrites deployment keys.

ONCE v0.3.3 otherwise overlaps SQLite/Litestream writers. Preserve the complete stored custom environment when changing `--env` values: ONCE replaces rather than merges that setting. Never print full Docker labels; they contain credentials. Operator credentials match the existing applications as requested; Google OAuth uses a separate client.

## Account operation and recovery

JIT creates `agents` identities, not employee records or HR privileges. Operators set unique employee `work_email` values; verified Google login can link matching accounts. HR grants and reporting lines remain explicit. Disable accounts for offboarding; deletion is blocked. Removing a link alone is temporary if a subsequent login still matches work_email. Workspace suspension does not revoke local sessions automatically.

The account/identity migration refuses automatic rollback. A recovery plan must account for the database migration and use a deliberate backup restore when necessary. Stop the writer before restoration. Real Google browser login remains a human verification step; protocol and identity behavior were tested against a synthetic provider.
