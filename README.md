# Universal Billable Module (universal Billing Engine)
Status: Active
Date: 2026-01-19
Tags: [monetization, billing, api, generic, ninja, n8n, referral, trial, quota, feature-based]

## Business Processes

The module is designed as an isolated rights and payments accounting system (Billing Engine). It does not contain the application logic of a specific product (e.g., report generation), delegating this to external orchestrators (n8n).

**1. Onboarding and Identity:**
- **External Identification**: Orchestrators (like messaging bots or web apps) or specialized modules (like `bot`) handle user identification/creation.
- **Trial Accruals**: The "first report for free" logic is implemented through the activation of products with the `trial` feature. The system checks trial usage history via the `TrialHistory` model using abstract identity hashes (protecting against reuse across different providers).
- **Quota Balance**: Before offering services, the user's quota balance is checked by feature name.

**2. Order Life Cycle (Order Flow):**
- **Initiation**: An `Order` is created via the API or service layer **before** sending an invoice to the client, passing a list of products with `sku` or `id`, `quantity`, and `price`. Application IDs (e.g., `report_id`) are stored in `metadata`. The order is created with `PENDING` status.
- **Invoice Creation**: After order creation, the `order_id` is included in the external invoice `payload` along with application metadata (e.g., `report_id`, `sku`). This ensures the order exists in the database before payment.
- **Payment**: Payment gateway processing occurs in n8n or external systems. The invoice payload contains the `order_id` from the database.
- **Confirmation**: After successful payment, the existing order is confirmed via `POST /api/v1/billing/orders/{order_id}/confirm` with `payment_id` and `payment_method`. The module atomically transitions the order to `paid` status, sets `paid_at`, and creates `UserProduct` records for each order item, calculating `expires_at` for period-based products.
- **Idempotency**: Reprocessing a payment with the same `payment_id` does not create duplicate `UserProduct` records. The order is identified by `order_id` from the invoice payload, not created anew.

**3. Referral Program (Generic Referrals):**
- **Links**: The module stores `referrer -> referee` chains through the `Referral` model.
- **Bonuses**: Bonus accrual logic is managed via the API or service layer, triggering the creation of free orders or direct quota accruals.
- **Protection**: Verification through `TrialHistory` prevents the reuse of bonuses by identity hashes (e.g., hashed external ID).

**4. Consumption Control (Quota Management):**
- **Selector-based approach (SKU-first, feature-fallback)**: For backward compatibility with external integrations that may pass a product identifier, the API parameter `feature` is treated as a selector:
  - First, the engine tries to match it as `Product.sku` (case-insensitive).
  - If no active product is found by SKU, it falls back to matching it as a feature name via `Product.metadata.features` (e.g., `['resume_lift', 'vacancy_response']`).
- **Verification**: `check_quota(user, feature)` methods return availability, a message, product name, and remaining balance.
- **Consumption**: `consume_quota(user, feature, ...)` methods atomically consume quota, creating a `ProductUsage` record and supporting idempotency via `idempotency_key`.
- **Product Types**:
  - `quantity`: `used_quantity` is decremented; when the limit is reached, the product is deactivated.
  - `period`: `expires_at` is checked; consumption does not affect the counter.
  - `unlimited`: Always available; consumption is for audit only.

## Architecture

The module is "detachable" (standalone-ready) and interacts with the environment through abstract interfaces.

### System Components

- **Core Engine (billable)**: 
    - Works with an abstract user model via `settings.AUTH_USER_MODEL`.
    - Has no direct imports from application modules (`yir`, `vacancies`).
    - Provides a REST API for n8n and a Python API (service layer) for workers.
    - Uses JSONB `metadata` to store application IDs instead of ForeignKeys.
- **Orchestrator (n8n)**: 
    - "Glue Logic" layer. Knows about external platforms, payment gateways, and application IDs.
    - Maps business events (like `/start` in bot) to the identification layer (`bot`) and then to the Billing Generic API.
    - Manages user scenarios and states through `UserSession`.
- **Consumer (yir/vacancies)**:
    - Consumes services through the service layer without knowing about the logic of their acquisition or cost.
    - Calls quota check and consumption methods by feature.

### Service Layer

