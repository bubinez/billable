# Architecture & Design

This document describes the business logic, process flows, and architectural principles of the **Universal Billable Module**.

## Overview

The module is designed as an isolated **Billing Engine** responsible for rights management and payments accounting. It abstracts the complexity of monetization from the main application logic.

It adheres to the **"Detachable"** principle: the module does not contain the business logic of a specific product (e.g., generating a report) but delegates orchestration to external systems (e.g., n8n, Airflow) or client applications.

**Terminology**: In this module, *user* denotes the **Billing account** — the entity to which orders, quotas, and product rights are attributed.

---

## Business Processes

### 1. Onboarding and Identity
*   **External Identification**: The module works with abstract user identities. Orchestrators (messaging bots, web apps, n8n) call the billing API with an external identity (provider + external_id).
*   **ExternalIdentity mapping**: The module stores external identifiers in `ExternalIdentity` and can optionally link them to `settings.AUTH_USER_MODEL`.
    *   `provider` is a string (e.g., `telegram`, `max`, `n8n`). If not provided, it defaults to `"default"`.
    *   Uniqueness is enforced for `(provider, external_id)`.
*   **Identify flow**: Orchestrators should call `POST /identify` at the start of a flow. The module always ensures a local `User` exists (creates and links if missing) and returns `user_id` for subsequent billing calls.
*   **Trial Accruals**: "First report for free" logic is implemented by granting products with the `trial` feature.
*   **Abuse Protection**: The system checks trial usage history via the `TrialHistory` model using SHA-256 identity hashes. This protects against trial reuse across different providers or accounts.
*   **Quota Check**: Before offering services, the system checks the user's quota balance by feature name.

### 2. Order Life Cycle (Order Flow)
The flow separates order creation from invoice generation to ensure data integrity.

1.  **Initiation**: An `Order` is created via the API or service layer **before** sending an invoice to the client.
    *   Data: List of products (SKU/ID), quantity, price.
    *   Metadata: Application IDs (e.g., `report_id`) are stored in JSON `metadata`.
    *   Status: `PENDING`.
2.  **Invoice Creation**: The `order_id` is passed to the external payment provider (in the invoice payload). This links the future payment to the database record.
3.  **Payment**: Processing happens externally (e.g., Stripe, Telegram Payments via n8n).
4.  **Confirmation**: Upon successful payment, the provider creates a callback/webhook. The system confirms the order via `POST /orders/{order_id}/confirm`.
    *   **Atomicity**: The system transitions the order to `PAID`, sets timestamps, and creates `UserProduct` records transactionally.
    *   **Idempotency**: Reprocessing the same `payment_id` does not create duplicate products.

### 3. Consumption Control (Quota Management)
The module uses a **Selector-based approach** (SKU-first, feature-fallback) to determine what rights a user has.

*   **Resolution Logic**: When a system asks "Can user X do Y?":
    1.  First, the engine tries to match "Y" as a `Product.sku` (case-insensitive).
    2.  If no active product is found by SKU, it falls back to matching "Y" as a feature name inside `Product.metadata.features` (e.g., `['resume_lift', 'vacancy_response']`).
*   **Product Types**:
    *   **Quantity**: Decrements a counter (`used_quantity`). Deactivates when limit is reached.
    *   **Period**: Checks `expires_at`. Consumption is logged but doesn't affect validity.
    *   **Unlimited**: Always available. Consumption is for audit trails only.

### 4. Referral Program
*   **Chains**: Stores `referrer -> referee` links.
*   **Bonuses**: Logic for accruing bonuses is managed via the API/Service layer (triggering free orders or direct quota grants).
*   **Verification**: Uses `TrialHistory` to prevent bonus abuse.

---

## System Architecture

The architecture consists of three distinct layers:

### 1. Core Engine (`billable`)
*   **Responsibility**: Database integrity, atomic transactions, API exposure.
*   **Dependencies**: Zero hard dependencies on other apps. Uses `settings.AUTH_USER_MODEL` for user linking.
*   **Storage**: Uses JSONB for extensibility (storing application-specific IDs like `report_id` in metadata).

### 2. Orchestrator (e.g., n8n)
*   **Responsibility**: "Glue Logic". It knows about external platforms (Telegram, Web), payment gateways, and specific application flows.
*   **Flow**: Maps business events (e.g., `/start` command) to the Identity Layer and then to the Billing API.

### 3. Consumer (Client Apps)
*   **Responsibility**: Consumes services (e.g., generating a PDF) without knowing about payment logic.
*   **Interaction**: Calls `check_quota()` and `consume_quota()` methods via the Service Layer.

---

## Service Layer

The module exposes Python services for internal usage (Workers/Celery):

*   **QuotaService**: The central entry point. Handles availability checks, atomic consumption with idempotency keys, and trial activations.
*   **UserProductService**: Manages the inventory of user rights. capable of filtering active products by features and calculating balances.
*   **OrderService**: Handles the financial lifecycle. Creates multi-item orders, processes payments, and manages refunds/cancellations.
*   **ProductService**: Catalog management. Retrieves products by SKU or feature tags.

---

## Design Principles

1.  **No Hardlinks**: No `ForeignKey` relationships to external application models. All links are logical (stored in metadata).
2.  **Settings Based**: Configuration (API tokens, User model) is injected via Django `settings.py`.
3.  **Event Driven**: Generates Django Signals (`order_paid`, `quota_consumed`) for decoupled integration with other local modules.
4.  **Feature Based**: Products define capabilities via `metadata.features`, allowing flexible repackaging of SKUs without changing code.
5.  **Idempotency**: Built-in protection against double-spending and duplicate processing at both the Order and Quota levels.