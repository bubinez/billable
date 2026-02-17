# API & Models Reference

This document provides a technical reference for the **Universal Billable Module**. It covers the database schema, configuration settings, and REST API specification.

## Configuration (`settings.py`)

The module relies on standard Django settings. 

| Setting | Default | Description |
| :--- | :--- | :--- |
| `INSTALLED_APPS` | - | Must include `"billable"`. |
| `AUTH_USER_MODEL` | `"auth.User"` | The Django user model to link orders and products to. |
| `BILLABLE_API_TOKEN` | `None` | **Required.** Secret token for Bearer authentication in REST API. |
| `BILLABLE_SHOW_DOCS` | `True` | Include OpenAPI docs at `/docs` when the API is mounted. |
| `BILLABLE_API_TITLE` | `"Billable Engine API"` | Title for the OpenAPI schema. |
| `BILLABLE_CURRENCY` | `"USD"` | Default currency code (optional, depends on implementation). |

**Database (PostgreSQL, async):** When using the module in async mode (ASGI, bots), set `CONN_MAX_AGE=0` for the PostgreSQL database in `DATABASES`. Persistent connections (`CONN_MAX_AGE` > 0) are not safe across async event loop context and can cause connection reuse issues; `CONN_MAX_AGE=0` closes the connection after each request/task.

---

## Usage Guidelines

To avoid `AppRegistryNotReady` errors during initialization (e.g., in tests or with `pytest-django`), **do not** import models directly from the `billable` package.

**Correct way to import models:**
```python
from billable.models import ExternalIdentity, Product, Offer
```

**Correct way to import services:**
```python
from billable.services import TransactionService, BalanceService, CustomerService
```

**Async context (bots, ASGI):** Prefer async methods of the API and services (e.g. `TransactionService.acheck_quota`, `OrderService.acreate_order`) so as not to block the event loop.

---

## Data Models

All database tables are prefixed with `billable_`. In every model, *user* (FK to `settings.AUTH_USER_MODEL`) denotes the **Billing account** — the entity to which orders and product rights are attributed.

### Product (`billable_products`)
The catalog of available resources.

- **`product_key`** *(CharField, PK)*: Unique string identifier for accounting (e.g., `DIAMONDS`, `VIP_ACCESS`). Stored in **uppercase (CAPS)**. Automatically normalized on save.
- **`name`** / **`description`**: Display fields.
- **`product_type`** *(Choice)*:
    - `PERIOD`: Time-based access.
    - `QUANTITY`: Consumable units.
    - `UNLIMITED`: Permanent access.
- **`is_active`**: Boolean flag. If `False`, cannot be used in new offers.
- **`is_currency`**: Boolean flag. If `True`, this product is treated as a currency (e.g. internal credits).
- **`created_at`**: Timestamp.
- **`metadata`**: Arbitrary JSON data.

### Referral (`billable_referrals`)
Tracks links between inviters and invitees.

- **`referrer`**: FK to User (the inviter).
- **`referee`**: FK to User (the invitee).
- **`bonus_granted`**: Boolean flag.
- **`created_at`**: Timestamp.
- **`metadata`** *(JSONField)*: Stores configuration.
    - Key `features`: List of feature strings.
- **Methods**:
    - `claim_bonus() -> bool`: Atomically marks `bonus_granted=True` and `bonus_granted_at` to now. Returns `True` if successful, `False` if already granted. Use this for idempotency in bonus logic.

**Note**: Product does NOT contain price or quantity. These are defined in Offers.

### Offer (`billable_offers`)
Marketing packages that bundle products.

- **`sku`** *(CharField, PK)*: Commercial identifier. Stored in **uppercase (CAPS)**. Automatically normalized on save.
- **`name`**: Display name (e.g., "Premium Bundle").
- **`price`** / **`currency`**: Cost (EUR, USD, XTR, INTERNAL).
- **`image`** / **`description`**: UI metadata.
- **`is_active`**: Visibility flag.
- **`metadata`**: Additional configuration (JSON).

### OfferItem (`billable_offer_items`)
Links products to offers with quantity and expiration rules.

- **`offer`**: FK to Offer.
- **`product`**: FK to Product.
- **`quantity`**: How many units of the product.
- **`period_value`** / **`period_unit`**: Expiration (DAYS, MONTHS, YEARS, FOREVER).

### QuotaBatch (`billable_quota_batches`)
User's "wallet" of resources. Each batch represents a grant of a specific product.

