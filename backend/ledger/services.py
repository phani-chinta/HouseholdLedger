import calendar
import re
from collections import defaultdict
from datetime import date
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Max, Q, Sum

from . import models as m

STARTER_CATEGORIES = [
    ("groceries", "Groceries", "expense"), ("dining", "Dining", "expense"),
    ("transport", "Transport", "expense"), ("shopping", "Shopping", "expense"),
    ("utilities", "Utilities", "expense"), ("housing", "Housing", "expense"),
    ("subscriptions", "Subscriptions", "expense"), ("health", "Health", "expense"),
    ("salary", "Salary", "income"), ("transfer", "Transfer", "transfer"),
]


def month_start(value):
    return value.replace(day=1)


def next_month(value):
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def normalize(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def event(entity, action, profile=None, actor=None, before=None, after=None):
    m.LedgerEvent.objects.create(
        profile=profile, actor=actor, entity=type(entity).__name__, entity_id=entity.pk,
        action=action, before=before or {}, after=after or {},
    )


def allocate(prefix):
    seq, _ = m.Sequence.objects.select_for_update().get_or_create(prefix=prefix)
    seq.value += 1
    seq.save(update_fields=["value"])
    return seq.value


def active_rules():
    return list(m.CategoryRule.objects.select_related("category").filter(
        state=m.ACTIVE, category__state=m.ACTIVE,
    ).order_by("priority", "pk"))


def matching_rules(entry, rules):
    descriptor = f" {normalize(entry.descriptor)} "
    for rule in rules:
        if rule.category.kind != entry.kind or rule.starts > entry.local_date:
            continue
        if rule.ends and rule.ends < entry.local_date:
            continue
        if f" {normalize(rule.pattern)} " in descriptor:
            yield rule


def infer_merchant(entry, rules):
    ids = {rule.merchant_id for rule in matching_rules(entry, rules) if rule.merchant_id}
    return ids.pop() if len(ids) == 1 else None


def classify(entry, rules):
    if entry.kind == "transfer":
        return "transfer", None, "excluded_transfer"
    if entry.kind == "refund":
        if entry.related_id and entry.related.category_id:
            return entry.related.category_id, None, "linked_refund"
        return None, None, "unmatched_refund"
    matches = [rule for rule in matching_rules(entry, rules)
               if not rule.merchant_id or rule.merchant_id == entry.merchant_id]
    if not matches:
        return None, None, "unmatched"
    winner = matches[0]
    if any(r.priority == winner.priority and r.category_id != winner.category_id for r in matches):
        return None, None, "ambiguous"
    return winner.category_id, winner.pk, "rule"


def apply_decision(entry, rules):
    entry.category_id, entry.rule_id, entry.decision = classify(entry, rules)


def localize(entry, profile):
    entry.local_date = entry.occurred_at.astimezone(ZoneInfo(profile.timezone)).date()


def open_issue(profile, issue, fingerprint, details, entry=None, series=None):
    case, created = m.ExceptionCase.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={"profile": profile, "issue_type": issue, "details": details,
                  "transaction": entry, "series": series},
    )
    if created:
        event(case, "opened", profile, after={"issue_type": issue, "details": details})


def inspect_entry(entry, profile):
    messages = {
        "ambiguous": "Two rules with the same priority match different categories.",
        "unmatched": "No active category rule matches the merchant descriptor.",
        "unmatched_refund": "The refund needs a linked, categorized purchase.",
    }
    if entry.decision in messages:
        open_issue(profile, entry.decision, f"{entry.decision}:{entry.pk}",
                   {"message": messages[entry.decision]}, entry=entry)
    if entry.occurred_at.date() != entry.local_date:
        open_issue(profile, "timezone_boundary", f"tz:{entry.pk}",
                   {"message": "UTC and household posting dates differ.", "local_date": str(entry.local_date)}, entry=entry)


def spending(entries):
    rows = entries.filter(kind__in=["expense", "refund"]).exclude(category__isnull=True)
    return {row["category_id"]: -row["total"] for row in rows.values("category_id").annotate(total=Sum("cents"))}


def budget_status(spent, limit):
    if spent > limit:
        return "over"
    return "near" if spent * 100 >= limit * 80 else "on_track"


def ledger(profile):
    return m.Transaction.objects.filter(account__profile=profile, state=m.ACTIVE)


def month_entries(profile, month):
    return ledger(profile).filter(local_date__gte=month, local_date__lt=next_month(month))


def refresh_insights(profile, month, actor=None):
    totals = spending(month_entries(profile, month))
    for budget in m.Budget.objects.filter(profile=profile, month=month).select_for_update():
        spent = totals.get(budget.category_id, 0)
        status = budget_status(spent, budget.limit_cents) if budget.state == m.ACTIVE else "archived"
        latest = m.Insight.objects.filter(budget=budget).order_by("-revision").first()
        if latest and (latest.spent_cents, latest.limit_cents, latest.status) == (spent, budget.limit_cents, status):
            continue
        insight = m.Insight.objects.create(
            profile=profile, budget=budget, revision=1 if latest is None else latest.revision + 1,
            spent_cents=spent, limit_cents=budget.limit_cents, status=status,
        )
        event(insight, "derived", profile, actor, after={"budget": budget.pk, "spent_cents": spent, "status": status})


