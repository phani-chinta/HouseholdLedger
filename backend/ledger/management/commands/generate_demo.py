import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ledger import models as m
from ledger import services as svc

YEAR = 2026
SEED = 20261022
START = date(YEAR, 1, 1)
MERCHANTS = [
    ("Fresh Basket", "groceries", "fresh basket", 520), ("Green Market", "groceries", "green market", 380),
    ("Brew Society", "dining", "brew society", 120), ("Tamarind Kitchen", "dining", "tamarind", 350),
    ("Metro Transit", "transport", "metro transit", 90), ("RideGo", "transport", "ridego", 240),
    ("Paper & Thread", "shopping", "paper thread", 550), ("Northline Store", "shopping", "northline", 800),
    ("City Power", "utilities", "city power", 780), ("WellCare Pharmacy", "health", "wellcare", 240),
    ("Oakwood Properties", "housing", "oakwood", 12500), ("Streamline Plus", "subscriptions", "streamline", 299),
    ("Harbor Payroll", "salary", "harbor payroll", 52000), ("Open Market", "shopping", "open market", 340),
]
TABLES = [
    m.UserProfile, m.Account, m.Merchant, m.Category, m.CategoryRule, m.Transaction,
    m.Budget, m.RecurringSeries, m.RecurringOccurrence, m.Insight, m.ExceptionCase, m.LedgerEvent,
]