- **`user_id`**: FK to User.
- **`product`**: FK to Product.
- **`source_offer`**: FK to Offer (nullable, for audit).
- **`order_item`**: FK to OrderItem (nullable, if purchased).
- **`initial_quantity`**: Original amount granted.
- **`remaining_quantity`**: Current balance.
- **`valid_from`** / **`expires_at`**: Validity period.
- **`state`**: ACTIVE, EXHAUSTED, EXPIRED, REVOKED.
- **`created_at`**: Timestamp for FIFO ordering.

### Transaction (`billable_transactions`)
Immutable ledger of all balance changes.

- **`user_id`**: FK to User.
- **`quota_batch`**: FK to QuotaBatch.
- **`amount`**: Quantity changed.
- **`direction`**: CREDIT (grant) or DEBIT (consume).
- **`action_type`**: Source (e.g., "purchase", "trial_activation", "usage").
- **`object_id`**: Optional external reference.
- **`metadata`**: Context (JSON).
- **`created_at`**: Timestamp.

### Order (`billable_orders`)
Represents a financial transaction intent.

- **`user_id`**: FK to User.
- **`status`** *(Choice)*: `PENDING`, `PAID`, `CANCELLED`, `REFUNDED`.
- **`total_amount`** / **`currency`**: Financial totals.
- **`payment_method`**: String identifier (e.g., `stripe`, `telegram_payments`).
- **`payment_id`**: External transaction ID (for idempotency).
- **`created_at`** / **`paid_at`**: Timestamps.
- **`metadata`**: Application-specific IDs (e.g., `{"report_id": 123}`).

### OrderItem (`billable_order_items`)
Individual lines within an order.

- **`order`**: FK to Order.
- **`offer`**: FK to Offer.
- **`quantity`**: Number of offers purchased.
- **`price`**: Price per offer **at the moment of purchase**.

### TrialHistory (`billable_trial_history`)
Fraud prevention tool. **Does NOT enforce trial logic** — your application layer should check this before granting.

- **`identity_hash`** *(CharField, indexed)*: SHA-256 hash of the user's external ID. The ID is normalized to **lowercase** before hashing for maximum compatibility.
- **`identity_type`**: Type of ID hashed (e.g., `telegram`, `email`).
- **`trial_plan_name`**: The specific trial name used.
- **Methods**:
    - `ahas_used_trial(identities: dict)`: Async check if any identity has used a trial.
    - `generate_identity_hash(value)`: Static method to hash identities.

### ExternalIdentity (`billable_external_identities`)
External identity mapping for integrations.

- **`provider`** *(CharField, indexed, default=`"default"`)*: Identity source (e.g., `telegram`, `n8n`).
- **`external_id`** *(CharField, indexed)*: Stable external identifier.
- **`user`** *(FK, nullable)*: Optional link to `settings.AUTH_USER_MODEL`.
- **`metadata`** *(JSONField)*: Provider-specific payload.
- **Uniqueness**: `(provider, external_id)`.
- **Methods**:
    - `get_user_by_identity(external_id, provider="default")`: Synchronously retrieves a User by their external identity.
    - `aget_user_by_identity(external_id, provider="default")`: Asynchronously retrieves a User by their external identity.
    - `get_external_id_for_user(user, provider="default")`: Synchronously retrieves external_id for a user by provider.
    - `aget_external_id_for_user(user, provider="default")`: Asynchronously retrieves external_id for a user by provider.

---

## Management Commands

### `migrate_identities`

Creates `ExternalIdentity` records from the values of a field on the User (or Custom User) model. Used for one-off migration of existing identifiers (e.g. `telegram_id`, `chat_id`, `stripe_id`) into the identities table. Idempotent: does not create duplicates for `(provider, external_id)`.

**Syntax:**
```bash
python manage.py migrate_identities <field> <provider> [--dry-run] [--limit N]
```

| Argument / option | Description |
|------------------|-------------|
| `field` | Name of the field on the User model that holds the identifier (e.g. `chat_id`, `telegram_id`, `stripe_id`). |
| `provider` | Provider name for `ExternalIdentity` (e.g. `telegram`, `stripe`). |
| `--dry-run` | Print the plan without writing to the database. |
| `--limit N` | Process at most N users (0 = no limit). |

**Behaviour:** For each user with a non-empty field value, an `ExternalIdentity(provider=..., external_id=str(value), user=user)` record is created if that `(provider, external_id)` pair does not already exist. Users with an empty or `None` value for the field are skipped. If the field does not exist on the model, the command writes an error to stderr and exits without creating any records.

