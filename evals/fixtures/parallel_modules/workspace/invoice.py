from contracts import InvoiceResult
from pricing import price_lines
from taxes import tax_for


def build_invoice(lines, coupon, region):
    pricing = price_lines(lines, coupon)
    tax_cents = tax_for(region, pricing.taxable_after_discount_cents)
    return InvoiceResult(
        subtotal_cents=pricing.subtotal_cents,
        discount_cents=pricing.discount_cents,
        tax_cents=tax_cents,
        total_cents=pricing.subtotal_cents - pricing.discount_cents + tax_cents,
    )
