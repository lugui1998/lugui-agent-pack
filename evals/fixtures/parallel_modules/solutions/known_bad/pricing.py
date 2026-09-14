from contracts import PricingResult


def price_lines(lines, coupon):
    subtotal = sum(line.unit_cents * line.quantity for line in lines)
    discount = sum(round(line.unit_cents * line.quantity * 0.10) for line in lines if line.discountable) if coupon == "SAVE10" else 0
    taxable = sum(line.unit_cents * line.quantity for line in lines if line.taxable) - discount
    return PricingResult(subtotal, discount, taxable)