**Examples:**
```bash
python manage.py migrate_identities telegram_id telegram
python manage.py migrate_identities chat_id telegram --dry-run
python manage.py migrate_identities stripe_id stripe --limit 100
```

You can run the command multiple times with different fields and providers for the same user; that user will have multiple `ExternalIdentity` records (one per provider/external_id pair).

---

### CustomerService

Service for managing customer-related operations, particularly merging accounts.

```python
from billable.services import CustomerService

# Merge source_user data into target_user
# Moves all orders, quota batches, transactions, identities, and referrals.
stats = CustomerService.merge_customers(target_user_id=101, source_user_id=102)
# Returns: {'moved_orders': 2, 'moved_batches': 5, ...}

# Async version
stats = await CustomerService.amerge_customers(target_user_id=101, source_user_id=102)
```

## Admin Interface

Billable provides enhanced Django Admin interfaces for managing products and analyzing customer usage.

### Customer Product Report
A hierarchical report view available in the Customer admin. It groups transactions by **QuotaBatch** (the source of funds), showing:
1.  **Inflows:** How the product was acquired (Order, Offer, or Manual Grant).
2.  **Spending:** How the product was consumed (Transactions).
3.  **Running Balance:** The balance history calculated chronologically.

This view helps support agents audit complex usage scenarios where a user has multiple active quotas for the same product.

## Webhooks

When integrating Billable with external systems (e.g., n8n, Zapier), you may need to implement specific webhook contracts.

### Referral Bonus Granted

