from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

ACTIVE = "active"
ARCHIVED = "archived"
STATES = [(ACTIVE, "Active"), (ARCHIVED, "Archived")]
KINDS = [(kind, kind.title()) for kind in ("expense", "income", "transfer", "refund")]


class UserProfile(models.Model):
    id = models.CharField(primary_key=True, max_length=12)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)


class Account(models.Model):
    id = models.CharField(primary_key=True, max_length=24)
    profile = models.ForeignKey(UserProfile, on_delete=models.PROTECT)
    name = models.CharField(max_length=80)
    opening_cents = models.BigIntegerField(default=0)
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)


class Merchant(models.Model):
    id = models.CharField(primary_key=True, max_length=16)
    name = models.CharField(max_length=100, unique=True)
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)


class Category(models.Model):
    code = models.SlugField(primary_key=True, max_length=24)
    name = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=8, choices=KINDS)
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)


class CategoryRule(models.Model):
    pattern = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    merchant = models.ForeignKey(Merchant, null=True, blank=True, on_delete=models.PROTECT)
    priority = models.PositiveIntegerField(default=100)
    starts = models.DateField()
    ends = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(ends__isnull=True) | Q(ends__gte=models.F("starts")), name="rule_dates_ordered")]


class Transaction(models.Model):
    id = models.CharField(primary_key=True, max_length=24)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    merchant = models.ForeignKey(Merchant, null=True, blank=True, on_delete=models.PROTECT)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT)
    rule = models.ForeignKey(CategoryRule, null=True, blank=True, on_delete=models.PROTECT)
    related = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    descriptor = models.CharField(max_length=120)
    occurred_at = models.DateTimeField()
    local_date = models.DateField()
    cents = models.BigIntegerField()
    kind = models.CharField(max_length=8, choices=KINDS)
    decision = models.CharField(max_length=20, default="unmatched")
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)

    class Meta:
        indexes = [models.Index(fields=["account", "local_date", "state"])]
        constraints = [
            models.CheckConstraint(condition=Q(kind="expense", cents__lt=0) | Q(kind__in=["income", "refund"], cents__gt=0) | (Q(kind="transfer") & ~Q(cents=0)), name="transaction_signed_amount"),
        ]


class Budget(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    profile = models.ForeignKey(UserProfile, on_delete=models.PROTECT)
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    month = models.DateField()
    limit_cents = models.BigIntegerField(validators=[MinValueValidator(1)])
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "category", "month"], name="unique_monthly_budget"),
            models.CheckConstraint(condition=Q(limit_cents__gt=0), name="budget_limit_positive"),
        ]


class RecurringSeries(models.Model):
    id = models.CharField(primary_key=True, max_length=24)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    merchant = models.ForeignKey(Merchant, null=True, blank=True, on_delete=models.PROTECT)
    key = models.CharField(max_length=160)
    name = models.CharField(max_length=120)
    typical_cents = models.BigIntegerField()
    first_date = models.DateField()
    last_date = models.DateField()
    next_date = models.DateField()
    missing_months = models.JSONField(default=list, blank=True)
    transactions = models.ManyToManyField(Transaction, through="RecurringOccurrence")
    state = models.CharField(max_length=8, choices=STATES, default=ACTIVE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "key"], name="unique_recurring_key")]


class RecurringOccurrence(models.Model):
    series = models.ForeignKey(RecurringSeries, on_delete=models.PROTECT)
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["series", "transaction"], name="unique_occurrence")]


class Insight(models.Model):
    profile = models.ForeignKey(UserProfile, on_delete=models.PROTECT)
    budget = models.ForeignKey(Budget, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    spent_cents = models.BigIntegerField()
    limit_cents = models.BigIntegerField()
    status = models.CharField(max_length=12, choices=[("over", "Over"), ("near", "Near"), ("on_track", "On track"), ("archived", "Archived")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["budget", "revision"], name="unique_insight_revision")]


class ExceptionCase(models.Model):
    profile = models.ForeignKey(UserProfile, on_delete=models.PROTECT)
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.PROTECT)
    series = models.ForeignKey(RecurringSeries, null=True, blank=True, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=160, unique=True)
    issue_type = models.CharField(max_length=32)
    details = models.JSONField(default=dict)
    state = models.CharField(max_length=12, choices=[("open", "Open"), ("resolved", "Resolved")], default="open")
    created_at = models.DateTimeField(auto_now_add=True)


class LedgerEvent(models.Model):
    profile = models.ForeignKey(UserProfile, null=True, blank=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    entity = models.CharField(max_length=32)
    entity_id = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Sequence(models.Model):
    prefix = models.CharField(primary_key=True, max_length=12)
    value = models.PositiveBigIntegerField(default=0)