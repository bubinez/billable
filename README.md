# Universal Billable Module

**A Detachable Billing Engine for Django & Ninja**

`billable` is an isolated rights management and payments accounting system designed for Django. It abstracts monetization logic (subscriptions, one-time purchases, trials, quotas) from your core application business logic.

The module provides a single API and accounting layer for different orchestrators (n8n, bots, web), so each can use the same billing flows. Designed to work seamlessly with orchestrators like **n8n**, and fully usable as a standalone Python service layer.

## Status
![Status](https://img.shields.io/badge/Status-Active-success)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Django](https://img.shields.io/badge/Django-5.0%2B-green)

## Features
- **Transaction-Based Ledger**: All balance changes are recorded as immutable transactions (Credit/Debit).
- **Offer System**: Flexible product bundles with configurable expiration periods.
- **FIFO Consumption**: Automatic oldest-first quota consumption.
- **Fraud Prevention**: Abstract identity hashing for trial abuse protection.
- **Detachable Architecture**: No foreign keys to your business models (uses metadata).
- **Idempotency**: Built-in protection against double-spending and duplicate payments.
- **Customer Merging**: Service and API for consolidating user accounts without data loss.
- **REST API**: Ready-to-use Django Ninja API for frontend or external orchestrators.

---

## Documentation

- 📘 **[Architecture & Design](doc/architecture.md)**
  Deep dive into Business Processes, Order Flow, and the Transaction Engine.
  
- 📙 **[API & Models Reference](doc/reference.md)**
  Database schema, Configuration variables, and REST API specification.

- 📋 **Changelog**: See repository releases or git history.

---

## Installation

Install using pip:

```bash
pip install billable
```

Or install directly from Git (if using a private repository):

```bash
pip install git+https://github.com/bubinez/billable.git
```

## Configuration

### 1. Update `settings.py`

Add the app to your installed apps and configure the required settings:

```python
INSTALLED_APPS = [
    # ...
    "billable",
]

# Required: Security token for the REST API
BILLABLE_API_TOKEN = env("BILLABLE_API_TOKEN", default="change-me-in-production")

# Optional: Defaults to "auth.User"
# BILLABLE_USER_MODEL = "custom_users.User" 
```

### 2. Import Policy

To avoid `AppRegistryNotReady` errors (especially in tests), **always** import models and services from their respective submodules. **Never** import directly from the root `billable` package.

```python
# Correct
from billable.models import Product, ExternalIdentity
from billable.services import TransactionService

# Incorrect - will cause AppRegistryNotReady
# from billable import Product, TransactionService
```

### 3. Configure URLs

Include billable URLs in your main `urls.py`:

```python
from django.urls import path, include

urlpatterns = [
    # Mounts the API at /api/v1/billing/
    path("api/v1/billing/", include("billable.urls")),
]
```

### 3. Run Migrations

Create the tables prefixed with `billable_`:

```bash
python manage.py migrate billable
```

To migrate existing user identity fields (e.g. `telegram_id`, `chat_id`) into `ExternalIdentity`, run: `python manage.py migrate_identities <field> <provider>`. See [Reference — Management Commands](doc/reference.md#management-commands).

---

## Quick Start

### Python Service Layer (Internal Usage)
You can use the module directly in your views or Celery tasks without calling the HTTP API.

**Checking Quota:**
```python
from billable.services import TransactionService

async def generate_pdf_report(user):
    # Check if user has the technical resource "pdf_export" available
    result = await TransactionService.acheck_quota(user.id, "pdf_export")

    if not result["can_use"]:
        raise PermissionError(f"Upgrade required: {result['message']}")

    # Your logic here...
    print("Generating PDF...")

    # Consume 1 unit of quota (Atomic & Idempotent)
    await TransactionService.aconsume_quota(
        user_id=user.id, 
        product_key="pdf_export", 
        idempotency_key=f"report_{report_id}"
    )
```

**Creating a Custom Order:**
```python
from billable.services import OrderService

order = await OrderService.acreate_order(
    user_id=request.user.id,
    items=[
        {"sku": "off_premium_pack", "quantity": 1}
    ],
    metadata={"source": "web_checkout"}
)
```

**Implementing Trial/Bonus Logic:**

`billable` provides **building blocks** for fraud prevention and transaction management, but does NOT include business rules for promotions. Here's how to implement trial logic in your application:

```python
from billable.models import Offer, TrialHistory
from billable.services import TransactionService
from asgiref.sync import sync_to_async

async def claim_welcome_trial(user_id: int, telegram_id: str):
    """Example: Grant welcome trial with fraud prevention."""
    
    # 1. Check eligibility using TrialHistory
    identities = {"telegram": telegram_id}
    if await TrialHistory.ahas_used_trial(identities=identities):
        return {"success": False, "reason": "trial_already_used"}
    
    # 2. Find the trial offer (create an Offer with sku="off_welcome_trial" in your DB)
    offer = await Offer.objects.aget(sku="off_welcome_trial")
    
    # 3. Grant the offer using TransactionService
    batches = await sync_to_async(TransactionService.grant_offer)(
        user_id=user_id,
        offer=offer,
        source="welcome_bonus",
        metadata={"identities": identities}
    )
    
    # 4. Mark trial as used
    await TrialHistory.objects.acreate(
        identity_type="telegram",
        identity_hash=TrialHistory.generate_identity_hash(telegram_id),
        trial_plan_name="Welcome Trial"
    )
    
    return {"success": True, "batches": batches}
```

For complex promotion campaigns (multi-step bonuses, referral rewards, etc.), create a dedicated `PromotionService` in your application layer that orchestrates calls to `TransactionService`.

### REST API Usage
If you are using **n8n** or a frontend:

**Identify user by external identity (recommended first step):**
`POST /api/v1/billing/identify`

**Purchase Flow (Real Money):**
1.  **Create Order**: `POST /api/v1/billing/orders`
2.  **Confirm Payment**: `POST /api/v1/billing/orders/{order_id}/confirm`
    *Triggered by your payment webhook. This grants products via `TransactionService.grant_offer(source="purchase")`.*

**Exchange Flow (Internal Currency):**
1.  **Exchange**: `POST /api/v1/billing/exchange`
    *Atomically spends internal currency and grants the target offer.*

**Get Balance:**
`GET /api/v1/billing/wallet` (Headers: `Authorization: Bearer <TOKEN>`)

**Catalog:**
- `GET /api/v1/billing/catalog` — list all active offers (or filter by `?sku=...&sku=...` for bulk lookup)
- `GET /api/v1/billing/catalog/{sku}` — get a single offer by SKU

*For full API details, see the [Reference Guide](doc/reference.md).*