class Dataset:
    def __init__(self, directory):
        self.directory = directory
        self.counts = Counter()
        self.metrics = Counter()
        self.files = {}
        self.fields = {}
        self.ids = Counter()
        self.hashes = {}
        if directory:
            directory.mkdir(parents=True, exist_ok=True)
            self.json_seed = (directory / "seed.jsonl").open("w", newline="\n", encoding="utf-8")
            for model in TABLES:
                name = model._meta.db_table.removeprefix("ledger_")
                self.fields[name] = [field.attname for field in model._meta.local_fields]
            self.fields["ground_truth"] = ["entity_id", "category", "is_recurring", "issue_type", "expected"]
            for name, fields in self.fields.items():
                handle = (directory / f"{name}.csv").open("w", newline="", encoding="utf-8")
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(fields)
                self.files[name] = (handle, writer)

    def key(self, name):
        self.ids[name] += 1
        return self.ids[name]

    def add(self, name, rows, load=False):
        self.counts[name] += len(rows)
        if self.directory:
            fields = self.fields[name]
            writer = self.files[name][1]
            for row in rows:
                values = row if name == "ground_truth" else [getattr(row, field) for field in fields]
                writer.writerow([json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value for value in values])
                if name != "ground_truth":
                    record = {"table": name, "row": dict(zip(fields, values))}
                    self.json_seed.write(json.dumps(record, default=str, ensure_ascii=False, separators=(",", ":")) + "\n")
        if load and rows and name != "ground_truth":
            type(rows[0]).objects.bulk_create(rows, batch_size=1000)

    def finish(self, configuration):
        def ratio(numerator, denominator):
            return round(numerator / denominator, 6) if denominator else None

        n = self.metrics
        if n["ledger_delta"] or n["active_transfer_delta"] or n["budget_delta"] or n["category_total"] != self.counts["transaction"]:
            raise CommandError("Dataset reconciliation failed.")
        summary = {
            "configuration": configuration, "rows": dict(self.counts), "checks": dict(n),
            "categorization_accuracy": ratio(n["category_correct"], n["category_total"]),
            "categorization_coverage": ratio(n["category_assigned"], n["category_total"]),
            "recurring_precision": ratio(n["recurring_tp"], n["recurring_tp"] + n["recurring_fp"]),
            "recurring_recall": ratio(n["recurring_tp"], n["recurring_tp"] + n["recurring_fn"]),
        }
        if not self.directory:
            return summary
        for handle, _ in self.files.values():
            handle.close()
        self.json_seed.close()
        sql = ["\\set ON_ERROR_STOP on", "BEGIN;"]
        for model in TABLES:
            table = model._meta.db_table
            name = table.removeprefix("ledger_")
            columns = ",".join(self.fields[name])
            sql.append(f"\\copy {table} ({columns}) FROM '{name}.csv' WITH (FORMAT csv, HEADER true)")
            if model._meta.pk.get_internal_type() == "BigAutoField":
                sql.append(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), GREATEST(COALESCE(MAX(id), 0), 1), MAX(id) IS NOT NULL) FROM {table};")
        for prefix, value in [("REC", self.ids["series"]), ("USR", configuration["profiles"]), ("TXN2026", configuration["transactions"])]:
            sql.append(f"INSERT INTO ledger_sequence (prefix, value) VALUES ('{prefix}', {value});")
        sql.append("COMMIT;")
        (self.directory / "seed.sql").write_text("\n".join(sql) + "\n", encoding="utf-8")
        (self.directory / "README.md").write_text(
            "# Household Ledger synthetic data\n\nRun seed.sql with psql from this directory against an empty migrated database. "
            "seed.jsonl contains the same domain records as table/row objects. "
            "Create demo logins through the application generator. See project docs for the dictionary and detection rules. "
            "ground_truth.csv is exclusively for offline evaluation.\n", encoding="utf-8")
        (self.directory / "source_manifest.json").write_text(json.dumps({
            "source": "Original synthetic generator", "external_sources": [], "currency": "INR",
            "seed": configuration["seed"], "year": YEAR,
            "command": f"python manage.py generate_demo --profiles {configuration['profiles']} --transactions {configuration['transactions']} --seed {configuration['seed']} --export exports/run",
        }, indent=2), encoding="utf-8")
        (self.directory / "validation.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        for path in sorted(self.directory.iterdir()):
            if path.is_file() and path.name != "checksums.json":
                with path.open("rb") as handle:
                    self.hashes[path.name] = hashlib.file_digest(handle, "sha256").hexdigest()
        (self.directory / "checksums.json").write_text(json.dumps(self.hashes, indent=2, sort_keys=True), encoding="utf-8")
        return summary


def master(dataset, load):
    categories = {code: m.Category(code=code, name=name, kind=kind) for code, name, kind in svc.STARTER_CATEGORIES}
    merchants = [
        m.Merchant(id=f"MRC-{index:04d}", name=name)
        for index, (name, _, _, _) in enumerate(MERCHANTS, 1)
    ]
    rules = []
    for merchant, (_, code, pattern, _) in zip(merchants, MERCHANTS):
        rules.append(m.CategoryRule(id=len(rules) + 1, category=categories[code], merchant=merchant,
                                    pattern=pattern, starts=START, priority=30))
    rules.append(m.CategoryRule(id=len(rules) + 1, category=categories["groceries"], merchant=merchants[-1],
                                pattern="open market", starts=START, priority=30))
    dataset.add("merchant", merchants, load)
    dataset.add("category", list(categories.values()), load)
    dataset.add("categoryrule", rules, load)
    return merchants, rules


def profile_data(dataset, index, count, rng, merchants, rules, load):
    names = ["Aarav Sharma", "Ananya Rao", "Rohan Patel", "Meera Iyer", "Ishaan Reddy", "Diya Shah", "Kiran Menon"]
    profile = m.UserProfile(id=f"USR-{index:06d}", name=names[(index - 1) % len(names)] + (f" {index}" if index > 7 else ""))
    accounts = [
        m.Account(id=f"ACC-{profile.pk}-{number:02d}", profile=profile, name=name, opening_cents=rng.randrange(8000, 15001) * 100)
        for number, name in [(1, "Everyday account"), (2, "Savings account")]
    ]
    dataset.add("userprofile", [profile], load)
    dataset.add("account", accounts, load)
    rows, labels, cases, events, budgets, insights, series_rows, occurrences = [], [], [], [], [], [], [], []
    expected_recurring = set()
    spending = defaultdict(int)
    source_delta = Counter()

    def record(month, day, amount, kind, merchant_index=None, descriptor=None, account=0, related=None, utc_late=False, issue="", recurring=False):
        merchant = merchants[merchant_index] if merchant_index is not None else None
        source_category = MERCHANTS[merchant_index][1] if merchant else ""
        if kind == "refund":
            source_category = "shopping"
        elif kind == "transfer":
            source_category = "transfer"
        stamp = datetime(YEAR, month, day, 21 if utc_late else 8, 0, tzinfo=timezone.utc)
        entry = m.Transaction(id=f"TXN-{YEAR}-{dataset.key('txn'):08d}", account=accounts[account], merchant=merchant,
                              descriptor=descriptor or MERCHANTS[merchant_index][2].upper(), occurred_at=stamp,
                              kind=kind, cents=amount, related=related,
                              state=m.ARCHIVED if issue == "transfer_duplicate" else m.ACTIVE)
        svc.localize(entry, profile)
        svc.apply_decision(entry, rules)
        rows.append(entry)
        if entry.state == m.ACTIVE:
            source_delta[entry.account_id] += amount
        labels.append((entry.pk, source_category, int(recurring), issue, {
            "transfer_duplicate": "exclude_both_transfer_legs_and_flag_duplicate",
            "refund_expense": "credit_original_category",
            "timezone_boundary": "use_local_posting_month",
            "ambiguous": "send_to_review",
        }.get(issue, entry.decision)))
        if recurring:
            expected_recurring.add(entry.pk)
        if entry.decision in ("ambiguous", "unmatched", "unmatched_refund"):
            add_case(entry.decision, f"{entry.decision}:{entry.pk}", "Category needs review.", entry)
        if utc_late:
            add_case("timezone_boundary", f"tz:{entry.pk}", "Use the household posting date.", entry, extra={"local_date": str(entry.local_date)})
        if issue == "transfer_duplicate":
            add_case(issue, f"transfer:{entry.pk}", "A second credit referenced the same transfer and was quarantined.", entry)
        if entry.kind in ("expense", "refund") and entry.category_id:
            spending[(svc.month_start(entry.local_date), entry.category_id)] -= amount
        dataset.metrics["category_total"] += 1
        dataset.metrics["category_correct"] += int(entry.category_id == source_category)
        dataset.metrics["category_assigned"] += int(bool(entry.category_id))
        return entry

    def add_case(issue, fingerprint, message, entry=None, series=None, extra=None):
        case = m.ExceptionCase(id=dataset.key("case"), profile=profile, transaction=entry, series=series,
                               fingerprint=fingerprint, issue_type=issue, details={"message": message, **(extra or {})})
        cases.append(case)
        add_event(case, "opened", {"issue_type": issue, **case.details})
        if issue == "transfer_duplicate":
            add_event(entry, "quarantined", {"state": m.ARCHIVED, "reason": issue})

    def add_event(entity, action, after):
        events.append(m.LedgerEvent(id=dataset.key("event"), profile=profile, entity=type(entity).__name__,
                                    entity_id=str(entity.pk), action=action, after=after))

    salary = rng.randrange(480, 751) * 10000
    rent = rng.randrange(115, 172) * 10000
    for month in range(1, 13):
        record(month, 1, salary, "income", 12, recurring=True)
        record(month, 3, -rent, "expense", 10, recurring=True)
        if month != 7 or index % 5 != 1:
            record(month, 11, -29900, "expense", 11, recurring=True)
    debit = record(3, 12, -200000, "transfer", descriptor="Savings transfer")
    record(3, 12, 200000, "transfer", descriptor="Savings transfer credit", account=1, related=debit)
    if index % 5 == 1:
        record(3, 12, 200000, "transfer", descriptor="Duplicate transfer credit", account=1, related=debit, issue="transfer_duplicate")
    original = record(8, 8, -97000, "expense", 6)
    record(8, 14, 42000, "refund", 6, related=original, issue="refund_expense")
    record(10, 31, -61000, "expense", 1, utc_late=True, issue="timezone_boundary")
    record(11, 17, -86000, "expense", 13, issue="ambiguous")
    remaining = count - len(rows)
    weights = [23, 12, 10, 9, 8, 9, 8, 6, 9, 5]
    for offset in range(remaining):
        month = offset % 12 + 1
        merchant_index = rng.choices(range(10), weights)[0]
        _, _, pattern, base = MERCHANTS[merchant_index]
        season = 1.32 if month in (10, 11, 12) and merchant_index in (0, 3, 6, 7) else 1
        amount = max(25, round(base * rng.uniform(0.48, 2.2) * season)) * 100
        descriptor = rng.choice([pattern.upper(), f"UPI / {pattern} / POS", f"{pattern.title()} Online"])
        record(month, rng.randint(1, 28), -amount, "expense", merchant_index, descriptor)
    dataset.add("transaction", rows, load)
    for month in range(1, 13):
        month_date = date(YEAR, month, 1)
        limits = {"housing": rent, "groceries": 560000, "dining": 260000, "transport": 170000,
                  "shopping": 340000, "utilities": 310000, "subscriptions": 35000, "health": 150000}
        for code, limit in limits.items():
            budget = m.Budget(id=f"BUD-{profile.pk}-{month_date:%Y-%m}-{code}", profile=profile,
                              category_id=code, month=month_date, limit_cents=limit)
            budgets.append(budget)
            spent = spending[(month_date, code)]
            insight = m.Insight(id=dataset.key("insight"), profile=profile, budget=budget, revision=1,
                                spent_cents=spent, limit_cents=limit, status=svc.budget_status(spent, limit))
            insights.append(insight)
    if index % 5 == 1:
        key = f"budget:{profile.pk}:2026-11:wellness_old"
        add_case("invalid_budget_category", key, "A budget used a category code outside the taxonomy.",
                 extra={"code": "wellness_old", "month": "2026-11", "limit_cents": 120000})
        labels.append((key, "", "", "invalid_budget_category", "reject_unknown_category"))
    predicted = set()
    for account_id, key, matched, typical, due, missing in svc.recurrence_candidates(rows):
        series = m.RecurringSeries(id=f"REC-{dataset.key('series'):06d}", account_id=account_id,
                                   merchant=matched[0].merchant, key=key, name=matched[0].merchant.name,
                                   typical_cents=typical, first_date=matched[0].local_date, last_date=matched[-1].local_date,
                                   next_date=due, missing_months=missing)
        series_rows.append(series)
        add_event(series, "detected", {"name": series.name, "missing_months": missing})
        for entry in matched:
            occurrences.append(m.RecurringOccurrence(id=dataset.key("occurrence"), series=series, transaction=entry))
            predicted.add(entry.pk)
        for month in missing:
            add_case("recurring_gap", f"gap:{series.pk}:{month}", "Expected monthly payment did not appear.",
                     series=series, extra={"month": month})
    if index % 5 == 1:
        labels.append((f"{profile.pk}:streamline:2026-07", "", "", "recurring_gap", "flag_missing_july"))
    for insight in insights:
        insight.created_at = datetime(YEAR, 12, 31, tzinfo=timezone.utc)
    for case in cases:
        case.created_at = datetime(YEAR, 12, 31, tzinfo=timezone.utc)
    for event in events:
        event.created_at = datetime(YEAR, 12, 31, tzinfo=timezone.utc)
    dataset.metrics["recurring_tp"] += len(expected_recurring & predicted)
    dataset.metrics["recurring_fp"] += len(predicted - expected_recurring)
    dataset.metrics["recurring_fn"] += len(expected_recurring - predicted)
    recomputed = Counter()
    transfer_net = 0
    independently_spent = Counter()
    seen = set()
    for entry in rows:
        if entry.pk in seen or entry.account_id not in {account.pk for account in accounts}:
            raise CommandError("Duplicate transaction or missing account.")
        if entry.related_id and entry.related_id not in seen:
            raise CommandError("Missing or out-of-order related transaction.")
        if entry.kind not in dict(m.KINDS) or entry.local_date.year != YEAR:
            raise CommandError("Invalid transaction enum or date.")
        if (entry.kind == "expense" and entry.cents >= 0 or entry.kind in ("income", "refund") and entry.cents <= 0 or entry.cents == 0):
            raise CommandError("Invalid signed amount.")
        if entry.category_id and entry.category_id not in {item[0] for item in svc.STARTER_CATEGORIES}:
            raise CommandError("Invalid category join.")
        seen.add(entry.pk)
        if entry.state == m.ACTIVE:
            recomputed[entry.account_id] += entry.cents
            if entry.kind == "transfer":
                transfer_net += entry.cents
        if entry.kind in ("expense", "refund") and entry.category_id:
            independently_spent[(entry.local_date.year, entry.local_date.month, entry.category_id)] -= entry.cents
    dataset.metrics["active_transfer_delta"] += abs(transfer_net)
    dataset.metrics["ledger_delta"] += sum(abs(recomputed[key] - value) for key, value in source_delta.items())
    dataset.metrics["budget_delta"] += sum(abs(insight.spent_cents - independently_spent[(insight.budget.month.year, insight.budget.month.month, insight.budget.category_id)]) for insight in insights)
    dataset.metrics["validated_profiles"] += 1
    for name, items in [
        ("budget", budgets), ("recurringseries", series_rows), ("recurringoccurrence", occurrences),
        ("insight", insights), ("exceptioncase", cases), ("ledgerevent", events), ("ground_truth", labels),
    ]:
        dataset.add(name, items, load)


def demo_logins():
    user_model = get_user_model()
    password = "LedgerDemo2026!"
    customer = user_model.objects.create_user("customer", password=password, first_name="Aarav")
    m.UserProfile.objects.filter(pk="USR-000001").update(user=customer)
    for username in ("analyst", "reviewer", "administrator"):
        user = user_model.objects.create_user(username, password=password, is_staff=username == "administrator")
        group, _ = Group.objects.get_or_create(name=username)
        user.groups.add(group)


class Command(BaseCommand):
    help = "Generate fixed-seed financial data, validation evidence and optional demo database rows."

    def add_arguments(self, parser):
        parser.add_argument("--profiles", type=int, default=8)
        parser.add_argument("--transactions", type=int, default=4000)
        parser.add_argument("--seed", type=int, default=SEED)
        parser.add_argument("--export", type=Path)
        parser.add_argument("--load", action="store_true")

    def handle(self, *args, **options):
        profiles, count, seed = (options[key] for key in ("profiles", "transactions", "seed"))
        if profiles < 1 or profiles > 999999 or count < profiles * 42 or count > 99999999:
            raise CommandError("Use 1–999,999 profiles, at least 42 transactions per profile, and at most 99,999,999 transactions.")
        if not options["load"] and not options["export"]:
            raise CommandError("Provide --load or --export.")
        if options["export"] and options["export"].exists() and any(options["export"].iterdir()):
            raise CommandError("Export to a new or empty directory.")
        summary = self.generate(profiles, count, seed, options["export"], options["load"])
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))

    def generate(self, profiles, count, seed, directory, load):
        def run():
            if load and (m.UserProfile.objects.exists() or m.Category.objects.exists()):
                raise CommandError("Demo loading requires an empty migrated database.")
            dataset = Dataset(directory)
            merchants, rules = master(dataset, load)
            rng = random.Random(seed)
            baseline, remainder = divmod(count, profiles)
            for index in range(1, profiles + 1):
                profile_data(dataset, index, baseline + int(index <= remainder), rng, merchants, rules, load)
            if load:
                demo_logins()
                for prefix, value in [("REC", dataset.ids["series"]), ("USR", profiles), ("TXN2026", count)]:
                    m.Sequence.objects.create(prefix=prefix, value=value)
                from django.core.management.color import no_style
                from django.db import connection
                with connection.cursor() as cursor:
                    for statement in connection.ops.sequence_reset_sql(no_style(), TABLES):
                        cursor.execute(statement)
            return dataset.finish({"profiles": profiles, "transactions": count, "seed": seed})
        if load:
            with transaction.atomic():
                return run()
        return run()