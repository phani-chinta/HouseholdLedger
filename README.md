# Household Ledger

Household Ledger categorizes transactions, identifies monthly payments, compares spending with budgets, and records decisions for review. The demo uses original synthetic data denominated in INR.

## Start locally

Install Python 3.11 or newer and start Docker Desktop. Run these commands in PowerShell from the project directory.

> **Note:** The `.venv` folder is located one level above `HouseholdLedger/` at `C:\CandidateWorkspace\.venv`. Run all commands from inside `C:\CandidateWorkspace\HouseholdLedger\`.

```powershell
cd C:\CandidateWorkspace\HouseholdLedger
docker compose -f database/compose.yml up -d --wait
..\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
..\.venv\Scripts\python.exe backend/manage.py migrate
..\.venv\Scripts\python.exe backend/manage.py generate_demo --load
..\.venv\Scripts\python.exe run_local.py
```

Open [Household Ledger](http://127.0.0.1:51800/) and select **Create an account** to start with a new household and an empty Everyday account. The overview lets you add transactions, set a budget, and add another ledger account. Use the gear next to your name to change your password.

**Explore demo accounts** opens the seeded role shortcuts. The shared demo password is `LedgerDemo2026!`. Demo staff can browse demo households. Self-registered households are excluded from their dashboard and account catalog. Seed an empty database before signing up if you want the demos. For a clean personal workspace, migrations and sign-up are sufficient. Sign-up initializes starter categories on an empty database.

### Later runs (existing database)

```powershell
cd C:\CandidateWorkspace\HouseholdLedger
..\.venv\Scripts\python.exe run_local.py
```

For an existing PostgreSQL installation, create an empty `ledger` database and set `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, and `PGPORT` before migrating. Compose uses PostgreSQL 16 on local port 55432. The frontend listens on 51800 and the API on 51801. Set `LEDGER_WEB_PORT` and `LEDGER_API_PORT` to change them.

The frontend ships with an offline utility stylesheet. For the Tailwind build, install Node.js and run `npm --prefix frontend install` followed by `npm --prefix frontend run build:css`. Custom styles are in `frontend/styles.css`.

### If sign-up returns 404

Stop both the frontend and Django processes and start them from the updated project folder using `run_local.py`. If the launcher reports an occupied port, use PowerShell to identify the process.

```powershell
Get-NetTCPConnection -LocalPort 51800,51801 -State Listen | Select-Object LocalPort, OwningProcess
```

In a signed-out browser, open `http://127.0.0.1:51800/api/session/` and look for `"registration": true`. If that field is missing, the frontend is reaching an older backend. After seeing the field, refresh the home page and try signing up again.

### Offline Django verification

For a machine with Django and tzdata already installed, an explicit SQLite setting enables offline verification. Run `$env:LEDGER_SQLITE = '1'` before migration, seeding, tests, and the launcher. PostgreSQL remains the default. The API workflow suite can run with Django REST Framework and SQLite for local verification.

## Demo workflow

| Role | Try this |
| --- | --- |
| Customer | Open November 2026, filter transactions, add an expense, review its rule, and add a linked refund. Compare the impact on net spending and budget insights. |
| Operations analyst | Switch households, run recurring detection, open the Streamline Plus series, and inspect the July gap. Review or resolve an exception with a note. |
| Risk/compliance reviewer | Inspect the duplicate transfer, timezone boundary, budget taxonomy rejection, audit trail, and evaluation metrics. |
| System administrator | Manage households, accounts, merchants, categories, and category rules. Add a rule and create a matching transaction to see the rule ID in its decision. |

The original amount, posting time and account on a posted transaction are immutable. You can update its category or archive it. A transfer archives both active legs together. If an expense has linked refunds, archive those first. Category rules apply to new transactions on their effective dates. Manual corrections are audited and propagate to linked refunds.

## Generate and verify data

The archive includes ready-made datasets in `exports/starter/` and `exports/full/`.

Run from the project directory after installing the Python requirements. CSV, JSON Lines, PostgreSQL seed script, ground truth, source manifest, SHA-256 hashes and validation summary appear in each export directory. Exports require an empty output directory and can run without a database connection.

```powershell
..\.venv\Scripts\python.exe backend/manage.py generate_demo --profiles 8 --transactions 4000 --export exports/starter-new
..\.venv\Scripts\python.exe backend/manage.py generate_demo --profiles 1000 --transactions 500000 --seed 20261022 --export exports/full-a
..\.venv\Scripts\python.exe backend/manage.py generate_demo --profiles 1000 --transactions 500000 --seed 20261022 --export exports/full-b
..\.venv\Scripts\python.exe database/verify_exports.py exports/full-a exports/full-b
..\.venv\Scripts\python.exe backend/manage.py test ledger
```

To populate an empty migrated database with the full dataset and demo users, add `--load` to one generator command. For CSV-based loading without demo users, run `psql -f seed.sql` from the exported directory against an empty migrated PostgreSQL database. Use either load method on a given database.

[Schema, data dictionary, and business rules](docs/data-dictionary.md) · [API map](docs/api.md) · [Full validation](docs/validation-full.json) · [Reproducibility report](docs/reproducibility.json)

## Project layout

| Tier | Location | Responsibility |
| --- | --- | --- |
| Frontend | `frontend/` | Static HTML, CSS, Tailwind build, vanilla JavaScript, and a same-origin local proxy |
| Backend | `backend/` | Django, REST Framework endpoints, authentication, authorization, classification, recurrence, audit and insights |
| Database | `database/` | PostgreSQL Compose configuration and verification utilities. Django migrations define the schema. |
