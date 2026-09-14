def tax_for(region, taxable_cents):
    rates = {"NE": 0.0725, "SW": 0.085, "EXEMPT": 0.0}
    return round(taxable_cents * rates[region])
