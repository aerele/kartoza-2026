"""Builders of real documents for the commission flow tests, on their own test company."""

import frappe
from erpnext.accounts.utils import get_fiscal_year, get_fiscal_years
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate, nowdate

COMPANY = "Commission Test Company"
ABBR = "_TCK"
BANK = f"Bank Accounts - {ABBR}"
VAT = f"VAT - {ABBR}"
WAREHOUSE = f"Stores - {ABBR}"
EMPLOYEE_TYPE = "full time"
THIS_YEAR = getdate().year
# two years back to next year, so the tests keep working as the calendar moves on
FISCAL_YEARS = tuple(range(THIS_YEAR - 2, THIS_YEAR + 2))


def ensure_test_environment():
	"""Create (once) the ZAR test company, its VAT account, the fiscal years and the currencies."""
	for currency in ("ZAR", "USD", "EUR"):
		frappe.db.set_value("Currency", currency, "enabled", 1)
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY,
				"abbr": ABBR,
				"default_currency": "ZAR",
				"country": "South Africa",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)
	ensure_fiscal_years()
	ensure_employee_type()
	if not frappe.db.exists("Account", VAT):
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "VAT",
				"parent_account": f"Duties and Taxes - {ABBR}",
				"company": COMPANY,
				"account_type": "Tax",
				"account_currency": "ZAR",
			}
		).insert(ignore_permissions=True)


def ensure_employee_type():
	"""The kartoza app requires an Employee Type on every Employee."""
	if frappe.db.exists("Employee Type", EMPLOYEE_TYPE):
		return
	frappe.get_doc({"doctype": "Employee Type", "employee_type_name": EMPLOYEE_TYPE}).insert(
		ignore_permissions=True
	)


def fiscal_year_of(date):
	return get_fiscal_year(date, company=COMPANY)[0]


def ensure_fiscal_years():
	"""Fiscal years of the test company starting 1 March, unless the site already has one for the date."""
	for year in FISCAL_YEARS:
		if get_fiscal_years(f"{year}-03-01", company=COMPANY, boolean=True):
			continue
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": f"{year}-{year + 1} {ABBR}",
				"year_start_date": f"{year}-03-01",
				"year_end_date": add_days(f"{year + 1}-03-01", -1),
				"companies": [{"company": COMPANY}],
			}
		).insert(ignore_permissions=True)


def date_in(years_back, month_day):
	"""A date in the fiscal year starting 1 March of this year minus years_back, e.g. date_in(1, "06-10")."""
	return f"{THIS_YEAR - years_back}-{month_day}"


def unique(prefix):
	return f"{prefix} {frappe.generate_hash(length=6)}"


def set_settings(factors=None, **values):
	"""Save Commission Settings. factors=(new, renewal) replaces the classification factor rows."""
	settings = frappe.get_single("Commission Settings")
	settings.update(values)
	if factors:
		settings.set("classification_factors", [])
		for classification, factor in zip(("New", "Renewal"), factors, strict=False):
			settings.append("classification_factors", {"classification": classification, "factor": factor})
	settings.save(ignore_permissions=True)
	frappe.clear_document_cache("Commission Settings", "Commission Settings")


def make_group(parent="All Item Groups", name=None):
	return (
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": name or unique("CG"),
				"parent_item_group": parent,
				"is_group": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_item(group, percent=None, grant=1):
	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": unique("CI"),
			"item_name": "Commission Test Item",
			"item_group": group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"grant_commission": grant,
			"commission_percent": percent or 0,
		}
	)
	return doc.insert(ignore_permissions=True).name


def make_receivable(currency):
	name = f"Debtors {currency} - {ABBR}"
	if not frappe.db.exists("Account", name):
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": f"Debtors {currency}",
				"parent_account": f"Accounts Receivable - {ABBR}",
				"company": COMPANY,
				"account_type": "Receivable",
				"account_currency": currency,
			}
		).insert(ignore_permissions=True)
	return name