**QuotaService** — central service for working with quotas:
- Feature availability check methods.
- Atomic consumption with idempotency.
- Trial product activation.
- Support for synchronous and asynchronous operations.

**UserProductService** — management of active user products:
- Retrieving active and available products with feature filtering.
- Usage capability check.
- Balance summary retrieval.
- Usage record creation.

**OrderService** — order management:
- Creation of orders with multiple products.
- Payment processing with idempotency.
- Automatic `UserProduct` creation upon payment.
- Product deactivation upon refund/cancellation.

**ProductService** — working with the product catalog:
- Retrieving products by features.
- Filtering trial products.
- Product activity management.

## Technical Implementation

### Current State (Analysis)

**What is implemented:**
- ✅ Data models: `Product`, `Order`, `OrderItem`, `UserProduct`, `ProductUsage`, `TrialHistory`, `Referral`.
- ✅ SKU support in the `Product` model.
- ✅ JSONB `metadata` for extensibility.
- ✅ Product types: `period`, `quantity`, `unlimited`.
- ✅ Service layer with atomic operations.
- ✅ REST API via Django Ninja with Swagger documentation.
- ✅ Django Signals for event-driven local integration.
- ✅ Full unit and integration test coverage.

### Universal Data Models

**Примечание**: Все таблицы и индексы биллинга используют префикс `billable_` (например, `billable_products`, `billable_orders`, `billable_order_items`, и т.д.).

**Product** — product catalog:
- Таблица: `billable_products`
- `sku` (CharField, unique): Unique identifier for integration.
- `name`, `description`: Name and description.
- `product_type` (TextChoices): `PERIOD`, `QUANTITY`, `UNLIMITED`.
- `price`, `currency`: Price and currency.
- `period_days` (nullable): Validity period for period-based products.
- `quantity` (nullable): Number of units for quantity-based products.
- `is_active`: Activity flag.
- `metadata` (JSONField): Additional parameters, including `features` (list of features).

**Order** — user orders:
- Таблица: `billable_orders`
- `user` (ForeignKey): Link to the user.
- `total_amount`, `currency`: Amount and currency.
- `status` (TextChoices): `PENDING`, `PAID`, `CANCELLED`, `REFUNDED`.
- `payment_method`, `payment_id`: Payment data.
- `created_at`, `paid_at`: Timestamps.

**OrderItem** — order items:
- Таблица: `billable_order_items`
- `order` (ForeignKey): Link to the order.
- `product` (ForeignKey): Link to the product.
- `quantity`, `price`: Quantity and price per unit.
- `total_quantity`, `period_days`: Copies of product parameters at the time of purchase.

**UserProduct** — active user rights:
- Таблица: `billable_user_products`
- `user`, `product`, `order_item`: Links.
- `purchased_at`, `expires_at`: Timestamps.
- `is_active`: Activity flag.
- `total_quantity`, `used_quantity`: For quantity-based products.
- `period_start`, `period_end`: For period-based products.
- Methods: `can_use()`, `is_expired()`, `get_remaining_quantity()`, `get_days_left()`.

**ProductUsage** — usage history:
- Таблица: `billable_product_usages`
- `user`, `user_product`: Links.
- `action_type`, `action_id`: Action type and identifier.
- `used_at`: Timestamp.
- `metadata` (JSONField): Additional data (stores app IDs like `report_id`).

**TrialHistory** — tracking of trial usage:
- Таблица: `billable_trial_history`
- `identity_type` (CharField): Type of identifier (e.g., 'external_id', 'hh').
- `identity_hash` (CharField, indexed): SHA-256 hash of the identifier for privacy.
- `trial_plan_name`: Name of the trial plan used.
- `used_at`, `created_at`: Timestamps.
- Methods: `has_used_trial(identities: dict)`, `has_used_trial_async(identities: dict)`.

**Referral** — referral program:
- Таблица: `billable_referrals`
- `referrer`, `referee` (ForeignKey): User links.
- `bonus_granted`: Bonus accrual flag.
- `created_at`: Timestamp.

### Generic API (Django Ninja)

