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

---

## Data Models

All database tables are prefixed with `billable_`. In every model, *user* (FK to `settings.AUTH_USER_MODEL`) denotes the **Billing account** — the entity to which orders and product rights are attributed.

### Product (`billable_products`)
The catalog of available items.

- **`sku`** *(CharField, unique)*: String identifier for integration (e.g., `premium_monthly`).
- **`name`** / **`description`**: Display fields.
- **`product_type`** *(Choice)*:
    - `PERIOD`: Subscription-based (requires `period_days`).
    - `QUANTITY`: Consumable packs (requires `quantity`).
    - `UNLIMITED`: Permanent access or audit-only items.
- **`price`** / **`currency`**: Cost definitions.
- **`is_active`**: Boolean flag for soft deletion.
- **`metadata`** *(JSONField)*: Stores configuration.
    - Key `features`: List of feature strings (e.g., `["pdf_export", "ai_analysis"]`).

### Order (`billable_orders`)
Represents a financial transaction intent.

- **`user`**: FK to `settings.AUTH_USER_MODEL`.
- **`status`** *(Choice)*: `PENDING`, `PAID`, `CANCELLED`, `REFUNDED`.
- **`total_amount`** / **`currency`**: Financial totals.
- **`payment_method`**: String identifier (e.g., `stripe`, `telegram_payments`).
- **`payment_id`**: External transaction ID (for idempotency).
- **`created_at`** / **`paid_at`**: Timestamps.
- **`metadata`**: Application-specific IDs (e.g., `{"report_id": 123}`).

### OrderItem (`billable_order_items`)
Individual lines within an order.

- **`order`**: FK to Order.
- **`product`**: FK to Product.
- **`quantity`**: Number of units purchased.
- **`price`**: Price per unit **at the moment of purchase**.
- **`total_quantity`** / **`period_days`**: Snapshot of product properties at purchase time (to preserve history if product changes).

### UserProduct (`billable_user_products`)
The active "inventory" of rights owned by a user.

- **`user`**: FK to User.
- **`product`**: FK to Product.
- **`is_active`**: Boolean flag.
- **For Quantity Products**:
    - `total_quantity`: Initial limit.
    - `used_quantity`: Current usage.
- **For Period Products**:
    - `period_start`: Activation date.
    - `expires_at`: Expiration date.
- **Methods**:
    - `can_use()`: Returns True if quota > used or not expired.
    - `get_remaining()`: Returns integer balance.

### TrialHistory (`billable_trial_history`)
Prevents trial abuse.

- **`identity_hash`** *(CharField, indexed)*: SHA-256 hash of the user's external ID (device ID, phone, etc.).
- **`identity_type`**: Type of ID hashed (typically matches `provider`, e.g., `telegram`, `max`, `n8n`, `email`).
- **`trial_plan_name`**: The specific trial SKU used.

### ExternalIdentity (`billable_external_identities`)
External identity mapping for integrations.

- **`provider`** *(CharField, indexed, default=`"default"`)*: Identity source/provider name (e.g., `telegram`, `max`, `n8n`). If not provided, `"default"` is used.
- **`external_id`** *(CharField, indexed)*: Stable external identifier within the provider scope.
- **`user`** *(FK, nullable)*: Optional link to `settings.AUTH_USER_MODEL`.
- **`metadata`** *(JSONField)*: Provider-specific payload (username, display names, workspace, etc.).
- **Uniqueness**: `(provider, external_id)`.

### ProductUsage (`billable_product_usages`)
Audit log of every consumption event.

- **`user_product`**: Link to the source of rights.
- **`action_type`**: String describing the action.
- **`action_id`**: Idempotency key for the specific action.
- **`metadata`**: Context (e.g., resulting artifact ID).

---

## REST API Specification

The API is built with **Django Ninja**.
**Base URL**: `/api/v1/billing` (typical configuration).
**Authentication**: Header `Authorization: Bearer <BILLABLE_API_TOKEN>`.

### 1. Quota & Balance

#### `GET /balance`
Get current quotas for the authenticated user.
- **Response**: List of active features and remaining limits.

#### `GET /user-products`
List **active products of a user**, optionally filtered by feature.

- **Query params**:
  - `user_id` *(int, optional)*: Local user id.
  - `feature` *(str, optional)*: Feature name to filter by. If omitted/empty, returns all active user products.
  - `external_id` *(str, optional)*: External identifier (used if `user_id` is not provided).
  - `provider` *(str, optional)*: Identity provider for `external_id`. Defaults to `"default"`.
- **Notes**:
  - User is resolved by `user_id`, or by `(provider, external_id)` mapping.
  - **Product features are returned in** `product.metadata.features` (list of strings).
- **Response (200)**: `List[UserProduct]`

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

#### `POST /grants`
Grant a product directly (Admin/System usage).
- **Body**:
  ```json
  {
    "sku": "promo_pack_10",
    "user_id": 123
  }
  ```

#### `POST /referrals`
Create a referral link between referrer and referee. Supports two input modes.

- **By user IDs** — body: `referrer_id`, `referee_id` *(int)*, optional `metadata`.  
- **By external identity** — body: `provider`, `referrer_external_id`, `referee_external_id` *(str)*, optional `metadata`. Both identities are resolved via `ExternalIdentity` (user created and linked if missing). Same semantics as `/identify` for each side.

Provide exactly one of the two modes. If `referrer` and `referee` resolve to the same user, returns 400.