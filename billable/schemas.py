"""Pydantic schemas for the billable API.

Define the structure of input and output data for Ninja API endpoints.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class BaseSchema(BaseModel):
    """Base schema with ORM/Django model support."""
    model_config = ConfigDict(from_attributes=True)


class ProductSchema(BaseSchema):
    """Product schema."""
    id: int
    sku: str | None
    name: str
    description: str
    product_type: str
    price: Decimal
    currency: str
    period_days: int | None
    quantity: int | None
    is_active: bool
    metadata: dict[str, Any]


class UserProductSchema(BaseSchema):
    """User product schema (active)."""
    id: int
    product: ProductSchema
    purchased_at: datetime
    expires_at: datetime | None
    total_quantity: int
    used_quantity: int
    is_active: bool
    remaining: int | None = None


class OrderItemSchema(BaseSchema):
    """Order item schema."""
    id: int
    product_id: int
    product_name: str = ""
    sku: str | None = None
    quantity: int
    price: Decimal
    total_quantity: int
    period_days: int | None

    @model_validator(mode="before")
    @classmethod
    def extract_product_data(cls, data):
        """Extract product_name and sku from related Product object."""
        # If data is already a dict, return as is
        if isinstance(data, dict):
            return data
        
        # If data is an OrderItem object with product loaded
        if hasattr(data, "product") and data.product is not None:
            # Create a dict with all attributes
            result = {
                "id": data.id,
                "product_id": data.product_id,
                "product_name": getattr(data.product, "name", ""),
                "sku": getattr(data.product, "sku", None),
                "quantity": data.quantity,
                "price": data.price,
                "total_quantity": data.total_quantity,
                "period_days": data.period_days,
            }
            return result
        
        # Fallback: try to get attributes directly if product is not loaded
        result = {
            "id": getattr(data, "id", None),
            "product_id": getattr(data, "product_id", None),
            "product_name": "",
            "sku": None,
            "quantity": getattr(data, "quantity", None),
            "price": getattr(data, "price", None),
            "total_quantity": getattr(data, "total_quantity", None),
            "period_days": getattr(data, "period_days", None),
        }
        return result


class OrderSchema(BaseSchema):
    """Order schema."""
    id: int
    user_id: int
    status: str
    total_amount: Decimal
    currency: str
    payment_method: str | None
    payment_id: str | None
    created_at: datetime
    paid_at: datetime | None
    items: list[OrderItemSchema] = []
    metadata: dict[str, Any]


# --- Schemas for Input Data ---

class OrderCreateSchema(BaseModel):
    """Schema for creating a new order."""
    user_id: int
    products: list[dict[str, Any]] = Field(..., json_schema_extra={"help_text": "List of {'sku': str, 'quantity': int}"})
    metadata: dict[str, Any] | None = None


class OrderConfirmSchema(BaseModel):
    """Schema for confirming order payment."""
    payment_method: str = "provider_payments"
    payment_id: str | None = None
    status: str = "paid"


class QuotaConsumeSchema(BaseModel):
    """Schema for quota consumption."""
    user_id: int
    feature: str
    action_type: str
    action_id: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] | None = None


class QuotaCheckSchema(BaseModel):
    """Schema for quota check."""
    user_id: int
    feature: str


class TrialGrantSchema(BaseModel):
    """Schema for trial grant."""
    user_id: int
    telegram_id: str | None = None
    identities: dict[str, str | int] | None = None
    sku: str | None = None
    grant_type: str = "trial"


class ReferralAssignSchema(BaseModel):
    """Schema for assigning a referral link."""
    referrer_id: int
    referee_id: int
    metadata: dict[str, Any] | None = None


# --- Schemas for Output Data (Responses) ---

class BalanceFeatureSchema(BaseModel):
    """Schema for balance of a specific feature."""
    can_use: bool
    feature: str
    remaining: int | None
    message: str


class BalanceSummarySchema(BaseModel):
    """Summary balance schema."""
    user_id: int
    features: dict[str, dict[str, Any]]


class CommonResponse(BaseModel):
    """General successful response schema."""
    success: bool
    message: str
    data: dict[str, Any] | None = None
