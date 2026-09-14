# Invoice module requirements

`contracts.py` is the agreed shared interface and must remain unchanged. All amounts are integer cents; floats are forbidden.

`pricing.py` owns `price_lines(lines, coupon)`:

- reject negative `unit_cents` or `quantity` with `ValueError`;
- accept only `NONE` and `SAVE10`, otherwise raise `ValueError`;
- subtotal is the sum of every `unit_cents * quantity`;
- `SAVE10` discounts each discountable extended line amount by 10%, independently, rounded half up to a cent;
- taxable-after-discount is the sum of each taxable line amount after that line's discount;
- return `PricingResult`.

`taxes.py` owns `tax_for(region, taxable_cents)`:

- reject a negative or non-integer taxable amount with `ValueError`;
- supported rates are `NE = 725` basis points, `SW = 850`, and `EXEMPT = 0`;
- reject unknown regions with `ValueError`;
- calculate once on the aggregate taxable cents and round half up to a cent using integer arithmetic.

`invoice.py` is the integration owner. It must call both modules and return `InvoiceResult(subtotal_cents, discount_cents, tax_cents, total_cents)`, where total is subtotal minus discount plus tax.

Run the existing checks with `python check_integration.py` after implementation. Keep that check file unchanged. Its checks cover the shared interface, integration, rounding, and representative invalid inputs; report the actual command result rather than rebuilding the same checks as shell one-liners.
