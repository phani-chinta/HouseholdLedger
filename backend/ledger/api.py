import json
import re
from datetime import date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from . import models as m
from . import services as svc

try:
    from rest_framework.decorators import api_view, parser_classes, permission_classes
    from rest_framework.parsers import JSONParser
    from rest_framework.permissions import AllowAny
    from rest_framework.response import Response
except ImportError:
    api_view = None


DEMO_USERNAMES = frozenset({"customer", "analyst", "reviewer", "administrator"})


class APIError(Exception):
    def __init__(self, message, status=400, **details):
        self.body = {"error": message, **details}
        self.status = status


def endpoint(handler):
    def dispatch(request, *args, **kwargs):
        try:
            result = handler(request, *args, **kwargs)
            status = 200
        except APIError as error:
            result, status = error.body, error.status
        except ValidationError as error:
            result, status = {"error": "Validation failed.", "fields": getattr(error, "message_dict", error.messages)}, 400
        except IntegrityError:
            result, status = {"error": "This change conflicts with an existing record."}, 409
        return Response(result, status=status) if api_view else JsonResponse(result, status=status)

    if api_view:
        # Authenticated requests use DRF session authentication and CSRF enforcement.
        view = parser_classes([JSONParser])(permission_classes([AllowAny])(dispatch))
        return api_view(["GET", "POST", "PATCH", "DELETE"])(view)
    return dispatch


def payload(request):
    if request.method == "DELETE":
        return {}
    if api_view:
        data = request.data
    else:
        if not request.body:
            return {}
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise APIError("Send a valid JSON object.")
    if not isinstance(data, dict):
        raise APIError("Send a JSON object.")
    return data


def allow(request, *methods):
    if request.method not in methods:
        raise APIError("Method not allowed.", 405)


def role(user):
    if user.is_superuser or user.groups.filter(name="administrator").exists():
        return "administrator"
    if user.groups.filter(name="reviewer").exists():
        return "reviewer"
    if user.groups.filter(name="analyst").exists():
        return "analyst"
    return "customer"


def identity(request):
    if not request.user.is_authenticated:
        raise APIError("Sign in to continue.", 401)
    return role(request.user)


def visible_profiles(user):
    profiles = m.UserProfile.objects.all()
    if user.username in DEMO_USERNAMES and not user.is_superuser:
        demo_customer = get_user_model().objects.filter(username="customer").values("pk")
        profiles = profiles.filter(Q(user_id__isnull=True) | Q(user_id__in=demo_customer))
    return profiles


def selected_profile(request):
    level = identity(request)
    profiles = visible_profiles(request.user).filter(state=m.ACTIVE).order_by("pk")
    if level == "customer":
        profile = profiles.filter(user=request.user).first()
        if request.GET.get("profile") and (not profile or profile.pk != request.GET["profile"]):
            raise APIError("That household is unavailable.", 404)
    else:
        key = request.GET.get("profile")
        profile = profiles.filter(pk=key).first() if key else profiles.first()
    if not profile:
        raise APIError("No active household was found.", 404)
    return profile, level


def find(query, key, message="Record not found."):
    row = query.filter(pk=key).first()
    if row is None:
        raise APIError(message, 404)
    return row


def field_map(row, *names):
    return {name: getattr(row, name) for name in names}


def page(query, request, serialize):
    paginator = Paginator(query, 20)
    result = paginator.get_page(request.GET.get("page", 1))
    return {"items": [serialize(row) for row in result], "total": paginator.count,
            "page": result.number, "pages": paginator.num_pages}


def selected_month(request):
    try:
        month = date.fromisoformat(request.GET.get("month", "2026-11") + "-01")
        if not 2000 <= month.year <= 2099:
            raise ValueError
        return month
    except ValueError:
        raise APIError("Use YYYY-MM for month.")


def whole_cents(value, name, minimum=None):
    if type(value) is not int or abs(value) > 100_000_000_000 or (minimum is not None and value < minimum):
        raise APIError(f"{name} must be a valid integer in paise.")
    return value


