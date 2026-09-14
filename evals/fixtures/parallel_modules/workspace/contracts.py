from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    sku: str
    unit_cents: int
    quantity: int
    taxable: bool
    discountable: bool


@dataclass(frozen=True)
class PricingResult:
    subtotal_cents: int
    discount_cents: int
    taxable_after_discount_cents: int


@dataclass(frozen=True)
class InvoiceResult:
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
