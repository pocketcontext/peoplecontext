# PeopleContext fields and permissions

Use the live `schema` result as authoritative. `check` compares it with the bundled schema.json.

| SQL table | Business fields | Visibility |
| --- | --- | --- |
| employees | name, job_title, department | Authenticated accounts |
| compensation | employee, annual_salary_minor, currency, effective_date | Self, HR, direct/indirect managers |
| personal_details | employee, home_address, emergency_contact | Self and HR |
| hr_notes | employee, body | HR only |

Tables also have id, created, and updated. Salary is an integer in minor currency units; currency is three uppercase letters. Compensation and personal details have at most one row per employee. There may be multiple notes.

The auth collection `agents` and policy collections `account_links`, `hr_members`, and `reporting_lines` are excluded from SQL. Employee `work_email` is operator-managed and excluded from SQL. Matching a name does not establish identity. Trusted Google login may link a unique verified work email; HR membership is always explicit. Existing reporting relationships determine manager visibility.

All ordinary REST list/view routes are locked, including for HR; successful authorized writes return their own payloads. Read with filtered SQL, never relation expansion or realtime as a substitute. Authority changes apply on subsequent requests; in-flight snapshots may finish. Revoking HR membership can leave self/manager access intact.

Example:

```sh
python3 scripts/pc.py sql 'SELECT e.name, c.annual_salary_minor, c.currency FROM compensation c JOIN employees e ON e.id = c.employee ORDER BY e.name'
```
