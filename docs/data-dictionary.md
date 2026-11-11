# Household Ledger data dictionary

All rows use immutable primary keys. Amounts are signed integer paise in INR unless a field is described as positive. Dates use ISO 8601. Effective dates are inclusive. The source year is 2026. PostgreSQL enforces primary keys, foreign keys, unique budgets and series membership, rule date ordering, and signed amounts. All relationships use protected deletion. The application archives records and writes audit events when lifecycle or decision fields change.

## Relationships

| Parent | Child | Cardinality and optionality |
| --- | --- | --- |
| Authentication user | UserProfile | Optional 1:1 on the profile. Generated customers can exist without sign-in credentials. |
| UserProfile | Account, Budget, Insight, ExceptionCase | 1:N, required on each child |
| Account | Transaction, RecurringSeries | 1:N, required on each child |
| Merchant | Transaction, CategoryRule, RecurringSeries | 1:N, optional on each child |
| Category | CategoryRule, Budget | 1:N, required on each child |
| Category | Transaction | 1:N, optional on a transaction pending review |
| CategoryRule | Transaction | 1:N, optional. Present on a rule-based assignment |
| Transaction | Transaction | 1:N self-reference, optional for linked refund or transfer credit |
| RecurringSeries | Transaction | N:M through RecurringOccurrence |
| Budget | Insight | 1:N, required, revisioned comparisons |
| Transaction, RecurringSeries | ExceptionCase | 1:N, optional evidence on each case |
| UserProfile, authentication user | LedgerEvent | 1:N, optional. Null profile is global reference data. Null actor is the system. |

## UserProfile

| Field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| id | String ≤12, required | Unique PK, USR-000001 pattern |
| user_id | Big integer FK, optional | Unique linked authentication user |
| name | String ≤100, required | Nonempty display name, repetitions allowed |
| timezone | String ≤64, required | IANA timezone, default Asia/Kolkata. Locked once a ledger exists |
| state | Enum, required | active or archived, defaults to active |

## Account

| Field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| id | String ≤24, required | Unique PK, ACC-USR-000001-01 pattern |
| profile_id | String FK, required | Owning household |
| name | String ≤80, required | Nonempty |
| opening_cents | Big integer, required | Signed paise, default 0. Current balance equals opening balance plus active signed entries |
| state | Enum, required | active or archived. Active transactions prevent archive |

## Merchant and Category

| Entity and field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| Merchant.id | String ≤16, required | Unique PK, MRC-0001 pattern |
| Merchant.name | String ≤100, required | Unique canonical merchant name |
| Merchant.state | Enum, required | active or archived |
| Category.code | Slug ≤24, required | Unique PK, stable taxonomy code |
| Category.name | String ≤80, required | Unique display name |
| Category.kind | Enum, required | expense, income, transfer, or refund. Seeded refund entries inherit expense categories |
| Category.state | Enum, required | active or archived. Transfer remains active |

## CategoryRule

| Field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| id | Big integer, required | Unique PK |
| pattern | String ≤100, required | Normalized token phrase with at least one letter or digit |
| category_id | Slug FK, required | Active expense or income category for new rules |
| merchant_id | String FK, optional | Restricts rule to a canonical merchant when set |
| priority | Nonnegative integer, required | Lower values win. API accepts 0–10,000 |
| starts | Date, required | Inclusive effective start |
| ends | Date, optional | Inclusive end, at or after start |
| state | Enum, required | active or archived |

## Transaction

| Field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| id | String ≤24, required | Unique PK, TXN-2026-00000001 pattern |
| account_id | String FK, required | Account in the selected household |
| merchant_id | String FK, optional | Canonical merchant |
| category_id | Slug FK, optional | Assigned category or null for review |
| rule_id | Big integer FK, optional | Exact rule that assigned the category |
| related_id | String self-FK, optional | Active source purchase for refunds, debit leg for transfer credits |
| descriptor | String ≤120, required | Original statement description, nonempty |
| occurred_at | Aware timestamp, required | Stored in UTC |
| local_date | Date, required | UTC timestamp converted to household timezone |
| cents | Big integer, required | Expense negative, income/refund positive, transfer nonzero |
| kind | Enum, required | expense, income, refund, transfer |
| decision | String ≤20, required | rule, manual, ambiguous, unmatched, linked_refund, unmatched_refund, excluded_transfer |
| state | Enum, required | active or archived |

## Budget

| Field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| id | String ≤64, required | Unique PK, BUD-USR-000001-2026-01-groceries pattern |
| profile_id | String FK, required | Owning household |
| category_id | Slug FK, required | Active expense category at creation or restore |
| month | Date, required | First of month. Unique together with profile and category |
| limit_cents | Big integer, required | Positive paise |
| state | Enum, required | active or archived. Recreating the same key restores it and records an event |

## RecurringSeries and RecurringOccurrence

