#!/usr/bin/env python3
import importlib
import sys
from pathlib import Path


EXPECTED_CONTRACT = '''from dataclasses import dataclass


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
'''


def require_raises_value_error(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def check(workspace):
    root = Path(workspace).resolve()
    assert (root / "contracts.py").read_text(encoding="utf-8") == EXPECTED_CONTRACT
    assert (root / "check_integration.py").read_bytes() == (Path(__file__).parent / "workspace" / "check_integration.py").read_bytes()
    sys.path.insert(0, str(root))
    contracts = importlib.import_module("contracts")
    pricing = importlib.import_module("pricing")
    taxes = importlib.import_module("taxes")
    invoice = importlib.import_module("invoice")

    Line = contracts.Line
    lines = [
        Line("part", 105, 3, True, True),
        Line("gift", 500, 1, True, False),
        Line("service", 199, 2, False, True),
    ]
    priced = pricing.price_lines(lines, "SAVE10")
    assert priced == contracts.PricingResult(1213, 72, 783)
    assert pricing.price_lines([Line("round", 5, 1, True, True)], "SAVE10") == contracts.PricingResult(5, 1, 4)
    assert pricing.price_lines([], "NONE") == contracts.PricingResult(0, 0, 0)
    require_raises_value_error(lambda: pricing.price_lines([Line("bad", -1, 1, True, True)], "NONE"))
    require_raises_value_error(lambda: pricing.price_lines(lines, "SURPRISE"))

    assert taxes.tax_for("NE", 783) == 57
    assert taxes.tax_for("SW", 100) == 9
    assert taxes.tax_for("EXEMPT", 999) == 0
    require_raises_value_error(lambda: taxes.tax_for("NE", -1))
    require_raises_value_error(lambda: taxes.tax_for("UNKNOWN", 100))
    require_raises_value_error(lambda: taxes.tax_for("NE", 10.0))

    combined = invoice.build_invoice(lines, "SAVE10", "NE")
    assert combined == contracts.InvoiceResult(1213, 72, 57, 1198)
    rounding_boundary = invoice.build_invoice([Line("round", 5, 1, True, True)], "SAVE10", "SW")
    assert rounding_boundary == contracts.InvoiceResult(5, 1, 0, 4)
    report = root / "EVAL_REPORT.md"
    assert report.is_file() and report.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"parallel_modules oracle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
