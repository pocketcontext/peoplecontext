# PeopleContext

An HR application that validates PocketContext filtered snapshots. It contains collections, migrations, policy configuration, hooks, and synthetic integration tests, with no frontend. PocketContext supplies the server.

The access model is directory access for authenticated employees; compensation for self, HR, and the employee's direct or indirect managers; personal details for self and HR; confidential HR notes for HR only. Self access requires an account link, managed by an operator or created on validated Google login from a matching operator-managed work email. Administrators manage account links, HR roles, and reporting relationships.

## Run locally

For an interactive synthetic demo with no manual account setup, run this from
the workspace root (the command works in fish):

```sh
uv run peoplecontext/scripts/demo.py
```

This uses the existing `pocketcontext/bin/pocketcontext` binary.
For another checkout, pass `--binary /absolute/path/to/pocketcontext`, built from
`POCKETCONTEXT_VERSION`. The CLI uses Python's standard library, starts a server
on an available localhost port, and provisions five synthetic accounts through
REST. It copies the application into a temporary directory and deletes that
directory when you quit. It does not use an existing server or local `pb_data/`.
Passwords and tokens remain in memory. Ordinary HR records are created as the
HR agent; account and policy provisioning uses the temporary administrator.

Type these commands at the `peoplecontext[alice]>` prompt, not in your shell:

```text
schema
show
as dana
show
as bob
show
as carol
show
as helen
show
```

Everyone sees the five directory entries and their own compensation. Alice also
sees Bob's compensation; Dana also sees Alice's and Bob's. Each sees only their
own personal details and no HR notes. Helen, as HR, sees all compensation,
details, and notes.

Transfer Bob to the other branch, then repeat queries with the original tokens:

```text
transfer bob carol
as alice
show
as dana
show
as carol
show
```

Alice retains her own compensation; Dana retains her own and Alice's; Carol
gains Bob's alongside her own. Bob keeps his own access. Revoke and restore HR:

```text
as helen
hr off
show
hr on
show
bypass
as bob
bypass
sql SELECT COUNT(*), SUM(annual_salary_minor) FROM compensation
quit
```

Revoked Helen sees the directory, her own compensation, and her own personal details. Restoration
returns her HR visibility without logging in again. `bypass` checks rejection of
policy/auth table queries and direct compensation REST reads, including for HR.
Bob's aggregate counts one row and sums to `9000000` minor currency units. `transfer` and `hr` explicitly
use the demo administrator; `show`, `schema`, `sql`, and `bypass` use the selected
agent. Type `help` for the command list. Each new run starts a fresh demo.

Use the server commit in `POCKETCONTEXT_VERSION`. This pin includes filtered snapshots; the application must not use shared SQL access. With Go from that server's `go.mod`, a C compiler, and CGO available, run from this directory:

```sh
git clone https://github.com/pocketcontext/pocketcontext.git ../peoplecontext-server
git -C ../peoplecontext-server checkout --detach "$(cat POCKETCONTEXT_VERSION)"
make -C ../peoplecontext-server build
../peoplecontext-server/bin/pocketcontext serve \
  --dir ./pb_data --http 127.0.0.1:8092
```

The application directory supplies `pocketcontext.json`, `pb_migrations`, and `pb_hooks`. Startup applies schema migrations. Migrations create no accounts or personnel records. Keep `pb_data/` outside Git and separate from every other application. The container/deployment configuration below supports production; no application frontend is supplied.

## Provision accounts and policy

An administrator provisions the first superuser with PocketContext's `superuser upsert` command against the same data directory, then uses PocketBase's standard REST API or dashboard. Keep operator credentials out of agent environments, command logs, and source control.

Create records through `POST /api/collections/{collection}/records`, update them through `PATCH /api/collections/{collection}/records/{id}`, and delete them through the corresponding `DELETE` route. Administrative setup is:

