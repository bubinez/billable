# API & Models Reference

This document provides a technical reference for the **Universal Billable Module**. It covers the database schema, configuration settings, and REST API specification.

## Configuration (`settings.py`)

The module relies on standard Django settings.

| Setting | Default | Description |
| :--- | :--- | :--- |
| `INSTALLED_APPS` | - | Must include `"billable"`. |
| `AUTH_USER_MODEL` | `"auth.User"` | The Django user model to link orders and products to. |
| `BILLING_API_TOKEN` | `None` | **Required.** Secret token for Bearer authentication in REST API. |
| `BILLING_CURRENCY` | `"USD"` | Default currency code (optional, depends on implementation). |

---

## Data Models

All database tables are prefixed with `billable_`.

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
- **`identity_type`**: Type of ID hashed (e.g., `telegram_id`, `email`).
- **`trial_plan_name`**: The specific trial SKU used.

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
**Authentication**: Header `Authorization: Bearer <BILLING_API_TOKEN>`.

### 1. Quota & Balance

#### `GET /balance`
Get current quotas for the authenticated user.
- **Response**: List of active features and remaining limits.

#### `POST /grants`
Grant a product directly (Admin/System usage).
- **Body**:
  ```json
  {
    "sku": "promo_pack_10",
    "user_id": 123
  }