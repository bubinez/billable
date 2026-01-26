# Billable Module for Django

Powerful and flexible monetization module for Django projects, developed with an emphasis on detachability and easy integration with n8n via REST API.

## Key Features

- **Product Management**: Support for types: period (subscription), quantity (quotas), unlimited.
- **REST API (Django Ninja)**: Full-featured API with Swagger/OpenAPI support.
- **Service Layer**: All business logic encapsulated in services (`QuotaService`, `OrderService`, etc.).
- **Atomicity and Safety**: Use of transactions and locks (`select_for_update`) to prevent race conditions during quota consumption.
- **Idempotency**: Support for idempotency keys to prevent double spending/accruals.
- **Event System**: Django Signals for integration with other parts of the system without tight coupling.
- **Trial Periods**: Built-in `TrialHistory` logic with abstract identities (support for hashing various external IDs like messaging platforms, auth providers, etc.).

## Installation and Integration

1. Add `billable` to `INSTALLED_APPS` in `settings.py`.
2. Connect the router in `urls.py`:
   ```python
   from billable.api import router as billing_router
   api.add_router("/billing", billing_router)
   ```
3. Run migrations: `python manage.py migrate billable`.

## Main API Endpoints

- `GET /api/v1/billing/balance` — check feature availability for a user.
- `POST /api/v1/billing/quota/consume` — atomic quota consumption.
- `POST /api/v1/billing/orders` — create an order.
- `POST /api/v1/billing/orders/{id}/confirm` — confirm payment and activate rights. Returns full order data including SKUs.
- `POST /api/v1/billing/grants` — activate a trial period or grant bonuses.
- `POST /api/v1/billing/referrals` — establish a referral link between users.

## "No Hardlinks" Principle

The module is designed not to depend on specific application models of your project. Instead of `ForeignKey` to your reports or vacancies, a `metadata` (JSONB) field is used where necessary IDs are stored.

## Signals

You can subscribe to events in your application:
- `order_confirmed`
- `quota_consumed`
- `trial_activated`
- `product_deactivated`

## Product Metadata Schema

The product metadata (`Product.metadata`) use a `features` list that defines available functionality:
```json
{
  "features": ["report_generation", "priority_support"],
  "is_trial": true
}
```
