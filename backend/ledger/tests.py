import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Sum
from django.test import Client, TestCase

from . import models as m


class LedgerWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("generate_demo", profiles=2, transactions=160, load=True, stdout=StringIO())

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.session = self.client.get("/api/session/").json()
        self.csrf = self.session["csrf"]

    def request(self, method, path, payload=None, status=200):
        result = getattr(self.client, method)(
            path, data=json.dumps(payload) if payload is not None else None,
            content_type="application/json", HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertEqual(result.status_code, status, result.content[:500])
        return result.json()

    def sign_in(self, role="customer"):
        result = self.request("post", "/api/login/", {"username": role, "password": "LedgerDemo2026!"})
        self.csrf = result["csrf"]
        self.assertEqual(result["role"], role)

    def entry(self, kind="expense", cents=-7800, **changes):
        defaults = {"account_id": "ACC-USR-000001-01", "kind": kind, "cents": cents,
                    "occurred_at": "2026-11-18T14:30:00Z", "descriptor": "UPI / fresh basket / POS"}
        return self.request("post", "/api/transactions/", {**defaults, **changes})

    def test_roles_and_csrf(self):
        self.request("get", "/api/overview/", status=401)
        response = self.client.post("/api/login/", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.sign_in()
        self.request("get", "/api/overview/?profile=USR-000002", status=404)
        self.request("get", "/api/rules/", status=403)
        self.request("post", "/api/logout/", {})
        self.assertFalse(self.client.get("/api/session/").json()["authenticated"])
        self.sign_in("reviewer")
        self.request("get", "/api/overview/?profile=USR-000002")
        self.request("post", "/api/budgets/", {}, status=403)
        self.request("post", "/api/recurring/", {}, status=403)

    def test_signup_after_seeding_is_hidden_from_shared_demo_staff(self):
        password = "Birch!Tides9402"
        created = self.request("post", "/api/register/", {"name": "New owner", "username": "new.owner",
                               "password": password, "confirm_password": password})
        self.csrf = created["csrf"]
        household = self.request("get", "/api/reference/")["profile"]["id"]
        self.assertEqual(household, "USR-000003")
        self.request("post", "/api/logout/", {})
        self.csrf = self.client.get("/api/session/").json()["csrf"]
        self.sign_in("administrator")
        self.request("get", "/api/reference/?profile=" + household, status=404)
        self.assertEqual(len(self.request("get", "/api/catalog/households/")["items"]), 2)
        self.assertEqual(len(self.request("get", "/api/catalog/accounts/")["items"]), 4)
        self.request("post", "/api/catalog/accounts/", {"name": "Blocked", "profile_id": household}, status=404)
        self.request("patch", "/api/catalog/households/" + household + "/",
                     {"name": "Blocked", "timezone": "Asia/Kolkata"}, status=404)

    def test_expense_refund_and_manual_category_trace(self):
        self.sign_in()
        before = self.request("get", "/api/overview/")["spending_cents"]
        expense = self.entry()
        self.assertEqual((expense["category_id"], expense["decision"]), ("groceries", "rule"))
        refund = self.entry("refund", 1800, related_id=expense["id"], descriptor="Reversal")
        self.assertEqual(refund["category_id"], "groceries")
        self.assertEqual(self.request("get", "/api/overview/")["spending_cents"], before + 6000)
        self.request("post", "/api/transactions/", {
            "account_id": "ACC-USR-000001-01", "kind": "refund", "cents": 7000, "descriptor": "Excess refund",
            "occurred_at": "2026-11-19T12:00:00Z", "related_id": expense["id"],
        }, status=400)
        self.request("patch", f"/api/transactions/{expense['id']}/", {"category_id": "dining"})
        result = self.request("get", f"/api/transactions/{refund['id']}/")
        self.assertEqual(result["category_id"], "dining")
        self.request("delete", f"/api/transactions/{expense['id']}/", status=409)
        self.request("delete", f"/api/transactions/{refund['id']}/")
        self.request("delete", f"/api/transactions/{expense['id']}/")
        self.assertEqual(self.request("get", "/api/overview/")["spending_cents"], before)
        self.assertGreater(m.LedgerEvent.objects.filter(entity_id=expense["id"]).count(), 2)

    def test_transfer_pair_and_timezone_boundary(self):
        self.sign_in()
        balances_before = {row["id"]: row["balance_cents"] for row in self.request("get", "/api/reference/")["accounts"]}
        spending_before = self.request("get", "/api/overview/")["spending_cents"]
        debit = self.entry("transfer", 12000, destination_id="ACC-USR-000001-02", descriptor="Savings move")
        self.assertEqual(debit["cents"], -12000)
        credit = m.Transaction.objects.get(related_id=debit["id"], state="active")
        self.assertEqual(credit.cents, 12000)
        self.assertEqual(self.request("get", "/api/overview/")["spending_cents"], spending_before)
        self.request("delete", f"/api/transactions/{credit.pk}/")
        balances_after = {row["id"]: row["balance_cents"] for row in self.request("get", "/api/reference/")["accounts"]}
        self.assertEqual(balances_before, balances_after)
        edge = self.entry(occurred_at="2026-10-31T21:00:00Z")
        self.assertEqual(edge["local_date"], "2026-11-01")
        self.assertTrue(m.ExceptionCase.objects.filter(transaction_id=edge["id"], issue_type="timezone_boundary").exists())
        net = m.Transaction.objects.filter(kind="transfer", state="active").aggregate(net=Sum("cents"))["net"]
        self.assertEqual(net, 0)

    def test_budgets_exception_and_recurring_lifecycle(self):
        self.sign_in("analyst")
        budgets = self.request("get", "/api/budgets/")["items"]
        budget = next(row for row in budgets if row["code"] == "groceries")
        self.request("patch", f"/api/budgets/{budget['id']}/", {"limit_cents": 11100})
        self.request("delete", f"/api/budgets/{budget['id']}/")
        self.request("post", "/api/budgets/", {"category_id": "groceries", "limit_cents": 270000})
        self.assertEqual(m.Insight.objects.filter(budget_id=budget["id"]).count(), 4)
        self.request("post", "/api/budgets/", {"category_id": "old_taxonomy", "limit_cents": 90000}, status=400)
        case = m.ExceptionCase.objects.get(fingerprint="budget:USR-000001:2026-11:old_taxonomy")
        self.request("patch", f"/api/exceptions/{case.pk}/", {"note": "Mapped to the new taxonomy."})
        self.request("patch", f"/api/exceptions/{case.pk}/", {"note": "Duplicate resolution"}, status=409)
        result = self.request("post", "/api/recurring/", {})
        series = next(row for row in result["items"] if row["name"] == "Streamline Plus")
        self.assertEqual(series["missing_months"], ["2026-07"])
        self.assertEqual(len(self.request("get", f"/api/recurring/{series['id']}/")["items"]), 11)
        count = m.RecurringSeries.objects.count()
        self.request("post", "/api/recurring/", {})
        self.assertEqual(m.RecurringSeries.objects.count(), count)

    def test_rule_and_catalog_crud(self):
        self.sign_in("administrator")
        category = self.request("post", "/api/catalog/categories/", {"name": "Repairs", "code": "repairs", "kind": "expense"})
        self.request("patch", "/api/catalog/categories/repairs/", {"name": "Home repairs"})
        merchant = self.request("post", "/api/catalog/merchants/", {"name": "Local Plumber"})
        rule = self.request("post", "/api/rules/", {"pattern": "local plumber", "merchant_id": merchant["id"],
                            "category_id": category["code"], "priority": 15, "starts": "2026-01-01"})
        result = self.entry(descriptor="UPI / Local Plumber / POS", merchant_id=merchant["id"])
        self.assertEqual(result["rule_id"], rule["id"])
        self.request("delete", f"/api/rules/{rule['id']}/")
        self.assertEqual(self.entry(descriptor="Local Plumber", merchant_id=merchant["id"])["decision"], "unmatched")
        self.request("delete", "/api/catalog/categories/repairs/")
        self.assertTrue(m.LedgerEvent.objects.filter(entity="CategoryRule", action="archived").exists())

class CustomerAccountTests(TestCase):
    registration = {"name": "Anjali Rao", "username": "Anjali.Rao", "password": "Tides!Pine9872",
                    "confirm_password": "Tides!Pine9872", "timezone": "Asia/Kolkata", "role": "administrator"}

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        session = self.client.get("/api/session/").json()
        self.assertIs(session["registration"], True)
        self.csrf = session["csrf"]

    def post(self, route, body, expected=200):
        response = self.client.post("/api/" + route + "/", body, content_type="application/json",
                                    HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, expected, response.content[:500])
        result = response.json()
        self.csrf = result.get("csrf", self.csrf)
        return result

    def test_registration_provisions_private_usable_household(self):
        result = self.post("register", self.registration)
        self.assertEqual(result["role"], "customer")
        self.assertEqual(result["username"], "anjali.rao")
        self.assertFalse(result["demo"])
        user = get_user_model().objects.get(username="anjali.rao")
        self.assertTrue(user.check_password(self.registration["password"]))
        self.assertFalse(user.is_staff)
        reference = self.client.get("/api/reference/").json()
        household = reference["profile"]["id"]
        first = reference["accounts"][0]
        self.assertEqual(first["balance_cents"], 0)
        self.assertEqual(len(reference["accounts"]), 1)
        self.assertTrue(any(category["code"] == "groceries" for category in reference["categories"]))
        second = self.post("accounts", {"name": "Savings", "opening_cents": 12500})
        self.assertEqual(second["opening_cents"], 12500)
        self.post("accounts", {"name": "savings"}, 409)
        self.post("budgets", {"category_id": "groceries", "limit_cents": 200000})
        self.post("transactions", {"account_id": first["id"], "kind": "transfer", "cents": 1000,
                                  "destination_id": second["id"], "descriptor": "Savings move",
                                  "occurred_at": "2026-11-19T12:00:00Z"})
        self.assertEqual(self.client.get("/api/overview/").json()["transaction_count"], 2)
        self.assertEqual(m.LedgerEvent.objects.filter(entity="Account", profile_id=household).count(), 2)
        self.post("register", self.registration, 409)
        self.assertEqual(self.client.get("/api/catalog/accounts/").status_code, 403)
        self.post("logout", {})
        self.post("register", {**self.registration, "name": "Another household", "username": "another.user"})
        self.assertEqual(self.client.get("/api/reference/?profile=" + household).status_code, 404)
        self.assertEqual(self.client.get("/api/overview/").json()["transaction_count"], 0)
        self.assertEqual(self.client.get("/api/overview/").json()["budgets"], [])

    def test_signup_validation_leaves_no_partial_data(self):
        denied = self.client.post("/api/register/", self.registration, content_type="application/json")
        self.assertEqual(denied.status_code, 403)
        for overrides, status in [
            ({"username": "AdMiniStrator"}, 409),
            ({"username": "bad name"}, 400),
            ({"password": "password", "confirm_password": "password"}, 400),
            ({"confirm_password": "different"}, 400),
            ({"timezone": "invalid/timezone"}, 400),
        ]:
            self.post("register", {**self.registration, **overrides}, status)
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(m.UserProfile.objects.exists())
        self.assertFalse(m.Account.objects.exists())
        self.post("register", self.registration)
        self.post("logout", {})
        self.post("register", {**self.registration, "username": "ANJALI.RAO"}, 409)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(m.Account.objects.count(), 1)

    def test_password_change_keeps_current_session_and_replaces_old_credential(self):
        self.post("register", self.registration)
        new = "Harbor!Wind9273"
        data = {"current_password": "incorrect", "password": new, "confirm_password": new}
        self.post("password", data, 400)
        self.post("password", {**data, "current_password": self.registration["password"]})
        self.assertTrue(self.client.get("/api/session/").json()["authenticated"])
        self.post("logout", {})
        self.post("login", {"username": "ANJALI.RAO", "password": self.registration["password"]}, 401)
        self.post("login", {"username": "ANJALI.RAO", "password": new})
