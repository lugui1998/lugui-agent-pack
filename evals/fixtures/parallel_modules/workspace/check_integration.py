"""Visible project checks. Run with: python check_integration.py"""
from contracts import InvoiceResult, Line, PricingResult
from invoice import build_invoice
from pricing import price_lines
from taxes import tax_for


def expect_value_error(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def main():
    single = [Line("item", 105, 1, True, True)]
    assert price_lines(single, "SAVE10") == PricingResult(105, 11, 94)
    assert build_invoice(single, "SAVE10", "NE") == InvoiceResult(105, 11, 7, 101)
    mixed = [Line("taxable", 5, 1, True, True), Line("exempt", 15, 1, False, True)]
    assert build_invoice(mixed, "SAVE10", "SW") == InvoiceResult(20, 3, 0, 17)
    assert price_lines(single, "NONE") == PricingResult(105, 0, 105)
    assert build_invoice([], "NONE", "EXEMPT") == InvoiceResult(0, 0, 0, 0)
    assert tax_for("NE", 69) == 5
    assert tax_for("SW", 59) == 5
    for call in (
        lambda: price_lines([Line("bad", -1, 1, True, True)], "NONE"),
        lambda: price_lines([Line("bad", 1, -1, True, True)], "NONE"),
        lambda: price_lines(single, "UNKNOWN"),
        lambda: tax_for("UNKNOWN", 0),
        lambda: tax_for("NE", -1),
        lambda: tax_for("NE", 1.5),
    ):
        expect_value_error(call)
    print("Integration and input checks passed")


if __name__ == "__main__":
    main()