| Entity and field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| RecurringSeries.id | String ≤24, required | Unique PK, REC-000001 pattern |
| RecurringSeries.account_id | String FK, required | Owning account |
| RecurringSeries.merchant_id | String FK, optional | Canonical merchant if present |
| RecurringSeries.key | String ≤160, required | Kind and canonical merchant or normalized descriptor. Unique within account |
| RecurringSeries.name | String ≤120, required | Merchant or description |
| RecurringSeries.typical_cents | Big integer, required | Signed median amount from eligible months |
| RecurringSeries.first_date | Date, required | Earliest retained occurrence |
| RecurringSeries.last_date | Date, required | Latest retained occurrence, on or after first |
| RecurringSeries.next_date | Date, required | Next calendar month and typical posting day, clipped to month length |
| RecurringSeries.missing_months | JSON array, required | Distinct YYYY-MM strings between first and last with no retained occurrence |
| RecurringSeries.state | Enum, required | active or archived |
| RecurringOccurrence.id | Big integer, required | Unique PK |
| RecurringOccurrence.series_id | String FK, required | Detected series |
| RecurringOccurrence.transaction_id | String FK, required | Member transaction from the same account. Unique with series_id |

## Insight, ExceptionCase and LedgerEvent

| Entity and field | Type and requirement | Values, uniqueness, validation |
| --- | --- | --- |
| Insight.id | Big integer, required | Unique PK |
| Insight.profile_id | String FK, required | Same household as budget |
| Insight.budget_id | String FK, required | Compared budget |
| Insight.revision | Positive integer, required | Monotonic within budget. Unique with budget_id |
| Insight.spent_cents | Big integer, required | Net category spending for budget month |
| Insight.limit_cents | Big integer, required | Positive budget limit at this revision |
| Insight.status | Enum, required | on_track, near, over, archived |
| Insight.created_at | UTC timestamp, required | System generated |
| ExceptionCase.id | Big integer, required | Unique PK |
| ExceptionCase.profile_id | String FK, required | Household under review |
| ExceptionCase.transaction_id | String FK, optional | Evidence from same household |
| ExceptionCase.series_id | String FK, optional | Evidence from same household |
| ExceptionCase.fingerprint | String ≤160, required | Unique issue identity for idempotent detection |
| ExceptionCase.issue_type | String ≤32, required | ambiguous, unmatched, unmatched_refund, transfer_duplicate, timezone_boundary, recurring_gap, or invalid_budget_category |
| ExceptionCase.details | JSON object, required | Reason, source code, month or other evidence |
| ExceptionCase.state | Enum, required | open → resolved by authorized staff, with resolution event |
| ExceptionCase.created_at | UTC timestamp, required | System generated |
| LedgerEvent.id | Big integer, required | Unique PK |
| LedgerEvent.profile_id | String FK, optional | Household scope |
| LedgerEvent.actor_id | Big integer FK, optional | Authentication user. System events are null |
| LedgerEvent.entity | String ≤32, required | Entity model name |
| LedgerEvent.entity_id | String ≤64, required | Historical entity identifier scoped by entity name |
| LedgerEvent.action | String ≤32, required | Recorded action such as created, categorized, derived, resolved or archived |
| LedgerEvent.before | JSON object, required | Prior tracked fields |
| LedgerEvent.after | JSON object, required | New tracked fields |
| LedgerEvent.created_at | UTC timestamp, required | System generated |
| Sequence.prefix | String ≤12, required | Unique allocation key |
| Sequence.value | Nonnegative big integer, required | Latest issued number. PostgreSQL row lock protects increments |

## Evaluation rules

- **Categorization.** Normalize the descriptor to lowercase alphanumeric tokens. Filter by kind, optional merchant and posting date. Apply the lowest numeric priority. Equal-priority rules assigning different categories produce an exception. Ground truth comes from the merchant scenario used to generate the descriptor. Accuracy counts an unassigned row as incorrect. Coverage counts rows with a category.
- **Recurrence.** Group by account, direction, and canonical merchant or normalized descriptor. Require at least three distinct months with one candidate each, amounts within 15% of the median, and posting days within four days of the median day. Missing interior months are flagged. Precision and recall compare detected member IDs with members labeled by the injected salary, rent and subscription schedules.
- **Budget.** Subtract signed expenses and linked refund credits from zero for category spending. Transfers and income are outside spending. A budget is near at 80–100%, and over above 100%. Revisions persist when figures or lifecycle states change.
- **Reconciliation.** Sum signed active transactions by account and add opening balance. Transfers have two opposite active legs. Synthetic duplicate credits are quarantined with an audit event and a review case. Refunds post to the original category and cannot cumulatively exceed the purchase. Budget totals are recomputed from posting-month entries.
- **Ground truth.** Each transaction has an offline expected category, recurrence membership and expected handling. Additional records identify the deliberately skipped subscription and rejected taxonomy code. The export keeps labels in a standalone CSV, outside the application database and API inputs.
- **Provenance.** The generator uses seed 20261022 by default. Names, merchants and records are synthetic. No external source data is used.