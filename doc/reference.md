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
The catalog of available resources.

- **`product_key`** *(CharField, PK)*: Unique string identifier for accounting (e.g., `diamonds`, `vip_access`). Noun, singular.
- **`name`** / **`description`**: Display fields.
- **`product_type`** *(Choice)*:
    - `PERIOD`: Time-based access.
    - `QUANTITY`: Consumable units.
    - `UNLIMITED`: Permanent access.
- **`is_active`**: Boolean flag. If False, cannot be used in new offers.
- **`created_at`**: Timestamp.
- **`metadata`** *(JSONField)*: Stores configuration.
    - Key `features`: List of feature strings.

**Note**: Product does NOT contain price or quantity. These are defined in Offers.

### Offer (`billable_offers`)
Marketing packages that bundle products.

- **`sku`** *(CharField, PK)*: Commercial identifier.
    - Prefixes: `off_` (base), `pack_` (bundle), `promo_` (sale).
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

- **`identity_hash`** *(CharField, indexed)*: SHA-256 hash of the user's external ID.
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

---

## REST API Specification

The API is built with **Django Ninja**.
**Base URL**: `/api/v1/billing` (typical configuration).
**Authentication**: Header `Authorization: Bearer <BILLABLE_API_TOKEN>`.

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

#### `POST /demo/trial-grant`
(Demo/Reference implementation) Grant a trial offer with abuse protection.
- **Body**:
  ```json
  {
    "sku": "off_trial_pack",
    "user_id": 123
  }
  ```
- **Notes**: 
  - Uses `TrialHistory` to prevent double-granting. 
  - This is a reference implementation; move logic to `PromotionService` in production.

### 2. Commercial Flows

#### `POST /exchange`
Exchange internal currency for an offer (spend `product_key`, grant `sku`). 
- **Entry point** for internal currency purchases.
- **Logic**: Atomically consumes internal balance and grants the offer via `TransactionService.grant_offer(source="exchange")`.
- **Body**: Send the JSON object **directly** as the request body (no top-level `"data"` wrapper). Content-Type: `application/json`.
- **By user ID**:
  ```json
  {
    "sku": "off_premium_pack",
    "user_id": 123
  }
  ```
- **By external identity** (e.g. Telegram): provide `external_id` and `provider` instead of `user_id`. User is resolved via `ExternalIdentity`.
  ```json
  {
    "sku": "off_premium_pack",
    "external_id": "322056265",
    "provider": "telegram"
  }
  ```
- **Notes**: `sku` is required. Either `user_id` or (`external_id` + `provider`) must be present. If `provider` is omitted when using external identity, `"default"` is used.

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

#### `POST /orders/{order_id}/confirm`
Confirm payment for an order and grant products.
- **Entry point** for real money purchases (RUB, USD, XTR).
- **Logic**: Transitions order to `PAID` and calls `TransactionService.grant_offer(source="purchase")`.
- **Body**:
  ```json
  {
    "payment_id": "tx_abc_123",
    "payment_method": "stripe"
  }
  ```

### 4. Referrals & Stats

#### `POST /referrals`
Create a referral link between referrer and referee. Supports two input modes.

- **By user IDs** — body: `referrer_id`, `referee_id` *(int)*, optional `metadata`.  
- **By external identity** — body: `provider`, `referrer_external_id`, `referee_external_id` *(str)*, optional `metadata`. Both identities are resolved via `ExternalIdentity` (user created and linked if missing). Same semantics as `/identify` for each side.

#### `GET /referrals/stats`
Referral statistics (e.g. count of invited users) for the referrer.

- **Query params**:
  - `user_id` *(int, optional)*: Local user id.
  - `external_id` *(str, optional)*: External identifier (used if `user_id` is not provided).
  - `provider` *(str, optional)*: Identity provider for `external_id`. Defaults to `"default"`.
- **Response (200)**: `{"success": true, "message": "Stats retrieved", "data": {"count": N}}`
