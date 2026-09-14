from contracts import PricingResult


def price_lines(lines, coupon):
    if coupon not in {"NONE", "SAVE10"}:
        raise ValueError("unknown coupon")
    subtotal = 0
    discount = 0
    taxable_after_discount = 0
    for line in lines:
        if not isinstance(line.unit_cents, int) or not isinstance(line.quantity, int):
            raise ValueError("amounts and quantities must be integers")
        if line.unit_cents < 0 or line.quantity < 0:
            raise ValueError("negative line value")
        extended = line.unit_cents * line.quantity
        line_discount = (extended + 5) // 10 if coupon == "SAVE10" and line.discountable else 0
        subtotal += extended
        discount += line_discount
        if line.taxable:
            taxable_after_discount += extended - line_discount
    return PricingResult(subtotal, discount, taxable_after_discount)
