# Architecture & Design

This document describes the business logic, process flows, and architectural principles of the **Universal Billable Module**.

## Overview

The module is designed as an isolated **Billing Engine** responsible for rights management and payments accounting. It abstracts the complexity of monetization from the main application logic.

It adheres to the **"Detachable"** principle: the module does not contain the business logic of a specific product (e.g., generating a report) but delegates orchestration to external systems (e.g., n8n, Airflow) or client applications. The module provides a **single API and accounting layer** for different orchestrators (n8n, bots, web), so each can use the same billing flows and data.

**Terminology**: In this module, *user* denotes the **Billing account** — the entity to which orders, quotas, and product rights are attributed.

---

## Business Processes

### 1. Onboarding and Identity
*   **External Identification**: The module works with abstract user identities. Orchestrators (messaging bots, web apps, n8n) call the billing API with an external identity (provider + external_id).
*   **ExternalIdentity mapping**: The module stores external identifiers in `ExternalIdentity` and can optionally link them to `settings.AUTH_USER_MODEL`.
    *   `provider` is a string (e.g., `telegram`, `max`, `n8n`). If not provided, it defaults to `"default"`.
    *   Uniqueness is enforced for `(provider, external_id)`.
    *   **Resolution**: Use `ExternalIdentity.get_user_by_identity` (or its async version `aget_user_by_identity`) to resolve an external ID to a local `User` object.