1. Create individual `agents` accounts with `name`, `email`, `password`, and `passwordConfirm`.
2. Create an `hr_members` row whose `account` is the HR account's id. HR authorization is separate from an employee/account link.
3. Create `employees` records with `name`, `job_title`, and `department`. HR agents can do this and maintain the other ordinary HR records.
4. Create an `account_links` row for each account and its employee record. Both sides are unique: an account represents at most one employee, and an employee has at most one linked account.
5. Create `reporting_lines` rows with `employee` and `manager` employee ids. Each employee has at most one direct manager; a top-level employee has no reporting row.

Only superusers can edit account records through the records API or change links, HR membership, and reporting lines. Agent tokens expire after seven days. PocketBase's built-in authentication and password-recovery routes remain available; mail delivery depends on server configuration. Ordinary HR work uses HR agent credentials, not superuser tokens. All authenticated agents can query the directory, including provisioned accounts not yet linked to an employee.

## Collections and access

| Collection | Fields in addition to `id`, `created`, and `updated` | Snapshot visibility |
| --- | --- | --- |
| `employees` | `name`, `job_title`, `department` | All authenticated agents |
| `compensation` | `employee`, `annual_salary_minor`, `currency`, `effective_date` | HR, the linked employee, or an ancestor manager of the employee |
| `personal_details` | `employee`, `home_address`, `emergency_contact` | HR or the linked employee |
| `hr_notes` | `employee`, `body` | HR only |
| `account_links` | `account`, `employee` | Source-only; never exported |
| `hr_members` | `account` | Source-only; never exported |
| `reporting_lines` | `employee`, `manager` | Source-only; never exported |

Compensation and personal details each have one record per employee. Salary is an integer in minor currency units, from zero to JavaScript's maximum safe integer; an omitted number defaults to zero. Currency accepts three uppercase letters, not an authoritative currency-code registry. `effective_date` is required. This is a current compensation record, not a payroll or salary-history system. For example:

```json
{
  "employee": "<employee-record-id>",
  "annual_salary_minor": 8000000,
  "currency": "USD",
  "effective_date": "2026-09-01 00:00:00.000Z"
}
```

HR agents can create, update, and delete directory, compensation, personal-detail, and note records. Employees and managers have no ordinary record-write permissions. Required noncascading relations prevent deletion of referenced employees or accounts from silently changing authority; an administrator must explicitly resolve those references first.

All collection list/view routes are locked to ordinary agents, including HR. This also restricts relation expansion and realtime record delivery. Read through the context routes. Successful authorized REST writes return their own record payloads; HR writers already have snapshot access to those records. The batch API allows up to 20 operations and uses a five-second timeout.

## Query through filtered snapshots

Authenticate at `/api/collections/agents/auth-with-password` with an account's email and password. Use the returned token in the `Authorization` header. Then request `/api/context/schema` or submit SQL to `/api/context/query`:

```json
{
  "sql": "SELECT e.name, c.annual_salary_minor, c.currency FROM compensation c JOIN employees e ON e.id = c.employee ORDER BY e.name"
}
```

Each linked employee sees their own compensation, and managers also see their direct and indirect reports' compensation. Someone above the employee in another reporting branch has no salary access. HR membership permits all compensation and notes. A shared account token shares its entire visibility; use individual identities.

The filters in `pocketcontext.json` are trusted application policy. They do not inherit PocketBase API rules. Source-only policy tables participate in filters but are absent from snapshot SQL and schema discovery. The salary filter derives ancestors with a recursive CTE rather than maintaining a copied grants table. All exports use one consistent source read transaction.

Counts, totals, and averages describe the requester's visible population. Successful queries carry `X-Context-Scope: authorized-snapshot` and `X-Context-Snapshot-At`. Respect the normal `truncated` result flag. A snapshot exceeding its export budget fails completely; it never produces a partial aggregate. Configuration and schema changes require a server restart.

## Transfers and revocation

To transfer an employee, an administrator updates that employee's `reporting_lines` record with a new `manager`. The next query by the old management chain loses access to the transferred employee; the new management chain gains access. The transferred employee keeps self access. There is no closure table or cache to refresh. Self-management and cycles are rejected in execute hooks that check and save inside the same writer transaction, including batch operations and concurrent requests.