def make_customer(currency=None):
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": unique("CC"),
			"customer_group": "Individual",
			"territory": "Rest Of The World",
		}
	)
	if currency:
		doc.default_currency = currency
		doc.append("accounts", {"company": COMPANY, "account": make_receivable(currency)})
	return doc.insert(ignore_permissions=True).name


def make_employee():
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": unique("Emp"),
				"company": COMPANY,
				"gender": "Female",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"custom_employee_type": EMPLOYEE_TYPE,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_sales_person(employee=True, rate=None):
	"""A salesperson linked to a new Employee, unless employee=False. rate: ERPNext commission rate."""
	return (
		frappe.get_doc(
			{
				"doctype": "Sales Person",
				"sales_person_name": unique("SP"),
				"parent_sales_person": "Sales Team",
				"is_group": 0,
				"employee": make_employee() if employee else None,
				"commission_rate": str(rate) if rate is not None else "",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_distribution():
	"""ERPNext needs a Monthly Distribution on every Sales Person target row."""
	name = "Commission Test Distribution"
	if not frappe.db.exists("Monthly Distribution", name):
		months = ["January", "February", "March", "April", "May", "June"]
		months += ["July", "August", "September", "October", "November", "December"]
		frappe.get_doc(
			{
				"doctype": "Monthly Distribution",
				"distribution_id": name,
				"percentages": [{"month": m, "percentage_allocation": 100 / 12} for m in months],
			}
		).insert(ignore_permissions=True)
	return name


def set_target(sales_person, fiscal_year, amount, item_group=None):
	"""Add a Sales Person > Targets row. Without item_group it is the annual target the escalator reads."""
	person = frappe.get_doc("Sales Person", sales_person)
	person.append(
		"targets",
		{
			"fiscal_year": fiscal_year,
			"item_group": item_group,
			"target_amount": amount,
			"distribution_id": make_distribution(),
		},
	)
	person.save(ignore_permissions=True)


def vat_rows(rate=15):
	return [{"charge_type": "On Net Total", "account_head": VAT, "description": "VAT", "rate": rate}]


def item_rows(lines):
	return [{"item_code": code, "qty": qty, "rate": rate} for code, qty, rate in lines]


def team_rows(team):
	return [{"sales_person": sp, "allocated_percentage": pct} for sp, pct in team or []]


def make_order(customer, lines, date=None, team=None, rate=1, currency=None, submit=True, **extra):
	doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer,
			"company": COMPANY,
			"transaction_date": date or nowdate(),
			"delivery_date": add_days(date or nowdate(), 30),
			"currency": currency or "ZAR",
			"conversion_rate": rate,
			"items": item_rows(lines),
			"sales_team": team_rows(team),
			**extra,
		}
	)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def make_invoice(customer, lines, date=None, team=None, rate=1, currency=None, submit=True, **extra):
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": customer,
			"company": COMPANY,
			"posting_date": date or nowdate(),
			"set_posting_time": 1,
			"due_date": date or nowdate(),
			"currency": currency or "ZAR",
			"conversion_rate": rate,
			"items": item_rows(lines),
			"sales_team": team_rows(team),
			"update_stock": 0,
			**extra,
		}
	)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def ledger(reference_name=None, **filters):
	"""Live ledger rows (not cancelled), oldest first. Pass is_cancelled=1 for the cancelled history."""
	if reference_name:
		filters["reference_name"] = reference_name
	filters.setdefault("is_cancelled", 0)
	return frappe.get_all(
		"Commission Ledger",
		filters=filters,
		fields=["*"],
		order_by="creation asc, name asc",
	)


class CommissionTestCase(FrappeTestCase):
	"""Base class: default settings before every test."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		ensure_test_environment()

	def setUp(self):
		frappe.set_user("Administrator")
		set_settings(factors=(1.0, 0.5), escalator_multiplier=1.5)