def text_field(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise APIError(f"{label} must contain 1–{maximum} characters.")
    return value.strip()


def date_field(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise APIError(f"{label} must be a valid date.")


def save(row):
    row.full_clean()
    row.save()


def session_data(request, user):
    return {
        "authenticated": True, "csrf": get_token(request), "username": user.username,
        "role": role(user), "name": user.first_name or user.username,
        "demo": user.username in DEMO_USERNAMES,
    }


def require_csrf(request):
    raw = getattr(request, "_request", request)
    middleware = CsrfViewMiddleware(lambda _: None)
    if middleware.process_view(raw, lambda _: None, (), {}):
        raise APIError("Your session token expired. Refresh the page and try again.", 403)
    return raw


def checked_password(data, user):
    password = data.get("password")
    if not isinstance(password, str) or not password or len(password) > 128:
        raise APIError("Enter a password of 10 to 128 characters.")
    if password != data.get("confirm_password"):
        raise APIError("The passwords do not match.")
    validate_password(password, user=user)
    return password


@endpoint
def session(request):
    allow(request, "GET")
    if not request.user.is_authenticated:
        return {"authenticated": False, "csrf": get_token(request), "registration": True}
    return session_data(request, request.user)


@endpoint
def sign_in(request):
    allow(request, "POST")
    raw = require_csrf(request)
    data = payload(request)
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise APIError("Enter a username and password.")
    username = username.strip()
    if not username or len(username) > 150 or not password or len(password) > 1024:
        raise APIError("Username or password is incorrect.", 401)
    user = authenticate(raw, username=username, password=password)
    if not user and username.lower() != username:
        user = authenticate(raw, username=username.lower(), password=password)
    if not user:
        raise APIError("Username or password is incorrect.", 401)
    login(raw, user)
    return session_data(raw, user)


@endpoint
def register(request):
    allow(request, "POST")
    raw = require_csrf(request)
    if request.user.is_authenticated:
        raise APIError("Sign out before creating another account.", 409)
    data = payload(request)
    name = text_field(data.get("name"), "Name", 100)
    username = text_field(data.get("username"), "Username", 30).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,29}", username):
        raise APIError("Use 3 to 30 letters, numbers, periods, underscores or hyphens for your username. Start with a letter or number.")
    zone = text_field(data.get("timezone", "Asia/Kolkata"), "Timezone", 64)
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise APIError("Choose a valid timezone.")
    users = get_user_model()
    if username in DEMO_USERNAMES or users.objects.filter(username__iexact=username).exists():
        raise APIError("That username is already in use.", 409)
    user = users(username=username, first_name=name)
    password = checked_password(data, user)
    user.set_password(password)
    try:
        with transaction.atomic():
            user.save()
            profile = m.UserProfile.objects.create(
                id=f"USR-{svc.allocate('USR'):06d}", user=user, name=name, timezone=zone,
            )
            if not m.Category.objects.exists():
                m.Category.objects.bulk_create([
                    m.Category(code=code, name=label, kind=kind)
                    for code, label, kind in svc.STARTER_CATEGORIES
                ])
            account = m.Account.objects.create(id=f"ACC-{profile.pk}-01", profile=profile, name="Everyday account")
            svc.event(profile, "created", profile, user, after={"name": name, "timezone": zone})
            svc.event(account, "created", profile, user, after={"name": account.name, "opening_cents": 0})
    except IntegrityError:
        if users.objects.filter(username__iexact=username).exists():
            raise APIError("That username is already in use.", 409)
        raise
    login(raw, user)
    return session_data(raw, user)


@endpoint
def change_password(request):
    allow(request, "POST")
    identity(request)
    raw = require_csrf(request)
    data = payload(request)
    current_password = data.get("current_password")
    if not isinstance(current_password, str) or len(current_password) > 1024:
        raise APIError("Enter your current password.")
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if user.username in DEMO_USERNAMES:
            raise APIError("The shared demo accounts have fixed credentials.", 403)
        if not user.check_password(current_password):
            raise APIError("The current password is incorrect.")
        password = checked_password(data, user)
        if password == current_password:
            raise APIError("Choose a different password.")
        user.set_password(password)
        user.save(update_fields=["password"])
    update_session_auth_hash(raw, user)
    return session_data(raw, user)


@endpoint
def sign_out(request):
    allow(request, "POST")
    identity(request)
    raw = getattr(request, "_request", request)
    logout(raw)
    return {"authenticated": False, "csrf": get_token(raw)}


@endpoint
def reference(request):
    allow(request, "GET")
    profile, level = selected_profile(request)
    profiles = visible_profiles(request.user).filter(state=m.ACTIVE).order_by("pk")
    if level == "customer":
        profiles = profiles.filter(pk=profile.pk)
    accounts = m.Account.objects.filter(profile=profile, state=m.ACTIVE).order_by("pk")
    balances = {item["account_id"]: item["total"] for item in svc.ledger(profile).values("account_id").annotate(total=Sum("cents"))}
    return {
        "profile": field_map(profile, "id", "name", "timezone"),
        "profiles": list(profiles.values("id", "name")),
        "accounts": [{**field_map(row, "id", "name", "opening_cents"), "balance_cents": row.opening_cents + balances.get(row.pk, 0)} for row in accounts],
        "categories": list(m.Category.objects.filter(state=m.ACTIVE).order_by("name").values("code", "name", "kind")),
        "merchants": list(m.Merchant.objects.filter(state=m.ACTIVE).order_by("name").values("id", "name")),
    }


def next_account_id(profile):
    count = profile.account_set.count()
    if count >= 99:
        raise APIError("The household has reached its account limit.")
    return f"ACC-{profile.pk}-{count + 1:02d}"


@endpoint
def create_account(request):
    allow(request, "POST")
    profile, level = selected_profile(request)
    if level == "reviewer":
        raise APIError("Reviewers have read-only account access.", 403)
    data = payload(request)
    name = text_field(data.get("name"), "Account name", 80)
    opening = whole_cents(data.get("opening_cents", 0), "Opening balance")
    with transaction.atomic():
        profile = m.UserProfile.objects.select_for_update().get(pk=profile.pk)
        if profile.account_set.filter(name__iexact=name, state=m.ACTIVE).exists():
            raise APIError("An active account already uses that name.", 409)
        account = m.Account.objects.create(id=next_account_id(profile), profile=profile, name=name, opening_cents=opening)
        svc.event(account, "created", profile, request.user, after={"name": name, "opening_cents": opening})
    return field_map(account, "id", "name", "opening_cents")


@endpoint
def overview(request):
    allow(request, "GET")
    profile, _ = selected_profile(request)
    month = selected_month(request)
    result = svc.dashboard(profile, month)
    trend = []
    for offset in range(5, -1, -1):
        index = month.year * 12 + month.month - 1 - offset
        start = date(index // 12, index % 12 + 1, 1)
        totals = {item["kind"]: item["total"] for item in svc.month_entries(profile, start).values("kind").annotate(total=Sum("cents"))}
        trend.append({"month": start.strftime("%Y-%m"), "income_cents": totals.get("income", 0),
                      "spending_cents": -totals.get("expense", 0) - totals.get("refund", 0)})
    result["trend"] = trend
    result["recent"] = [entry_json(row) for row in svc.month_entries(profile, month).select_related("merchant", "category", "account").order_by("-occurred_at", "-pk")[:6]]
    return result


def entry_json(row):
    return {
        **field_map(row, "id", "descriptor", "local_date", "occurred_at", "cents", "kind",
                    "decision", "rule_id", "related_id", "merchant_id", "account_id", "category_id"),
        "merchant": row.merchant.name if row.merchant else None, "account": row.account.name,
        "category": row.category.name if row.category else None,
    }


@endpoint
def entries(request, pk=None):
    profile, level = selected_profile(request)
    query = svc.ledger(profile).select_related("merchant", "category", "account")
    if request.method == "GET":
        if pk:
            return entry_json(find(query, pk))
        search = request.GET.get("q", "").strip()
        kind = request.GET.get("kind")
        query = query.filter(local_date__gte=selected_month(request), local_date__lt=svc.next_month(selected_month(request)))
        if search:
            query = query.filter(Q(descriptor__icontains=search) | Q(merchant__name__icontains=search) | Q(pk__icontains=search))
        if kind:
            query = query.filter(kind=kind)
        return page(query.order_by("-occurred_at", "-pk"), request, entry_json)
    if level == "reviewer":
        raise APIError("Reviewers have read-only ledger access.", 403)
    allow(request, "POST" if pk is None else "PATCH", "POST" if pk is None else "DELETE")
    data = payload(request)
    with transaction.atomic():
        m.UserProfile.objects.select_for_update().get(pk=profile.pk)
        if pk:
            row = find(query, pk)
            previous = {"category": row.category_id, "decision": row.decision, "state": row.state}
            if request.method == "DELETE":
                if row.kind == "transfer":
                    anchor = find(query.filter(kind="transfer"), row.related_id) if row.related_id else row
                    pair = [anchor, *anchor.transaction_set.filter(state=m.ACTIVE, kind="transfer")]
                    if len(pair) != 2 or sum(entry.cents for entry in pair):
                        raise APIError("This transfer needs review before it can be archived.", 409)
                    for leg in pair:
                        leg.state = m.ARCHIVED
                        leg.save(update_fields=["state"])
                        svc.event(leg, "archived", profile, request.user, {"state": m.ACTIVE},
                                  {"state": m.ARCHIVED, "transfer": anchor.pk})
                    row.state = m.ARCHIVED
                else:
                    if row.transaction_set.filter(state=m.ACTIVE).exists():
                        raise APIError("Archive linked refunds before archiving the original expense.", 409)
                    row.state = m.ARCHIVED
                    row.save(update_fields=["state"])
                    svc.event(row, "archived", profile, request.user, previous, {"state": row.state})
            else:
                if row.kind not in ("income", "expense"):
                    raise APIError("Linked refunds and transfers derive their categories.")
                category = find(m.Category.objects.filter(state=m.ACTIVE, kind=row.kind),
                                data.get("category_id"), "Choose a compatible category.")
                row.category, row.rule, row.decision = category, None, "manual"
                row.save(update_fields=["category", "rule", "decision"])
                svc.event(row, "categorized", profile, request.user, previous,
                          {"category": category.pk, "decision": row.decision})
                for refund in row.transaction_set.filter(state=m.ACTIVE, kind="refund"):
                    old = {"category": refund.category_id, "decision": refund.decision}
                    refund.category, refund.decision = category, "linked_refund"
                    refund.save(update_fields=["category", "decision"])
                    svc.event(refund, "categorized", profile, request.user, old,
                              {"category": category.pk, "decision": refund.decision})
                    svc.refresh_insights(profile, svc.month_start(refund.local_date), request.user)
        else:
            account = find(m.Account.objects.filter(profile=profile, state=m.ACTIVE),
                           data.get("account_id"), "Choose an active account.")
            kind = data.get("kind")
            if not isinstance(kind, str) or kind not in dict(m.KINDS):
                raise APIError("Choose a transaction type.")
            cents = whole_cents(data.get("cents"), "Amount")
            if not cents or (kind == "expense" and cents > 0) or (kind != "expense" and cents < 0):
                raise APIError("Enter a negative expense or a positive income, refund or transfer amount.")
            try:
                stamp = parse_datetime(data.get("occurred_at", ""))
            except (TypeError, ValueError):
                stamp = None
            if not stamp or is_naive(stamp) or not 2000 <= stamp.year <= 2099:
                raise APIError("Enter a date and time with a timezone between 2000 and 2099.")
            merchant = find(m.Merchant.objects.filter(state=m.ACTIVE), data["merchant_id"]) if data.get("merchant_id") else None
            related = None
            if kind == "refund":
                related = find(query.filter(kind="expense", account=account), data.get("related_id"), "Link the original expense in this account.")
                credited = related.transaction_set.filter(state=m.ACTIVE, kind="refund").aggregate(total=Sum("cents"))["total"] or 0
                if stamp < related.occurred_at or cents + credited > -related.cents:
                    raise APIError("Refunds must follow the purchase and cannot exceed its unrefunded amount.")
            stamp = stamp.astimezone(timezone.utc)
            prefix = f"TXN{stamp.year}"
            row = m.Transaction(id=f"TXN-{stamp.year}-{svc.allocate(prefix):08d}", account=account, merchant=merchant,
                                descriptor=text_field(data.get("descriptor"), "Description", 120), kind=kind,
                                cents=-cents if kind == "transfer" else cents, related=related, occurred_at=stamp)
            svc.localize(row, profile)
            rules = svc.active_rules()
            if not row.merchant_id and kind in ("expense", "income"):
                row.merchant_id = svc.infer_merchant(row, rules)
            svc.apply_decision(row, rules)
            save(row)
            svc.event(row, "created", profile, request.user, after={
                "cents": row.cents, "decision": row.decision, "category": row.category_id, "rule": row.rule_id,
                "account": row.account_id, "merchant": row.merchant_id, "related": row.related_id,
                "descriptor": row.descriptor, "occurred_at": row.occurred_at.isoformat(), "local_date": str(row.local_date),
            })
            svc.inspect_entry(row, profile)
            if kind == "transfer":
                destination = find(m.Account.objects.filter(profile=profile, state=m.ACTIVE).exclude(pk=account.pk),
                                   data.get("destination_id"), "Choose a different destination account.")
                credit = m.Transaction(id=f"TXN-{stamp.year}-{svc.allocate(prefix):08d}", account=destination,
                                       descriptor=row.descriptor, kind=kind, cents=cents, related=row,
                                       occurred_at=stamp, local_date=row.local_date)
                svc.apply_decision(credit, [])
                save(credit)
                svc.event(credit, "created", profile, request.user, after={"cents": cents, "paired_with": row.pk})
        svc.refresh_insights(profile, svc.month_start(row.local_date), request.user)
        if row.kind in ("expense", "income"):
            svc.refresh_recurrence(profile, request.user)
        return entry_json(row)


@endpoint
def budgets(request, pk=None):
    profile, level = selected_profile(request)
    month = selected_month(request)
    if request.method == "GET":
        items = svc.dashboard(profile, month)["budgets"]
        if pk:
            for item in items:
                if item["id"] == pk:
                    return item
            raise APIError("Budget not found.", 404)
        return {"items": items}
    if level == "reviewer":
        raise APIError("Reviewers have read-only budget access.", 403)
    allow(request, "POST" if pk is None else "PATCH", "POST" if pk is None else "DELETE")
    data = payload(request)
    error = None
    with transaction.atomic():
        m.UserProfile.objects.select_for_update().get(pk=profile.pk)
        if pk:
            row = find(m.Budget.objects.filter(profile=profile, state=m.ACTIVE), pk)
            before = field_map(row, "limit_cents", "state")
        else:
            code = text_field(data.get("category_id"), "Category", 24)
            category = m.Category.objects.filter(pk=code, kind="expense", state=m.ACTIVE).first()
            if not category:
                svc.open_issue(profile, "invalid_budget_category", f"budget:{profile.pk}:{month:%Y-%m}:{code}",
                               {"message": "Budget rejected because the category is outside the active expense taxonomy.", "code": code, "month": month.strftime("%Y-%m")})
                error = APIError("Choose an active expense category.")
            else:
                row = m.Budget.objects.filter(profile=profile, category=category, month=month).first()
                before = field_map(row, "limit_cents", "state") if row else {}
                if row and row.state == m.ACTIVE:
                    raise APIError("This category already has a budget for the selected month.", 409)
                row = row or m.Budget(id=f"BUD-{profile.pk}-{month:%Y-%m}-{code}", profile=profile,
                                      category=category, month=month)
        if not error:
            row.state = m.ARCHIVED if request.method == "DELETE" else m.ACTIVE
            if row.state == m.ACTIVE:
                row.limit_cents = whole_cents(data.get("limit_cents"), "Budget limit", 1)
            save(row)
            after = field_map(row, "limit_cents", "state")
            svc.event(row, {"POST": "created" if not before else "restored", "PATCH": "updated",
                            "DELETE": "archived"}[request.method], profile, request.user, before, after)
            svc.refresh_insights(profile, row.month, request.user)
    if error:
        raise error
    return {"id": row.pk, **after}


@endpoint
def recurring(request, pk=None):
    profile, level = selected_profile(request)
    allow(request, "GET", "POST")
    if pk:
        allow(request, "GET")
        series = find(m.RecurringSeries.objects.filter(account__profile=profile, state=m.ACTIVE), pk)
        rows = series.transactions.select_related("merchant", "category", "account").order_by("local_date")
        return {"name": series.name, "items": [entry_json(row) for row in rows]}
    if request.method == "POST":
        if level == "reviewer":
            raise APIError("Reviewers have read-only access to detection.", 403)
        svc.refresh_recurrence(profile, request.user)
    rows = m.RecurringSeries.objects.filter(account__profile=profile, state=m.ACTIVE)
    from django.db.models import Count
    rows = rows.annotate(count=Count("recurringoccurrence")).order_by("next_date", "pk")
    return {"items": [field_map(row, "id", "name", "typical_cents", "first_date", "last_date", "next_date", "missing_months", "count") for row in rows]}


@endpoint
def cases(request, pk=None):
    profile, level = selected_profile(request)
    query = m.ExceptionCase.objects.filter(profile=profile).order_by("state", "-pk")
    if request.method == "GET":
        return page(query, request, lambda row: field_map(row, "id", "issue_type", "details", "state", "transaction_id", "series_id", "created_at"))
    allow(request, "PATCH")
    if level not in ("reviewer", "analyst", "administrator"):
        raise APIError("An analyst, reviewer or administrator can resolve exceptions.", 403)
    data = payload(request)
    note = text_field(data.get("note"), "Resolution note", 240)
    with transaction.atomic():
        row = find(query.select_for_update(), pk)
        if row.state != "open":
            raise APIError("This exception has already been resolved.", 409)
        row.state = "resolved"
        row.save(update_fields=["state"])
        svc.event(row, "resolved", profile, request.user, {"state": "open"}, {"state": "resolved", "note": note})
    return {"id": row.pk, "state": row.state}


@endpoint
def history(request):
    allow(request, "GET")
    profile, level = selected_profile(request)
    query = m.LedgerEvent.objects.filter(profile=profile)
    if level != "customer":
        query = m.LedgerEvent.objects.filter(Q(profile=profile) | Q(profile__isnull=True))
    query = query.select_related("actor").order_by("-created_at", "-pk")
    return page(query, request, lambda row: {**field_map(row, "entity", "entity_id", "action", "before", "after", "created_at"),
                                             "actor": row.actor.username if row.actor else "System"})


@endpoint
def insights(request):
    allow(request, "GET")
    profile, _ = selected_profile(request)
    rows = m.Insight.objects.filter(profile=profile, budget__month=selected_month(request)).select_related("budget__category").order_by("-created_at", "-pk")
    return page(rows, request, lambda row: {**field_map(row, "id", "revision", "spent_cents", "limit_cents", "status", "created_at"), "category": row.budget.category.name})


@endpoint
def evaluation(request):
    allow(request, "GET")
    if identity(request) == "customer":
        raise APIError("Evaluation is available to review and operations staff.", 403)
    report = Path(__file__).resolve().parents[2] / "docs" / "validation-full.json"
    if not report.exists():
        return {"available": False}
    return {"available": True, **json.loads(report.read_text(encoding="utf-8"))}

@endpoint
def rules(request, pk=None):
    level = identity(request)
    if level == "customer":
        raise APIError("Rules are available to staff.", 403)
    query = m.CategoryRule.objects.select_related("category", "merchant").order_by("priority", "pk")
    def serialize(row):
        return field_map(row, "id", "pattern", "category_id", "merchant_id", "priority", "starts", "ends", "state")
    if request.method == "GET":
        return page(query, request, serialize)
    if level != "administrator":
        raise APIError("Only administrators can change rules.", 403)
    allow(request, "POST" if pk is None else "PATCH", "POST" if pk is None else "DELETE")
    data = payload(request)
    with transaction.atomic():
        row = find(m.CategoryRule.objects.select_for_update(), pk) if pk else m.CategoryRule()
        before = serialize(row) if pk else {}
        if request.method == "DELETE":
            row.state = m.ARCHIVED
        else:
            row.pattern = text_field(data.get("pattern"), "Pattern", 100)
            if not svc.normalize(row.pattern):
                raise APIError("Enter at least one letter or digit in the pattern.")
            row.category = find(m.Category.objects.filter(state=m.ACTIVE).exclude(kind__in=["transfer", "refund"]), data.get("category_id"))
            row.merchant = find(m.Merchant.objects.filter(state=m.ACTIVE), data["merchant_id"]) if data.get("merchant_id") else None
            row.priority = whole_cents(data.get("priority"), "Priority", 0)
            if row.priority > 10000:
                raise APIError("Priority must be between 0 and 10000.")
            row.starts = date_field(data.get("starts"), "Effective start")
            row.ends = date_field(data["ends"], "Effective end") if data.get("ends") else None
            row.state = m.ACTIVE
        save(row)
        after = serialize(row)
        def json_dates(values):
            return {key: str(value) if isinstance(value, date) else value for key, value in values.items()}
        svc.event(row, {"POST": "created", "PATCH": "updated", "DELETE": "archived"}[request.method],
                  actor=request.user, before=json_dates(before), after=json_dates(after))
    return after


CATALOG = {
    "households": (m.UserProfile, ("id", "name", "timezone", "state")),
    "accounts": (m.Account, ("id", "name", "profile_id", "opening_cents", "state")),
    "merchants": (m.Merchant, ("id", "name", "state")),
    "categories": (m.Category, ("code", "name", "kind", "state")),
}


@endpoint
def catalog(request, resource, pk=None):
    if identity(request) != "administrator":
        raise APIError("Only administrators can manage reference data.", 403)
    if resource not in CATALOG:
        raise APIError("Unknown catalog.", 404)
    model, fields = CATALOG[resource]
    profiles = visible_profiles(request.user)
    query = model.objects.all()
    if model is m.UserProfile:
        query = profiles
    elif model is m.Account:
        query = query.filter(profile__in=profiles)
    query = query.order_by("pk")
    if request.method == "GET":
        return page(query, request, lambda row: field_map(row, *fields))
    allow(request, "POST" if pk is None else "PATCH", "POST" if pk is None else "DELETE")
    data = payload(request)
    with transaction.atomic():
        row = find(query.select_for_update(), pk) if pk else model()
        before = field_map(row, *fields) if pk else {}
        if request.method == "DELETE":
            if model is m.Category and pk == "transfer":
                raise APIError("The transfer category is required by the ledger.")
            if model is m.Account and row.transaction_set.filter(state=m.ACTIVE).exists():
                raise APIError("Archive active transactions before archiving this account.", 409)
            if model is m.UserProfile and row.account_set.filter(state=m.ACTIVE).exists():
                raise APIError("Archive active accounts before archiving this household.", 409)
            row.state = m.ARCHIVED
        else:
            row.name = text_field(data.get("name"), "Name", 80)
            if model is m.Category:
                row.kind = row.kind if pk else data.get("kind")
                if not pk:
                    row.pk = text_field(data.get("code"), "Category code", 24).lower()
            elif model is m.UserProfile:
                if not pk:
                    row.pk = f"USR-{svc.allocate('USR'):06d}"
                zone = text_field(data.get("timezone"), "Timezone", 64)
                try:
                    ZoneInfo(zone)
                except (ValueError, ZoneInfoNotFoundError):
                    raise APIError("Enter an IANA timezone such as Asia/Kolkata.")
                if pk and zone != row.timezone and svc.ledger(row).exists():
                    raise APIError("The posting timezone is locked after the first ledger entry.", 409)
                row.timezone = zone
            elif model is m.Account and not pk:
                row.profile = find(profiles.select_for_update().filter(state=m.ACTIVE), data.get("profile_id"))
                row.pk = next_account_id(row.profile)
                row.opening_cents = whole_cents(data.get("opening_cents", 0), "Opening balance")
            elif model is m.Merchant and not pk:
                candidate = svc.allocate("MRC")
                while model.objects.filter(pk=f"MRC-{candidate:04d}").exists():
                    candidate = svc.allocate("MRC")
                row.pk = f"MRC-{candidate:04d}"
        save(row)
        after = field_map(row, *fields)
        profile = row if model is m.UserProfile else row.profile if model is m.Account else None
        svc.event(row, {"POST": "created", "PATCH": "updated", "DELETE": "archived"}[request.method],
                  profile, request.user, before, after)
    return after