def recurrence_candidates(entries):
    groups = defaultdict(list)
    for entry in entries:
        if entry.kind not in ("expense", "income"):
            continue
        merchant_key = entry.merchant_id or normalize(entry.descriptor)
        groups[(entry.account_id, f"{entry.kind}:{merchant_key}")].append(entry)
    for (account_id, key), group in sorted(groups.items()):
        group.sort(key=lambda entry: (entry.local_date, entry.pk))
        by_month = defaultdict(list)
        for entry in group:
            by_month[month_start(entry.local_date)].append(entry)
        eligible = [rows[0] for rows in by_month.values() if len(rows) == 1]
        if len(eligible) < 3:
            continue
        amounts = sorted(entry.cents for entry in eligible)
        typical = amounts[len(amounts) // 2]
        days = sorted(entry.local_date.day for entry in eligible)
        day = days[len(days) // 2]
        matched = [
            entry for entry in eligible
            if abs(entry.cents - typical) * 100 <= abs(typical) * 15
            and abs(entry.local_date.day - day) <= 4
        ]
        if len(matched) < 3:
            continue
        first, last = matched[0].local_date, matched[-1].local_date
        occupied = {month_start(entry.local_date) for entry in matched}
        missing = []
        cursor = month_start(first)
        while cursor < month_start(last):
            if cursor not in occupied:
                missing.append(cursor.strftime("%Y-%m"))
            cursor = next_month(cursor)
        if len(missing) >= len(matched):
            continue
        following = next_month(last)
        due = following.replace(day=min(day, calendar.monthrange(following.year, following.month)[1]))
        yield account_id, key, matched, typical, due, missing


@transaction.atomic
def refresh_recurrence(profile, actor=None):
    m.UserProfile.objects.select_for_update().get(pk=profile.pk)
    entries = ledger(profile).select_related("merchant").filter(kind__in=["expense", "income"])
    retained = []
    for account_id, key, entries, typical, due, missing in recurrence_candidates(entries.iterator()):
        series = m.RecurringSeries.objects.filter(account_id=account_id, key=key).first()
        if series is None:
            series = m.RecurringSeries(id=f"REC-{allocate('REC'):06d}", account_id=account_id, key=key)
        fields = {
            "merchant_id": entries[0].merchant_id, "name": entries[0].merchant.name if entries[0].merchant else entries[0].descriptor,
            "typical_cents": typical, "first_date": str(entries[0].local_date), "last_date": str(entries[-1].local_date),
            "next_date": str(due), "missing_months": missing, "state": m.ACTIVE,
        }
        before = {} if series._state.adding else {name: getattr(series, name) for name in fields}
        for name in ("first_date", "last_date", "next_date"):
            if before:
                before[name] = str(before[name])
        if before != fields:
            for name, value in fields.items():
                setattr(series, name, value)
            series.save()
            event(series, "detected" if not before else "revised", profile, actor, before, fields)
        retained.append(series.pk)
        existing = set(series.recurringoccurrence_set.values_list("transaction_id", flat=True))
        wanted = {entry.pk for entry in entries}
        if existing != wanted:
            series.recurringoccurrence_set.exclude(transaction_id__in=wanted).delete()
            m.RecurringOccurrence.objects.bulk_create([
                m.RecurringOccurrence(series=series, transaction_id=pk) for pk in sorted(wanted - existing)
            ])
            event(series, "occurrences_updated", profile, actor,
                  {"transactions": sorted(existing)}, {"transactions": sorted(wanted)})
        for month in missing:
            open_issue(profile, "recurring_gap", f"gap:{series.pk}:{month}",
                       {"message": "Expected monthly payment did not appear.", "month": month}, series=series)
    for series in m.RecurringSeries.objects.filter(account__profile=profile, state=m.ACTIVE).exclude(pk__in=retained):
        series.state = m.ARCHIVED
        series.save(update_fields=["state"])
        event(series, "archived", profile, actor, {"state": m.ACTIVE}, {"state": m.ARCHIVED})


def dashboard(profile, month):
    entries = month_entries(profile, month)
    totals = {row["kind"]: row["total"] for row in entries.values("kind").annotate(total=Sum("cents"))}
    expenses = -totals.get("expense", 0) - totals.get("refund", 0)
    income = totals.get("income", 0)
    categories = dict(m.Category.objects.values_list("pk", "name"))
    split = [{"category": categories[key], "code": key, "cents": value}
             for key, value in sorted(spending(entries).items(), key=lambda pair: -pair[1])]
    unassigned = expenses - sum(row["cents"] for row in split)
    if unassigned:
        split.append({"category": "Uncategorized", "code": "unassigned", "cents": unassigned})
    budgets = m.Budget.objects.filter(profile=profile, month=month, state=m.ACTIVE).select_related("category")
    split_totals = {row["code"]: row["cents"] for row in split}
    budget_rows = [{
        "id": budget.pk, "category": budget.category.name, "code": budget.category_id,
        "limit_cents": budget.limit_cents, "spent_cents": split_totals.get(budget.category_id, 0),
        "status": budget_status(split_totals.get(budget.category_id, 0), budget.limit_cents),
    } for budget in budgets.order_by("category__name")]
    return {
        "income_cents": income, "spending_cents": expenses, "net_cents": income - expenses,
        "budget_cents": sum(row["limit_cents"] for row in budget_rows),
        "transaction_count": entries.count(), "category_spending": split, "budgets": budget_rows,
        "open_exceptions": m.ExceptionCase.objects.filter(profile=profile, state="open").count(),
        "recurring_count": m.RecurringSeries.objects.filter(account__profile=profile, state=m.ACTIVE).count(),
    }