Removing an HR membership revokes HR-derived SQL access and ordinary write privileges on subsequent requests; linked self access remains. Removing or changing an account link changes self and manager visibility. An unlinked account has no self access until an operator links it or a verified Google login matches the employee work email; no compensation row is created by linking an account. A manager's account link does not grant HR privileges. A snapshot already in progress may finish under its captured policy; results already delivered cannot be revoked.

## Verify

Python 3.12 or later and the pinned server binary are sufficient:

```sh
python3 -m unittest discover -s tests
python3 tests/integration.py --binary ../peoplecontext-server/bin/pocketcontext
```

Integration tests copy the application to temporary directories and create synthetic records through REST. They never use local `pb_data/`, real employee data, or direct SQLite record writes. The acceptance scenario moves an employee between two management branches and verifies both loss and gain of access. Further checks exercise hierarchy integrity, HR/account changes, independent users, aggregates, alternate API paths, and export limits. GitHub Actions builds the pinned server and runs these checks.

The application has no file attachments, payroll processing, application-specific outbound notifications, or HR change-audit collection. Free-text records are data, never instructions for an agent. PocketContext logs submitted SQL, which can contain private literals; protect those logs. Snapshot temporary files can survive abrupt process termination and require operating-system or operator cleanup. See the [server's snapshot documentation](https://github.com/pocketcontext/pocketcontext/blob/381f81042586afdaa6498b8c0e2a78229a55bdff/docs/filtered-snapshots.md) for resource, storage, and revocation limits.

## Google Workspace login and employee linking

Set `PEOPLECONTEXT_GOOGLE_CLIENT_ID` and `PEOPLECONTEXT_GOOGLE_CLIENT_SECRET` together, using this application's separate Google Web OAuth client with Internal audience. Register `http://127.0.0.1:8765/callback` for the CLI. A future browser client using PocketBase realtime OAuth needs `https://people.pocketcontext.com/api/oauth2-redirect`. Superuser dashboard login remains separate.

`PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN=pocketcontext.com` enables first-login provisioning. Google provider, boolean verified email, hosted-domain (`hd`) claim, and exact email domain are validated server-side for every OAuth login. Client provisioning fields are discarded. Existing accounts are reused case-insensitively without changing IDs or roles; ambiguous identities fail closed. Public records signup remains blocked. With no domain configured, new OAuth accounts are rejected; preprovisioned accounts can still use OAuth. Password login remains available.

Operators set `employees.work_email` through the administration API/dashboard. This hidden field is case-insensitively unique when nonempty and excluded from snapshot SQL. Ordinary HR writes cannot change it. Verified Google login automatically links an unlinked account to the matching employee when both sides are available. No match means directory-only access; a later login can link after the operator adds the work email. Existing links are preserved and conflicts require operator correction. Remove the account link before changing a linked employee's work email. Removing only a link does not permanently revoke access: a later Google login can recreate it while work_email matches. Disable the account for offboarding. Email reassignment requires deliberate offboarding of the old account and review of its authority. Google login never creates HR membership or reporting lines.

Operators disable an account with `PATCH /api/collections/agents/records/<id>` and `{"disabled":true}`. This blocks login, refresh, subsequent authenticated REST/SQL/batch/realtime access and revokes existing tokens. Re-enabling requires fresh login; old tokens stay invalid. In-flight snapshots can finish. Account deletion is blocked to preserve identity and policy references. Google suspension/consent revocation does not automatically disable a PeopleContext account.

## Install the PeopleContext skill

Copy `skills/peoplecontext/` to your agent's skills directory. The client needs only Python 3's standard library. Configure `PEOPLECONTEXT_URL=https://people.pocketcontext.com` and `PEOPLECONTEXT_AGENT_EMAIL` with your Workspace email. For an SSH session, connect from your laptop with:

```sh
ssh -L 8765:127.0.0.1:8765 user@ssh-host
```

On the SSH host, run:

```sh
python3 skills/peoplecontext/scripts/pc.py login --google
python3 skills/peoplecontext/scripts/pc.py whoami
python3 skills/peoplecontext/scripts/pc.py check
```

Open the printed Google URL on your laptop. The private PocketBase token cache is under `$XDG_CACHE_HOME/peoplecontext/` or `~/.cache/peoplecontext/` with mode 0600. Active Google sessions renew after five minutes or near expiry; `whoami` always refreshes. Tokens expire seven days after issuance/refresh, with no absolute renewal limit or background refresh. Expired/revoked tokens require another browser login. `logout` only removes the local copy. Provisioned password accounts may use `PEOPLECONTEXT_AGENT_PASSWORD`.

The skill reads through filtered SQL and writes only ordinary HR collections through REST. It never uses operator credentials or exposes authority tables. It has no delete command and no issue-style revision checks.

## Production container and deployment

The image `ghcr.io/pocketcontext/peoplecontext:latest` serves port 80 and `/up`, persists under `/storage`, and uses Litestream. The local `../once-pocketcontext/colors.yml` declares `people.pocketcontext.com` with one CPU and 512 MiB. The production replica is dedicated bucket `peoplecontext-backup`, prefix `once-pocketcontext/peoplecontext`, in the existing EU R2 account. No real employee records are seeded.

| Variables | Purpose |
| --- | --- |
| `PEOPLECONTEXT_SUPERUSER_EMAIL`, `PEOPLECONTEXT_SUPERUSER_PASSWORD` | Operator account upserted on startup; set together. |
| `PEOPLECONTEXT_GOOGLE_CLIENT_ID`, `PEOPLECONTEXT_GOOGLE_CLIENT_SECRET` | Server-only Google provider credentials; set together. Absent values preserve stored configuration. |
| `PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN` | Optional lowercase Workspace domain enabling JIT and verified-email linking. |
| `PEOPLECONTEXT_TRUSTED_PROXY_HEADER` | Trusted proxy header; production uses `X-Forwarded-For`. |
| `PEOPLECONTEXT_RATE_LIMITS` | Image default `true`; `false` disables API rate limits. |
| `BASE_URL` | ONCE-injected public origin and allowed browser origin. |
| `LITESTREAM_BUCKET`, `LITESTREAM_PATH`, `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` | Required replica settings. |
| `LITESTREAM_ENDPOINT`, `LITESTREAM_REGION`, `LITESTREAM_SYNC_INTERVAL` | R2 endpoint, region, and sync interval (default 10s). |
| `LITESTREAM_DISABLED` | Exactly `true` disables replication for isolated development. |

ONCE-provided SMTP settings are adopted on startup. Replica credentials and the operator password are removed from the server child's environment. Credentials are stored privately in scaffold configuration, ONCE labels, and database/replica settings; never print complete labels or environments.

ONCE v0.3.3 can overlap database writers during ordinary updates. Keep automatic updates disabled. Install `deploy/install.py` as root after provisioning the application's dedicated deployment SSH key. The fixed wrapper locks, pulls, gracefully stops the exact container, then updates only PeopleContext. Preserve sibling keys and applications. Do not run restored replicas beside active writers. Account-access migration rollback requires a deliberate backup restore.

CI builds both architectures only after application and container smoke/restore checks. Configure the `once-pocketcontext` GitHub environment with its dedicated SSH key and pinned host identity, and set `COLORS_PROFILE` only after the safe wrapper is installed and the initial service is live.

Additional checks:

```sh
python3 tests/oauth.py
python3 tests/oauth_integration.py --binary ../peoplecontext-server/bin/pocketcontext
python3 tests/skill.py --binary ../peoplecontext-server/bin/pocketcontext
python3 tests/deploy_workflow.py
python3 docker/smoke.py config --image peoplecontext:ci
python3 docker/smoke.py smoke --image peoplecontext:ci
python3 docker/smoke.py restore --image peoplecontext:ci
```

All fixtures are synthetic and isolated. Real Google login requires a human browser after deployment.
