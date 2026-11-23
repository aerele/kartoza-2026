"""Salary component, structure and slips of the payroll flow tests."""

import frappe
from frappe.utils import get_first_day, get_last_day

from kartuza_2026.tests.helpers import ABBR, ANCHOR, COMPANY, FY_YEAR, unique

EXPENSE = f"Administrative Expenses - {ABBR}"
COMMISSION_COMPONENT = "Test Sales Commission"
BASIC_COMPONENT = "Test Basic Pay"


def make_component(name, abbr, remove_if_zero=0):
	if frappe.db.exists("Salary Component", name):
		return name
	frappe.get_doc(
		{
			"doctype": "Salary Component",
			"salary_component": name,
			"salary_component_abbr": abbr,
			"type": "Earning",
			"depends_on_payment_days": 0,
			"is_tax_applicable": 0,
			"remove_if_zero_valued": remove_if_zero,
			"accounts": [{"company": COMPANY, "account": EXPENSE}],
		}
	).insert(ignore_permissions=True)
	return name


def ensure_holiday_list():
	"""Salary slips need the company's default Holiday List to count working days."""
	name = "Commission Test Holidays"
	if not frappe.db.exists("Holiday List", name):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": name,
				"from_date": f"{FY_YEAR - 1}-01-01",
				"to_date": f"{FY_YEAR + 1}-12-31",
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Company", COMPANY, "default_holiday_list", name)
	# HRMS v16 finds the holiday list through a Holiday List Assignment, not the company field
	assignment = {"applicable_for": "Company", "assigned_to": COMPANY, "holiday_list": name}
	if not frappe.db.exists("Holiday List Assignment", {**assignment, "docstatus": 1}):
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				**assignment,
				"from_date": frappe.db.get_value("Holiday List", name, "from_date"),
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()


def ensure_payroll_period():
	"""Calendar-year payroll periods of the test fiscal year and the one after it."""
	for year in (FY_YEAR, FY_YEAR + 1):
		if frappe.db.exists("Payroll Period", {"company": COMPANY, "start_date": f"{year}-01-01"}):
			continue
		frappe.get_doc(
			{
				"doctype": "Payroll Period",
				"name": f"Commission Test {year}",
				"company": COMPANY,
				"start_date": f"{year}-01-01",
				"end_date": f"{year}-12-31",
			}
		).insert(ignore_permissions=True)


def make_structure_assignment(employee):
	"""A monthly structure with a flat basic pay, assigned from the start of last year."""
	make_component(BASIC_COMPONENT, "TBP")
	structure = frappe.get_doc(
		{
			"doctype": "Salary Structure",
			"name": unique("Commission Test Structure"),
			"company": COMPANY,
			"payroll_frequency": "Monthly",
			"currency": "ZAR",
			"is_active": "Yes",
			"earnings": [{"salary_component": BASIC_COMPONENT, "amount": 1000}],
		}
	).insert(ignore_permissions=True)
	structure.submit()
	frappe.get_doc(
		{
			"doctype": "Salary Structure Assignment",
			"employee": employee,
			"salary_structure": structure.name,
			"company": COMPANY,
			"from_date": f"{FY_YEAR - 1}-01-01",
			"base": 1000,
		}
	).insert(ignore_permissions=True).submit()


def make_slip(employee, start_date=None, end_date=None, submit=False):
	slip = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"company": COMPANY,
			"posting_date": end_date or ANCHOR,
			"start_date": start_date or get_first_day(ANCHOR),
			"end_date": end_date or get_last_day(ANCHOR),
		}
	)
	slip.insert(ignore_permissions=True)
	if submit:
		slip.submit()
	return slip


def commission_row(slip):
	rows = [r for r in slip.earnings if r.salary_component == COMMISSION_COMPONENT]
	return rows[0] if rows else None


def ledger_slips(employee):
	"""(commission_amount, salary_slip) of the employee's live ledger rows."""
	rows = frappe.get_all(
		"Commission Ledger",
		filters={"employee": employee, "is_cancelled": 0},
		fields=["commission_amount", "salary_slip"],
	)
	return [(r.commission_amount, r.salary_slip) for r in rows]
