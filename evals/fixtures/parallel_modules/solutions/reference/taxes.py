RATES_BP = {"NE": 725, "SW": 850, "EXEMPT": 0}


def tax_for(region, taxable_cents):
    if region not in RATES_BP:
        raise ValueError("unknown region")
    if not isinstance(taxable_cents, int) or taxable_cents < 0:
        raise ValueError("taxable cents must be a nonnegative integer")
    return (taxable_cents * RATES_BP[region] + 5000) // 10000