*   **Identify flow**: Orchestrators should call `POST /identify` at the start of a flow. The module always ensures a local `User` exists (creates and links if missing) and returns `user_id` for subsequent billing calls.
*   **Migration of existing identities**: If identifiers are stored in fields on the User model (e.g. `telegram_id`, `chat_id`, `stripe_id`), the management command `migrate_identities` creates corresponding `ExternalIdentity` records in bulk. The command is idempotent and can be run multiple times for different field/provider pairs. See [Reference — Management Commands](reference.md#management-commands).
*   **Abuse Protection**: The system provides `TrialHistory` model with SHA-256 identity hashing as a **tool** for fraud prevention. User identifiers are normalized to **lowercase** before hashing to ensure consistency across different input sources.
*   **Quota Check**: Before offering services, the system checks the user's quota balance by `product_key`.

### 2. Order Life Cycle (Order Flow)
The flow separates order creation from invoice generation to ensure data integrity.

**Recommended flow (create order → invoice payload → webhook → confirm):**

1.  **Initiation**: An `Order` is created via the API or service layer **before** sending an invoice to the client.
    *   **Catalog Lookup**: Clients fetch offers via `GET /catalog` (full list or bulk by `sku` query param) or `GET /catalog/{sku}` (single offer by SKU). SKU is unique; matching is exact (case-sensitive).
    *   Data: List of offers (`sku`), quantity, price.
    *   Metadata: Application IDs (e.g., `report_id`) are stored in JSON `metadata`.
    *   Status: `PENDING`.
2.  **Invoice Creation**: The `order_id` is passed to the external payment provider (in the invoice payload). This links the future payment to the database record.
3.  **Payment**: Processing happens externally (e.g., Stripe, YooKassa, Telegram Payments).
4.  **Webhook**: The payment provider sends a callback/webhook to **your application** (not to billable). Your application parses the webhook, extracts `order_id` and `payment_id`, and calls `POST /orders/{order_id}/confirm` with those values. Billable does not expose a built-in webhook endpoint; the app is responsible for receiving Stripe/YooKassa/Telegram webhooks and delegating confirmation to the billing API.
5.  **Confirmation**: The system confirms the order via `POST /orders/{order_id}/confirm`.
    *   **Atomicity**: The system transitions the order to `PAID`, sets timestamps, and creates `QuotaBatch` records via `TransactionService.grant_offer()`.
    *   **Idempotency**: Reprocessing the same `payment_id` does not create duplicate batches or transactions; repeated confirm calls with the same `payment_id` are safe.
6.  **Customer Merging**:
    *   **Process**: Moves all financial data (orders, batches, transactions, identities, referrals) from a `source_user` to a `target_user`.
    *   **Conflict Resolution**: If both users have identities for the same provider, the system ensures they match or raises a conflict error.
    *   **Referrals**: Automatically handles referral links to avoid self-referral after merging.
7.  **Refund/Cancellation**:
    *   **Cancellation**: Possible for `PENDING` orders.
    *   **Refund**: For `PAID` orders. The system transitions the order to `REFUNDED`, finds all associated `QuotaBatch` records, creates `DEBIT` transactions for any remaining quantity, and marks batches as `REVOKED`. This ensures a clean audit trail in the ledger.

### 3. Purchase Flows: Real Money vs. Internal Currency

The system distinguishes between two ways of acquiring products:

#### A. Real Money Purchase (RUB, USD, XTR, etc.)
This flow is managed via the **Order Life Cycle**:
1.  **Entry Point**: `OrderService.process_payment(order_id, payment_id=..., payment_method=...)`.
2.  **Activation**: Once the order status is updated to `PAID`, the system iterates through all `OrderItem` records.
3.  **Granting**: For each item, it calls `TransactionService.grant_offer(user_id, offer, order_item=item, source="purchase")`.
4.  **Result**: This creates a `QuotaBatch` linked to the specific `order_item` and a `Transaction` (CREDIT) with `action_type="purchase"`. This ensures a full audit trail for future refunds or partial returns.

#### B. Internal Currency Exchange (INTERNAL)
This flow is a specialized "buy with balance" mechanism:
1.  **Entry Point**: `POST /exchange/` API endpoint or `TransactionService.exchange(...)`.
2.  **Debit**: The system first consumes the "internal currency" product from the user's balance using FIFO logic.
3.  **Credit**: Upon successful debit, it grants the target offer via `TransactionService.grant_offer(source="exchange")`.
4.  **Atomicity**: Both operations (Debit internal + Grant target) are wrapped in a single database transaction.

### 4. Identification Policy

The system enforces a strict distinction between technical resources and commercial deals:

-   **Shared Namespace (Zero Collision)**: It is strictly forbidden for a `product_key` to match an Offer `sku`. Any attempt to create a duplicate at the DB level will trigger an error.
-   **Contract Separation**:
    -   **Access/Balance** methods (checking rights) accept `product_key`.
    -   **Grant/Purchase** methods (giving rights) accept `sku`.
-   **Naming Convention**:
    -   `product_key`: **What** is being tracked (e.g., `DIAMONDS`, `VIP_ACCESS`). Stored in **uppercase (CAPS)**.
    -   `sku`: **How** it is sold (e.g., `OFF_DIAMONDS`, `PACK_VIP_30D`). Prefixes: `OFF_` (base), `PACK_` (bundle), `PROMO_` (sale). Stored in **uppercase (CAPS)**.

### 5. Normalization Policy (CAPS)

The system enforces consistent uppercase storage for technical identifiers:
1.  **Silent Normalization**: API and Service methods accept any case and automatically call `.upper()` before database operations.
2.  **Zero Collisions**: Since all keys are uppercase, `gold_100` and `GOLD_100` are treated as the same entity.
3.  **Exception (Trial Hashes)**: User emails and IDs are hashed in **lowercase** to maintain compatibility with external systems.

### 6. Transaction Engine (Entitlement Management)
The module uses a **Transaction-based Ledger** approach where all balance changes are recorded as immutable transactions.

*   **QuotaBatch**: The source of truth for user rights. Each batch represents a portion of a product granted to a user.
    *   `initial_quantity`: Original amount granted.
    *   `remaining_quantity`: Current balance.
    *   `expires_at`: Optional expiration date.
    *   `state`: ACTIVE, EXHAUSTED, or EXPIRED.
*   **Transaction**: Immutable record of every balance change:
    *   `direction`: CREDIT (grant) or DEBIT (consume).
    *   `action_type`: Source of the transaction (e.g., "purchase", "trial_activation", "usage").
    *   `quota_batch`: Link to the affected batch.
*   **FIFO Consumption**: When consuming quota, the system automatically uses the oldest active batch first (ordered by `created_at ASC`).
*   **Product Key Resolution**: When checking quota for a `product_key`, the system matches by `Product.product_key` only.

### 6. Referral Program
*   **Chains**: Stores `referrer -> referee` links in the `Referral` model.
*   **Bonuses**: The module provides **signals** (`referral_attached`, `transaction_created`) for your application to implement bonus logic.
*   **Verification**: Use `TrialHistory` to prevent bonus abuse.

---

## System Architecture

The architecture consists of three distinct layers:

### 1. Core Engine (`billable`)
*   **Responsibility**: Database integrity, atomic transactions, API exposure.
*   **Dependencies**: Zero hard dependencies on other apps. Uses `settings.AUTH_USER_MODEL` for user linking.
*   **Storage**: Uses JSONB for extensibility (storing application-specific IDs like `report_id` in metadata).
*   **What it DOES provide**:
    *   Transaction ledger (`TransactionService`)
    *   Balance queries (`BalanceService`)
    *   Order management (`OrderService`)
    *   Fraud prevention tools (`TrialHistory`)
    *   Django signals for integration
*   **What it DOES NOT provide**:
    *   Business rules for promotions/bonuses
    *   Multi-channel communications (WhatsApp, Email, SMS)
    *   A/B testing or campaign analytics
    *   Banner/popup management

### 2. Application Layer (Your Code)
*   **Responsibility**: Implements business-specific promotion logic, bonus campaigns, and user communications.
*   **Recommended Services**:
    *   `PromotionService`: Orchestrates trial/bonus grants using `TransactionService.grant_offer()`.
    *   `NotificationService`: Sends WhatsApp/Email/SMS notifications on balance changes.
    *   `CampaignService`: Manages A/B tests, banners, and segmentation.
*   **Integration**: Subscribes to `billable` signals (`transaction_created`, `order_confirmed`) to trigger application logic.

### 3. Orchestrator (e.g., n8n, Customer.io)
*   **Responsibility**: "Glue Logic". Connects external platforms (Telegram, Web), payment gateways, and marketing automation.
*   **Flow**: Maps business events (e.g., `/start` command) to the Identity Layer and then to the Billing API.

---

## Technical Integration

### Import Policy
To ensure compatibility with Django's application registry (especially during tests), always import models and services from their respective submodules. **Never** import from the root `billable` package.

*   **Models**: `from billable.models import ...`
*   **Services**: `from billable.services import ...`

### Service Layer

The module exposes Python services for internal usage (Workers/Celery). For use from **async context** (bots, ASGI), prefer the **async methods** of the API and services (e.g. `TransactionService.acheck_quota`, `OrderService.acreate_order`); they avoid blocking the event loop and integrate cleanly with async callers.

*   **TransactionService**: The core entitlement engine. Handles granting (`grant_offer`), consumption (`consume_quota`), balance checks (`check_quota`), exchange (`exchange`), and expiration (`expire_batches`).
*   **BalanceService**: Queries the user's inventory. Capable of filtering active batches by `product_key` and calculating aggregate balances.
*   **OrderService**: Handles the financial lifecycle. Creates multi-item orders, processes payments, and manages refunds/cancellations.
*   **CustomerService**: Manages customer-centric operations. Implements `merge_customers` (and async `amerge_customers`) to consolidate user accounts while preserving ledger integrity.
*   **ProductService**: Catalog management. Retrieves products by `product_key` or feature tags.

---

## Implementing Promotion Logic

The `billable` module provides **building blocks**, not complete promotion campaigns. Here's the recommended pattern:

### Example: Welcome Trial

**In your application code** (e.g., `your_app/services/promotion_service.py`):

```python
from billable.models import Offer, TrialHistory
from billable.services import TransactionService
from asgiref.sync import sync_to_async

class PromotionService:
    @classmethod
    async def claim_welcome_trial(cls, user_id: int, telegram_id: str):
        # 1. Fraud check (using billable tool)
        identities = {"telegram": telegram_id}
        if await TrialHistory.ahas_used_trial(identities=identities):
            return {"success": False, "reason": "trial_already_used"}
        
        # 2. Find trial offer (create in Django admin)
        offer = await Offer.objects.aget(sku="off_welcome_trial")
        
        # 3. Grant using billable engine
        batches = await sync_to_async(TransactionService.grant_offer)(
            user_id=user_id,
            offer=offer,
            source="welcome_bonus"
        )
        
        # 4. Mark as used
        await TrialHistory.objects.acreate(
            identity_type="telegram",
            identity_hash=TrialHistory.generate_identity_hash(telegram_id),
            trial_plan_name="Welcome Trial"
        )
        
        return {"success": True, "batches": batches}
```

### Example: Referral Bonus

**Subscribe to signals** (in `your_app/signals/handlers.py`):

```python
from django.dispatch import receiver
from billable.signals import order_confirmed
from billable.services import TransactionService

@receiver(order_confirmed)
async def on_first_purchase(sender, order, **kwargs):
    # Check if this is the first purchase
    if not await Order.objects.filter(user=order.user, status=Order.Status.PAID).exclude(id=order.id).aexists():
        
        # Atomically claim bonus
        referral = await Referral.objects.filter(referee=order.user).afirst()
        
        if referral:
             # Sync wrapper or async equivalent for model method required in async context
             claimed = await sync_to_async(referral.claim_bonus)()
             
             if claimed:
                bonus_offer = await Offer.objects.aget(sku="off_referral_bonus")
                await sync_to_async(TransactionService.grant_offer)(
                    user_id=referral.referrer_id,
                    offer=bonus_offer,
                    source="referral_bonus",
                    metadata={
                        "referee_id": referral.referee_id,  # Required for webhook payload
                        "order_id": order.id,  # Required for webhook payload
                    }
                )
```

**Important**: When creating a referral bonus transaction, always include `referee_id` and `order_id` in the `metadata` parameter. This ensures that webhook payloads (e.g., `referral_bonus_granted` events) can include `referee_external_id` by looking up the referee's `ExternalIdentity` record. Without these fields in metadata, the webhook will only contain `referrer_external_id` and `referee_external_id` will be `null`.

---

## Design Principles

1.  **No Hardlinks**: No `ForeignKey` relationships to external application models. All links are logical (stored in metadata).
2.  **Settings Based**: Configuration (API tokens, User model) is injected via Django `settings.py`.
3.  **Event Driven**: Generates Django Signals (`order_confirmed`, `transaction_created`, `quota_consumed`) for decoupled integration with other local modules.
4.  **Idempotency**: Built-in protection against double-spending and duplicate processing at both the Order and Transaction levels.
5.  **Separation of Concerns**: The billing engine handles **accounting**, not **marketing**. Promotion logic belongs in your application layer.