**Main Endpoints:**
- `POST /api/v1/billing/grants` — grant products by SKU (e.g., 'express') or default trial products.
- `GET /api/v1/billing/balance` — get current user quotas by feature.
- `POST /api/v1/billing/orders` — create an order with arbitrary metadata.
- `POST /api/v1/billing/orders/{order_id}/confirm` — confirm payment and activate rights. Returns full order data including item SKUs.
- `POST /api/v1/billing/quota/consume` — consume quota by feature.
- `POST /api/v1/billing/referrals` — establish a referral link.

**Detailed Endpoint: Order Confirmation**

`POST /api/v1/billing/orders/{order_id}/confirm`

**Request Body:**
```json
{
  "payment_id": "string (e.g. external payment_charge_id)",
  "payment_method": "string (default: provider_payments)",
  "status": "paid"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Order paid and products activated",
  "data": {
    "id": 123,
    "user_id": 456,
    "status": "paid",
    "total_amount": "500.00",
    "currency": "RUB",
    "payment_method": "provider_payments",
    "payment_id": "charge_...",
    "created_at": "2026-01-19T10:00:00Z",
    "paid_at": "2026-01-19T10:05:00Z",
    "items": [
      {
        "id": 1,
        "product_id": 10,
        "product_name": "Premium Report",
        "sku": "yir_premium",
        "quantity": 1,
        "price": "500.00",
        "total_quantity": 1,
        "period_days": null
      }
    ],
    "metadata": {
      "report_id": 789
    }
  }
}
```

*Note: User profile creation/syncing is moved to the `bot` module.*

## Security

**1. Authentication**
- **Bearer Token Authentication**: Tokens are passed in the `Authorization: Bearer <token>` header. Token is checked against `settings.BILLING_API_TOKEN`.

**2. Protective measures**
- **Atomic Operations**: All quota changes use `transaction.atomic()` and `select_for_update()` (where supported).
- **Idempotency**: Using `idempotency_key` (via `action_id`) in quota consumption and payment processing.
- **Validation**: All input data is validated via Pydantic/Ninja schemas.

## Refactoring Log

### Refactoring monetization to billable (COMPLETED)
- Renamed folder `monetization` -> `billable`.
- Renamed AppConfig `MonetizationConfig` -> `BillableConfig`.
- Updated all imports and references in `settings.py`, `urls.py`, and within the module.
- Translated all comments, docstrings, and `README.md` to English.
- Updated all migrations to use the new app name and English verbose names.
- Created `pyproject.toml` to make the module a standalone installable package.
- All tests updated and passing.

### Stage 5: User Identification Refactoring (COMPLETED)
- **Extracted Logic**: Moved user registration and identification from `billable` to the new `bot` module.
- **New Endpoint**: Created `POST /api/v1/bot/identify` as the main entry point for n8n/external scenarios.
- **Trial Eligibility**: Trial check logic is now part of the `identify` flow but uses the abstract `TrialHistory` from `billable`.

### Stage 6: n8n Workflow Migration (IN PROGRESS)
**Goal**: Replace direct SQL queries and old API calls in n8n.
**Tasks**:
- [x] Update "Year in Review - Onboarding" workflow.
- [x] Replace user registration SQL with `POST /api/v1/bot/identify`.
- [x] Replace trial grant SQL with `POST /api/v1/billing/grants`.
- [x] **Invoice Creation Flow**: Orders are now created in the database **before** sending invoices to the client. The `order_id` is included in the invoice `payload`, and payment confirmation uses the existing order via `POST /api/v1/billing/orders/{order_id}/confirm` instead of creating a new order.
- [ ] Test E2E workflow with the new API.

## Principles of Detachability

1. **No Hardlinks**: No ForeignKeys to other apps. App IDs are stored in `metadata`.
2. **Settings Based**: Configuration via `settings.py`.
3. **Event Driven**: Generates Django Signals for decoupled integration.
4. **Feature Based**: Products define capabilities via `metadata.features`.
5. **Idempotency**: Built-in protection against double-spending.
6. **Packageable**: Included `pyproject.toml` for `pip install` support.

Смотрите также:
- [API Design Patterns](./api_design_patterns.md)
- [Bot Module](./bot.md)
- [Year in Review Generation](./year_in_review_generation.md)
- [Deployment Setup](../40-Operations/04_deployment_setup.md)
