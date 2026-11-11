# Local API

All paths start with `/api/`. GET requests use the session cookie. Mutations use JSON and the `X-CSRFToken` returned by the session or login response. Money is signed integer paise. Errors return an HTTP status and an error message. Page results contain items, total, page and pages.

| Resource | Methods | Parameters or body |
| --- | --- | --- |
| session/ | GET | Authentication, CSRF, user and role |
| login/ | POST | username, password |
| register/ | POST | name, username, password, confirm_password, optional timezone. Creates a customer household and first ledger account, then signs in. |
| password/ | POST | current_password, password, confirm_password. Available to signed-in personal accounts. |
| accounts/ | POST | name, optional opening_cents. Adds a ledger account to the selected writable household. |
| logout/ | POST | Empty JSON object |
| reference/ | GET | Household, accounts, balances, merchants and categories |
| overview/ | GET | Month, totals, trend, category breakdown and recent transactions |
| transactions/ | GET, POST | GET supports q and kind. POST uses account_id, kind, cents, descriptor, occurred_at with offset, optional merchant_id, related_id for refunds, and destination_id for transfers |
| transactions/{id}/ | GET, PATCH, DELETE | PATCH uses category_id. DELETE archives the transaction or transfer pair |
| budgets/ | GET, POST | category_id, limit_cents for create |
| budgets/{id}/ | GET, PATCH, DELETE | limit_cents for update |
| recurring/ | GET, POST | POST recomputes detected series |
| recurring/{id}/ | GET | Member transactions |
| exceptions/ | GET | Household exception queue |
| exceptions/{id}/ | PATCH | note for resolution |
| insights/ | GET | Budget comparison revisions |
| audit/ | GET | Entity IDs, actor, before and after values |
| rules/ | GET, POST | pattern, category_id, optional merchant_id, priority, starts, optional ends |
| rules/{id}/ | PATCH, DELETE | Same fields as create. DELETE archives |
| catalog/{resource}/ | GET, POST | households, accounts, merchants or categories |
| catalog/{resource}/{id}/ | PATCH, DELETE | Update mutable reference fields or archive |
| evaluation/ | GET | Full synthetic generation metrics for staff |

Household routes accept `profile` for staff, with the first active household as default. Customers are scoped to their linked household. Month-based routes accept `month=YYYY-MM`. Paginated routes accept `page`. Reviewers can resolve cases and read financial records. Catalog and rule mutations are administrator-only.