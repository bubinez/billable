"""Pydantic schemas for the billable API.

Define the structure of input and output data for Ninja API endpoints.
All request/response shapes used by the REST API are declared here;
Field descriptions are exposed in the OpenAPI schema for interactive docs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, List, Dict
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class BaseSchema(BaseModel):
    """Base schema with ORM/Django model support.

    Enables population from Django model instances via from_attributes.
    """

    model_config = ConfigDict(from_attributes=True)


class ProductSchema(BaseSchema):
    """Catalog product: the unit of accounting (e.g. diamonds, vip_access).

    Product defines what is tracked; price and quantity are defined in Offer.
    """

    id: int = Field(..., description="Primary key of the product.")
    product_key: str | None = Field(None, description="Unique identifier for accounting (e.g. DIAMONDS, VIP_ACCESS). Stored in uppercase (CAPS). Noun, singular.")
    name: str = Field(..., description="Display name of the product.")
    description: str = Field("", description="Optional text description.")
    product_type: str = Field(..., description="Type: PERIOD (time-based), QUANTITY (consumable), UNLIMITED (permanent).")
    is_active: bool = Field(..., description="If false, product cannot be used in new offers.")
    metadata: dict[str, Any] = Field(..., description="JSON configuration; may include 'features' list.")
    created_at: datetime = Field(..., description="Creation timestamp.")


class ActiveBatchSchema(BaseSchema):
    """Active quota batch: a grant of a product to a user (user's wallet entry).

    Represents one portion of balance; multiple batches sum to total balance per product_key.
    """

    id: UUID = Field(..., description="Unique batch identifier.")
    product: ProductSchema = Field(..., description="The product this batch grants.")
    purchased_at: datetime = Field(..., validation_alias="created_at", description="When the batch was created (grant time).")
    expires_at: datetime | None = Field(None, description="Optional expiration; null means no expiry.")
    total_quantity: int = Field(..., validation_alias="initial_quantity", description="Original quantity granted.")
    used_quantity: int = Field(0, description="Quantity already consumed from this batch.")
    is_active: bool = Field(True, description="True if batch is ACTIVE and can be consumed.")
    remaining: int | None = Field(None, validation_alias="remaining_quantity", description="Remaining quantity in this batch.")

    @model_validator(mode="before")
    @classmethod
    def calculate_fields(cls, data: Any) -> Any:
        if hasattr(data, "initial_quantity") and hasattr(data, "remaining_quantity"):
            # Model instance
            data.used_quantity = data.initial_quantity - data.remaining_quantity
            from .models import QuotaBatch
            data.is_active = (data.state == QuotaBatch.State.ACTIVE)
        elif isinstance(data, dict):
            if "initial_quantity" in data and "remaining_quantity" in data:
                data["used_quantity"] = data["initial_quantity"] - data["remaining_quantity"]
            if "state" in data:
                data["is_active"] = (data["state"] == "ACTIVE")
        return data


class OfferItemSchema(BaseSchema):
    """Single line item within an offer: links a product to quantity and expiration rules."""

    product: ProductSchema = Field(..., description="The product included in this offer.")
    quantity: int = Field(..., description="Number of units of the product granted.")
    period_unit: str = Field(..., description="Expiration unit: DAYS, MONTHS, YEARS, FOREVER.")
    period_value: int | None = Field(None, description="Numeric value for expiration; null for FOREVER.")


class OfferSchema(BaseSchema):
    """Offer (catalog item): a sellable bundle with price and linked products.

    SKU prefixes: off_ (base), pack_ (bundle), promo_ (sale). Currency may be INTERNAL for balance exchange.
    """

    sku: str = Field(..., description="Commercial identifier (e.g. OFF_DIAMONDS_100, PACK_PREMIUM). Stored in uppercase (CAPS).")
    name: str = Field(..., description="Display name of the offer.")
    price: Decimal = Field(..., description="Price per unit.")
    currency: str = Field(..., description="Currency code: EUR, USD, XTR, INTERNAL, etc.")
    description: str = Field(..., description="Optional description for UI.")
    image: str | None = Field(None, description="Optional image URL.")
    is_active: bool = Field(..., description="If false, offer is hidden from catalog.")
    items: List[OfferItemSchema] = Field(default_factory=list, description="List of products and quantities in this offer.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional configuration (JSON).")

    @field_validator("image", mode="before")
    @classmethod
    def validate_image(cls, v: Any) -> str | None:
        if not v:
            return None
        # Robust check for Django ImageFieldFile
        try:
            if hasattr(v, "url"):
                return v.url
        except Exception:
             return None
        
        if isinstance(v, str):
            return v
        return str(v)

    @field_validator("items", mode="before")
    @classmethod
    def validate_items(cls, v: Any) -> list:
         if hasattr(v, "all"):
              return list(v.all())
         return v


class QuotaBatchSchema(BaseSchema):
    """Quota batch: one grant of a product to a user (wallet entry with state)."""

    id: UUID = Field(..., description="Unique batch identifier.")
    product: ProductSchema = Field(..., description="The product this batch grants.")
    initial_quantity: int = Field(..., description="Original quantity granted.")
    remaining_quantity: int = Field(..., description="Current remaining quantity.")
    valid_from: datetime = Field(..., description="Start of validity period.")
    expires_at: datetime | None = Field(None, description="End of validity; null if no expiry.")
    state: str = Field(..., description="Batch state: ACTIVE, EXHAUSTED, EXPIRED, REVOKED.")


class TransactionSchema(BaseSchema):
    """Immutable ledger record of a single balance change (credit or debit)."""

    id: UUID = Field(..., description="Unique transaction identifier.")
    user_id: int = Field(..., description="Billing user this transaction belongs to.")
    amount: int = Field(..., description="Quantity changed (positive for credit, consumed for debit).")
    direction: str = Field(..., description="CREDIT (grant) or DEBIT (consume).")
    action_type: str = Field(..., description="Source: purchase, trial_activation, usage, exchange, etc.")
    created_at: datetime = Field(..., description="When the transaction was recorded.")
    metadata: dict[str, Any] = Field(..., description="Context (e.g. order_item_id, idempotency_key).")


class OrderItemSchema(BaseSchema):
    """Single line item within an order: one offer and quantity at a fixed price."""

    id: int = Field(..., description="Order item primary key.")
    sku: str = Field(..., alias="offer_sku", description="Offer SKU (e.g. off_diamonds_100).")
    quantity: int = Field(..., description="Number of offers purchased.")
    price: Decimal = Field(..., description="Price per offer at the moment of purchase.")

    @model_validator(mode="before")
    @classmethod
    def extract_order_item_data(cls, data: Any) -> Any:
        """Simple mapping from OrderItem model or dict."""
        if isinstance(data, dict):
            # Support both 'sku' and 'offer_sku' in dicts
            if "offer_sku" not in data and "sku" in data:
                data["offer_sku"] = data["sku"]
            return data
        
        if hasattr(data, "offer") and data.offer:
            return {
                "id": data.id,
                "offer_sku": data.offer.sku,
                "quantity": data.quantity,
                "price": data.price,
            }
        return data


class OrderSchema(BaseSchema):
    """Order: a financial intent (cart) with status and line items.

    Created before invoice; confirmed when payment is received. Status: PENDING, PAID, CANCELLED, REFUNDED.
    """

    id: int = Field(..., description="Order primary key.")
    user_id: int = Field(..., description="Billing user who owns the order.")
    status: str = Field(..., description="PENDING, PAID, CANCELLED, or REFUNDED.")
    total_amount: Decimal = Field(..., description="Total order amount.")
    currency: str = Field(..., description="Currency code.")
    payment_method: str | None = Field(None, description="Payment provider (e.g. stripe, telegram_payments); set on confirm.")
    payment_id: str | None = Field(None, description="External transaction ID for idempotency.")
    created_at: datetime = Field(..., description="When the order was created.")
    paid_at: datetime | None = Field(None, description="When the order was paid; null until confirmed.")
    items: list[OrderItemSchema] = Field(default_factory=list, description="Line items (offer SKU, quantity, price).")
    metadata: dict[str, Any] = Field(..., description="Application-specific data (e.g. report_id).")


# --- Schemas for Input Data ---

class OrderCreateSchema(BaseModel):
    """Request body for creating a new order (financial intent before invoice).

    Provide either user_id (local) or (external_id + provider). Items list is required.
    """

    user_id: int | None = Field(None, description="Local billing user ID; required if external_id not provided.")
    external_id: str | None = Field(None, description="External identifier (e.g. telegram chat id); used with provider.")
    provider: str | None = Field(None, description="Identity provider (e.g. telegram, n8n). Defaults to 'default'.")
    items: list[dict[str, Any]] = Field(..., description="List of {sku: str, quantity: int}. SKU is automatically normalized to uppercase. At least one item required.")
    metadata: dict[str, Any] | None = Field(None, description="Optional application-specific payload (e.g. report_id).")


class OrderConfirmSchema(BaseModel):
    """Request body for confirming order payment (called by payment webhook).

    After confirmation, order status becomes PAID and products are granted via TransactionService.
    """

    payment_method: str = Field("provider_payments", description="Payment provider identifier (e.g. stripe, telegram_payments).")
    payment_id: str | None = Field(None, description="External transaction ID; used for idempotency (no duplicate grants).")
    status: str = Field("paid", description="Status to set; typically 'paid'.")


class OrderRefundSchema(BaseModel):
    """Request body for refunding a paid order.
    """

    reason: str | None = Field(None, description="Optional reason for the refund.")


class QuotaConsumeSchema(BaseModel):
    """Request body for consuming quota (admin or server-to-server).

    Provide either user_id or (external_id + provider). Idempotency_key prevents double consumption.
    """

    user_id: int | None = Field(None, description="Local billing user ID; required if external_id not provided.")
    external_id: str | None = Field(None, description="External identifier; used with provider to resolve user.")
    provider: str | None = Field(None, description="Identity provider. Defaults to 'default'.")
    product_key: str = Field(..., description="Product key to consume (e.g. PDF_EXPORT, DIAMONDS). Automatically normalized to uppercase.")
    action_type: str = Field(..., description="Reason for consumption (e.g. usage, admin_adjustment).")
    action_id: str | None = Field(None, description="Optional external reference (e.g. report_id).")
    idempotency_key: str | None = Field(None, description="Optional key to prevent duplicate consumption for same action.")
    metadata: dict[str, Any] | None = Field(None, description="Optional context (JSON).")


class QuotaCheckSchema(BaseModel):
    """Request body for checking quota without consuming.

    Provide either user_id or (external_id + provider).
    """

    user_id: int | None = Field(None, description="Local billing user ID.")
    external_id: str | None = Field(None, description="External identifier; used with provider.")
    provider: str | None = Field(None, description="Identity provider. Defaults to 'default'.")
    product_key: str = Field(..., description="Product key to check (e.g. PDF_EXPORT). Automatically normalized to uppercase.")


class TrialGrantSchema(BaseModel):
    """Request body for demo/reference trial grant endpoint.

    Uses TrialHistory to prevent double-granting. Provide user_id or (external_id + provider).
    """

    user_id: int | None = Field(None, description="Local billing user ID; required if external_id not provided.")
    external_id: str | None = Field(None, description="External identifier; used with provider.")
    provider: str | None = Field(None, description="Identity provider. Defaults to 'default'.")
    sku: str | None = Field(None, description="Offer SKU to grant as trial (e.g. OFF_TRIAL_PACK). Automatically normalized to uppercase.")
    grant_type: str = Field("trial", description="Grant type label; typically 'trial'.")


class IdentifySchemaIn(BaseModel):
    """Request body for identifying an external identity and ensuring a local user exists.

    Creates ExternalIdentity and User if missing. Call this at the start of a flow before other billing calls.
    """

    provider: str | None = Field(None, description="Identity source (e.g. telegram, max, n8n). Defaults to 'default'.")
    external_id: str = Field(..., description="Stable external identifier (e.g. telegram user id).")
    profile: dict[str, Any] | None = Field(None, description="Optional profile (first_name, last_name, telegram_username, etc.).")

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, v: str) -> str:
        """Validate that external_id is not empty after stripping."""
        if not v or not v.strip():
            raise ValueError("external_id cannot be empty or whitespace-only")
        return v.strip()


class IdentifySchemaOut(BaseModel):
    """Response from identify: local user and identity info plus trial eligibility."""

    user_id: int = Field(..., description="Local billing user ID to use in subsequent API calls.")
    identity_id: int = Field(..., description="ExternalIdentity primary key.")
    provider: str = Field(..., description="Identity provider used.")
    external_id: str = Field(..., description="External identifier used.")
    created_identity: bool = Field(..., description="True if ExternalIdentity was just created.")
    created_user: bool = Field(..., description="True if User was just created.")
    trial_eligible: bool = Field(..., description="True if TrialHistory has not recorded a trial for this identity.")
    metadata: dict[str, Any] = Field(..., description="Identity metadata (e.g. profile).")


class ReferralAssignSchema(BaseModel):
    """Request body for creating a referral link between referrer and referee.

    Two modes: by user IDs (referrer_id, referee_id) or by external identity (provider, referrer_external_id, referee_external_id).
    """

    referrer_id: int | None = Field(None, description="Local user ID of the referrer; use with referee_id.")
    referee_id: int | None = Field(None, description="Local user ID of the referee; use with referrer_id.")
    provider: str | None = Field(None, description="Identity provider when using external IDs; defaults to 'default'.")
    referrer_external_id: str | None = Field(None, description="External ID of referrer; use with referee_external_id and provider.")
    referee_external_id: str | None = Field(None, description="External ID of referee; use with referrer_external_id and provider.")
    metadata: dict[str, Any] | None = Field(None, description="Optional payload (JSON).")

    @field_validator("referrer_external_id", "referee_external_id")
    @classmethod
    def validate_external_id(cls, v: str | None) -> str | None:
        """Validate that external_id is not empty if provided."""
        if v is not None:
            stripped = str(v).strip()
            if not stripped:
                raise ValueError("external_id cannot be empty or whitespace-only")
            return stripped
        return v


class ExchangeSchema(BaseModel):
    """Request body for exchanging internal currency for an offer (spend balance, grant SKU).

    Atomically debits internal currency product and grants the target offer. Provide user_id or (external_id + provider).
    """

    user_id: int | None = Field(None, description="Local billing user ID; required if external_id not provided.")
    external_id: str | None = Field(None, description="External identifier; used with provider.")
    provider: str | None = Field(None, description="Identity provider. Defaults to 'default'.")
    sku: str = Field(..., description="Offer SKU to grant (e.g. OFF_PREMIUM_PACK). Automatically normalized to uppercase. Internal currency is consumed automatically.")


# --- Schemas for Output Data (Responses) ---

class BalanceFeatureSchema(BaseModel):
    """Response for balance check: whether user can use a product_key and remaining amount."""

    can_use: bool = Field(..., description="True if user has remaining quota for the product_key.")
    product_key: str = Field(..., description="Product key that was checked.")
    remaining: int | None = Field(None, description="Remaining quantity; null if not applicable.")
    message: str = Field(..., description="Human-readable message (e.g. reason when can_use is false).")


class WalletBalanceSchema(BaseModel):
    """Aggregated wallet balance: user_id and map of product_key -> total remaining quantity."""

    user_id: int = Field(..., description="Billing user ID.")
    balances: dict[str, int] = Field(..., description="Map of product_key to total remaining quantity across all active batches.")


class BalanceSummarySchema(BaseModel):
    """Summary balance by product_key with optional per-product details."""

    user_id: int = Field(..., description="Billing user ID.")
    products: dict[str, dict[str, Any]] = Field(..., description="Map of product_key to summary/details.")


class CommonResponse(BaseModel):
    """Generic API response envelope: success flag, message, and optional data payload."""

    success: bool = Field(..., description="True if the operation succeeded.")
    message: str = Field(..., description="Human-readable status or error message.")
    data: dict[str, Any] | None = Field(None, description="Optional result payload (e.g. order, batches).")


class CustomerMergeSchema(BaseModel):
    """Request body for merging two customers.
    
    Data from source_user will be moved to target_user.
    """

    target_user_id: int = Field(..., description="ID of the user who will remain.")
    source_user_id: int = Field(..., description="ID of the user whose data will be moved.")


class CustomerMergeResponse(BaseSchema):
    """Response from customer merge operation."""

    success: bool = Field(..., description="True if the merge was successful.")
    message: str = Field(..., description="Human-readable status or error message.")
    moved_orders: int = Field(0, description="Number of orders moved.")
    moved_batches: int = Field(0, description="Number of quota batches moved.")
    moved_transactions: int = Field(0, description="Number of transactions moved.")
    moved_identities: int = Field(0, description="Number of external identities moved.")
    moved_referrals: int = Field(0, description="Number of referral links moved.")
