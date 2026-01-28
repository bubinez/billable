# Universal Billable Module

**A Detachable Billing Engine for Django & Ninja**

`billable` is an isolated rights management and payments accounting system designed for Django. It abstracts monetization logic (subscriptions, one-time purchases, trials, quotas) from your core application business logic.

Designed to work seamlessly with orchestrators like **n8n**, but fully usable as a standalone Python service layer.

## Status
![Status](https://img.shields.io/badge/Status-Active-success)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Django](https://img.shields.io/badge/Django-4.2%2B-green)

## Features
- **Abstract Rights Management**: Decouples "SKU" (what you sell) from "Features" (what the user gets).
- **Flexible Quotas**: Supports Quantity-based, Period-based (subscriptions), and Unlimited product types.
- **Trial & Abuse Protection**: Fingerprints abstract identities to prevent trial reuse.
- **Detachable Architecture**: No foreign keys to your business models (uses metadata).
- **Idempotency**: Built-in protection against double-spending and duplicate payments.
- **REST API**: Ready-to-use Django Ninja API for frontend or external orchestrators.

---

## Documentation

- 📘 **[Architecture & Design](doc/architecture.md)**
  Deep dive into Business Processes, Order Flow, and the "Selector-based" quota logic.
  
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

### 2. Configure URLs

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

---

## Quick Start

### Python Service Layer (Internal Usage)
You can use the module directly in your views or Celery tasks without calling the HTTP API.

**Checking Quota:**
```python
from billable.services import QuotaService

def generate_pdf_report(user):
    # Check if user has the feature "pdf_export" available
    # This automatically checks subscriptions, one-time packs, and trials.
    is_allowed, msg, product, balance = QuotaService.check_quota(user, "pdf_export")

    if not is_allowed:
        raise PermissionError(f"Upgrade required: {msg}")

    # Your logic here...
    print("Generating PDF...")

    # Consume 1 unit of quota (Atomic & Idempotent)
    QuotaService.consume_quota(
        user=user, 
        feature="pdf_export", 
        idempotency_key=f"report_{report_id}"
    )
```

**Creating a Custom Order:**
```python
from billable.services import OrderService

order = OrderService.create_order(
    user=request.user,
    items=[
        {"sku": "premium_monthly", "quantity": 1}
    ],
    metadata={"source": "web_checkout"}
)
```

### REST API Usage
If you are using **n8n** or a frontend:

**Identify user by external identity (recommended first step):**
`POST /api/v1/billing/identify`

**Get Balance:**
`GET /api/v1/billing/balance` (Headers: `Authorization: Bearer <TOKEN>`)

**Confirm Payment:**
`POST /api/v1/billing/orders/{order_id}/confirm`
```json
{
  "payment_id": "stripe_ch_123",
  "status": "paid"
}
```

*For full API details, see the [Reference Guide](doc/reference.md).*