Sent by the application layer when a referral bonus is successfully claimed (typically after the referee's first paid order).

**Method:** `POST`  
**Expected Payload (JSON):**

```json
{
  "event": "referral_bonus_granted",
  "referrer_external_id": "123456789", // e.g. Telegram Chat ID
  "referee_external_id": "987654321",
  "order_id": 55,                      // ID of the order that triggered the bonus
  "idempotency_key": "referral_bonus:10:order:55" // Unique key for deduplication
}
```

## REST API Specification

The API is built with **Django Ninja**.
**Base URL**: `/api/v1/billing` (typical configuration).
**Authentication**: Header `Authorization: Bearer <BILLABLE_API_TOKEN>`.

**Normalization Note**: All endpoints are **case-insensitive** for `sku` and `product_key` inputs. The system automatically converts these fields to uppercase before processing.

### 1. Quota & Balance

#### `GET /balance`
Get current quotas for the authenticated user.
- **Response**: List of active `product_key` and remaining limits.

#### `GET /user-products`
List **active quota batches**, optionally filtered by `product_key`.

- **Query params**:
  - `user_id` *(int, optional)*: Local user id.
  - `product_key` *(str, optional)*: Resource key to filter by.
  - `external_id` *(str, optional)*: External identifier.
  - `provider` *(str, optional)*: Identity provider.
- **Response (200)**: `List[ActiveBatch]`
- **Response (404)**: If `external_id` is provided but user does not exist.

#### `GET /wallet`
Get aggregated balance for all active products.
- **Query params**: `user_id` or (`external_id` + `provider`).
- **Response (200)**: `{"user_id": int, "balances": {"PRODUCT_KEY": int, ...}}`
- **Response (404)**: If user not found (lookup only).

#### `GET /wallet/batches`
Detailed list of all active quota batches (with expires_at).
- **Query params**: `user_id` or (`external_id` + `provider`).
- **Response (200)**: `List[QuotaBatchSchema]`
- **Response (404)**: If user not found (lookup only).

#### `GET /wallet/transactions`
Transaction history (ledger) for the user.
- **Query params**: `user_id` or (`external_id` + `provider`), `product_key`, `action_type`, `date_from`.
- **Response (200)**: `List[TransactionSchema]` (up to 100 recent items).
- **Response (404)**: If user not found (lookup only).

#### `POST /identify`
Identify an external identity and ensure a local `User` exists (create and link if missing).

- **Body**:
  ```json
  {
    "provider": "telegram",
    "external_id": "123456789",
    "profile": {
      "telegram_username": "alice",
      "first_name": "Alice"
    }
  }
  ```
- **Notes**:
  - If `provider` is omitted, `"default"` is used.
  - User is always created or resolved; the response always includes `user_id`.

#### `POST /wallet/consume`
Consume quota for a specific product (admin / server-to-server).
- **Body**: `user_id` or (`external_id` + `provider`), `product_key`, `action_type`, optional `action_id`, `idempotency_key`, optional `metadata` (JSON). The metadata is stored on the created DEBIT transaction.
- **Note**: Automatically creates a local `User` if missing.
- **Response (200)**: `{"success": true, "message": "...", "data": {"usage_id": "...", "remaining": N, "metadata": {...}}}`. The `data.metadata` is the stored transaction metadata (on idempotent replay, the existing transaction's metadata is returned).

#### `POST /demo/trial-grant`
(Demo/Reference implementation) Grant a trial offer with abuse protection.
- **Body**:
  ```json
  {
    "sku": "off_trial_pack",
    "user_id": 123,
    "metadata": { "campaign_id": "winter2024" }
  }
  ```
  Optional `metadata` (JSON) is merged with internal data (e.g. identities) and stored on the created CREDIT transaction(s); the same merged metadata is returned in the response.
- **Notes**: 
  - Uses `TrialHistory` to prevent double-granting. 
  - Automatically creates a local `User` if `external_id` + `provider` is used and user is missing.
  - This is a reference implementation; move logic to `PromotionService` in production.
- **Response (200)**: `{"success": true, "message": "Trial granted", "data": {"products": [...], "metadata": {...}}}`

### 2. Commercial Flows

#### `POST /exchange`
Exchange internal currency for an offer (spend `product_key`, grant `sku`). 
- **Entry point** for internal currency purchases.
- **Logic**: Atomically consumes internal balance and grants the offer via `TransactionService.grant_offer(source="exchange")`. Optional request `metadata` is merged with internal data (e.g. price) and stored on the created Transaction; the same metadata is returned in the response.
- **Body**: Send the JSON object **directly** as the request body (no top-level `"data"` wrapper). Content-Type: `application/json`. Optional field: `metadata` (JSON).
- **By user ID**:
  ```json
  {
    "sku": "off_premium_pack",
    "user_id": 123,
    "metadata": { "source": "telegram_menu" }
  }
  ```
- **By external identity** (e.g. Telegram): provide `external_id` and `provider` instead of `user_id`. User is resolved via `ExternalIdentity`.
  ```json
  {
    "sku": "off_premium_pack",
    "external_id": "322056265",
    "provider": "telegram",
    "metadata": { "source": "telegram_menu" }
  }
  ```
- **Notes**: `sku` is required. Either `user_id` or (`external_id` + `provider`) must be present. If `provider` is omitted when using external identity, `"default"` is used.
- **Auto-creation**: If a new `external_id` + `provider` is used, the system automatically creates a local `User` before processing the exchange.
- **Response (200)**: `{"success": true, "message": "Exchange successful", "data": {"success": true, "message": "Exchanged", "metadata": {...}}}`. The `data.metadata` is the stored transaction metadata (includes at least `price`; plus any request metadata if provided).

### 3. Orders

#### `POST /orders`
Create a new order (financial intent).
- **Body**:
  ```json
  {
    "user_id": 123,
    "items": [
      {"sku": "off_diamonds_100", "quantity": 1}
    ]
  }
  ```
- **Auto-creation**: If a new `external_id` + `provider` is used (instead of `user_id`), the system automatically creates a local `User`.

#### `POST /orders/{order_id}/confirm`
Confirm payment for an order and grant products.
- **Entry point** for real money purchases (RUB, USD, XTR). Webhooks from Stripe, YooKassa, or Telegram Payments are handled in **your application**: the app receives the webhook, extracts `order_id` and `payment_id`, then calls this endpoint. Billable does not expose a built-in webhook URL.
- **Logic**: Transitions order to `PAID` and calls `TransactionService.grant_offer(source="purchase")`. Reprocessing the same `payment_id` does not create duplicate batches or transactions.
- **Body**:
  ```json
  {
    "payment_id": "tx_abc_123",
    "payment_method": "stripe"
  }
  ```

#### `POST /orders/{order_id}/refund`
Refund a paid order and revoke associated products.
- **Logic**: Transitions order to `REFUNDED` and creates `DEBIT` transactions for any remaining quantity in the batches granted by this order. Batches are marked as `REVOKED`.
- **Body**:
  ```json
  {
    "reason": "Customer request"
  }
  ```

### 4. Referrals & Stats

#### `POST /referrals`
Create a referral link between referrer and referee. Supports two input modes.

- **By user IDs** — body: `referrer_id`, `referee_id` *(int)*, optional `metadata`.  
- **By external identity** — body: `provider`, `referrer_external_id`, `referee_external_id` *(str)*, optional `metadata`. Only existing `ExternalIdentity` records are used; if either identity is missing, returns 400 without creating the referral.
- **Response (200)**: `{"success": true, "message": "Referral assigned", "data": {"created": bool, "referral_id": int, "metadata": {...}}}`. The `metadata` is the stored Referral metadata (from the request or existing record).

#### `GET /referrals/stats`
Referral statistics (e.g. count of invited users) for the referrer.

- **Query params**:
  - `user_id` *(int, optional)*: Local user id.
  - `external_id` *(str, optional)*: External identifier (used if `user_id` is not provided).
  - `provider` *(str, optional)*: Identity provider for `external_id`. Defaults to `"default"`.
- **Response (200)**: `{"success": true, "message": "Stats retrieved", "data": {"count": N}}`
- **Response (404)**: If `external_id` is provided but user does not exist.

#### `POST /customers/merge`
Merge two customers: move all data from `source_user` to `target_user`.

- **Body**:
  ```json
  {
    "target_user_id": 1,
    "source_user_id": 2
  }
  ```
- **Response (200)**: `CustomerMergeResponse` (success flag, message, and counts of moved items).
- **Notes**: Atomically moves orders, batches, transactions, identities, and referrals.

### 5. Catalog

#### `GET /catalog`
List all active offers (catalog) with nested offer items and products.

- **Logic**: Returns offers with `is_active=True`, prefetched `items` and products.
- **Response fields**: `sku`, `name`, `price`, `currency`, `description`, `image`, `is_active`, `items`, `metadata`.
- **Query params** *(optional)*:
  - `sku` *(str, repeatable)*: Filter by SKU list. Example: `?sku=off_a&sku=off_b`. Preserves input order; returns only found offers. If omitted, returns full catalog.
- **Response (200)**: `List[OfferSchema]`

#### `GET /catalog/{sku}`
Get a single active offer by SKU.

- **Path**: `sku` — unique offer identifier (e.g. `off_credits_100`).
- **Logic**: Exact match on `sku` and `is_active=True`. Prefetches `items__product`.
- **Response fields**: `sku`, `name`, `price`, `currency`, `description`, `image`, `is_active`, `items`, `metadata`.
- **Response (200)**: `OfferSchema`
- **Response (404)**: `CommonResponse` — `{"success": false, "message": "Offer not found"}` if offer does not exist or is inactive.

**Контракт ответа (OfferSchema):**

| Поле | Тип | Описание |
|------|-----|----------|
| `sku` | string | Коммерческий идентификатор (например `off_diamonds_100`, `pack_premium`). |
| `name` | string | Отображаемое название оффера. |
| `price` | number (Decimal) | Цена за единицу. |
| `currency` | string | Код валюты: EUR, USD, XTR, INTERNAL и т.д. |
| `description` | string | Описание для UI. |
| `image` | string \| null | URL изображения или null. |
| `is_active` | boolean | Видимость в каталоге. |
| `items` | array | Список позиций оффера (продукты и количества). |
| `metadata` | object | Доп. конфигурация (JSON). |

**Структура элемента `items` (OfferItemSchema):**

| Поле | Тип | Описание |
|------|-----|----------|
| `product` | object | Продукт в позиции (см. ниже). |
| `quantity` | integer | Количество единиц продукта в оффере. |
| `period_unit` | string | Единица срока: DAYS, MONTHS, YEARS, FOREVER. |
| `period_value` | integer \| null | Число для срока; null для FOREVER. |

**Вложенный объект `product` (ProductSchema):**

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | integer | PK продукта. |
| `product_key` | string \| null | Идентификатор для учёта (например `diamonds`, `vip_access`). |
| `name` | string | Отображаемое название продукта. |
| `description` | string | Текстовое описание. |
| `product_type` | string | PERIOD, QUANTITY, UNLIMITED. |
| `is_active` | boolean | Доступность в новых офферах. |
| `metadata` | object | JSON-конфигурация. |
| `created_at` | string (datetime) | Время создания. |

**Пример ответа (фрагмент):**
```json
[
  {
    "sku": "pack_premium",
    "name": "Premium Bundle",
    "price": "9.99",
    "currency": "USD",
    "description": "Monthly premium access",
    "image": null,
    "is_active": true,
    "items": [
      {
        "product": {
          "id": 1,
          "product_key": "vip_access",
          "name": "VIP Access",
          "description": "",
          "product_type": "PERIOD",
          "is_active": true,
          "metadata": {},
          "created_at": "2025-01-01T00:00:00"
        },
        "quantity": 1,
        "period_unit": "MONTHS",
        "period_value": 1
      }
    ],
    "metadata": {}
  }
]
```
