from django.urls import path
from ledger import api

urlpatterns = [
    path("api/session/", api.session), path("api/login/", api.sign_in), path("api/logout/", api.sign_out),
    path("api/register/", api.register), path("api/password/", api.change_password),
    path("api/reference/", api.reference), path("api/accounts/", api.create_account), path("api/overview/", api.overview),
    path("api/transactions/", api.entries), path("api/transactions/<str:pk>/", api.entries),
    path("api/budgets/", api.budgets), path("api/budgets/<str:pk>/", api.budgets),
    path("api/recurring/", api.recurring), path("api/recurring/<str:pk>/", api.recurring), path("api/exceptions/", api.cases), path("api/exceptions/<int:pk>/", api.cases),
    path("api/audit/", api.history), path("api/insights/", api.insights), path("api/evaluation/", api.evaluation),
    path("api/rules/", api.rules), path("api/rules/<int:pk>/", api.rules),
    path("api/catalog/<str:resource>/", api.catalog), path("api/catalog/<str:resource>/<str:pk>/", api.catalog),
]