import frappe

from kartoza_2026.utils.ledger_status import status_of

ROW_FIELDS = [
	"name",
	"is_cancelled",
	"commission_amount",
	"entry_type",
	"reference_doctype",
]


def get_commission_filters(ledger, slip):
	"""Conditions of the ledger rows a salary slip pays: its employee and period, not paid on another slip.

	Rows of a credit note (deduction and its reversal) count only when the setting is ticked.
	"""
	conditions = [
		ledger.employee == slip.employee,
		ledger.company == slip.company,
		ledger.is_cancelled == 0,
		ledger.posting_date >= slip.start_date,
		ledger.posting_date <= slip.end_date,
		(ledger.salary_slip.isnull())
		| (ledger.salary_slip == "")
		| (ledger.salary_slip == (slip.name or "")),
	]
	if not frappe.db.get_single_value("Commission Settings", "include_commission_for_credit_note"):
		conditions.append((ledger.credit_note.isnull()) | (ledger.credit_note == ""))
	return conditions


def stamp_ledger_on_submit(doc, method=None):
	"""Salary Slip on_submit: the rows this slip paid are locked to it."""
	if not paid_commission(doc):
		return
	ledger = frappe.qb.DocType("Commission Ledger")
	query = frappe.qb.from_(ledger).select(*ROW_FIELDS)
	for condition in get_commission_filters(ledger, doc):
		query = query.where(condition)
	update_rows(query.run(as_dict=True), salary_slip=doc.name)


def release_ledger_on_cancel(doc, method=None):
	"""Salary Slip on_cancel: the rows are unpaid again."""
	rows = frappe.get_all("Commission Ledger", filters={"salary_slip": doc.name}, fields=ROW_FIELDS)
	update_rows(rows, salary_slip=None)


def update_rows(rows, salary_slip):
	"""Sets the slip and the matching status on rows, one update per status."""
	by_status = {}
	for row in rows:
		row.salary_slip = salary_slip
		by_status.setdefault(status_of(row), []).append(row.name)
	ledger = frappe.qb.DocType("Commission Ledger")
	for status, names in by_status.items():
		(
			frappe.qb.update(ledger)
			.set(ledger.salary_slip, salary_slip)
			.set(ledger.status, status)
			.where(ledger.name.isin(names))
			.run()
		)


def latest_slip(slips):
	"""The salary slip that ends last of these names (None for none)."""
	names = sorted({name for name in slips if name})
	if not names:
		return None
	latest = frappe.get_all(
		"Salary Slip",
		filters={"name": ["in", names], "docstatus": 1},
		order_by="end_date desc, creation desc",
		limit=1,
		pluck="name",
	)
	return latest[0] if latest else None


def paid_commission(doc):
	component = frappe.db.get_single_value("Commission Settings", "commission_salary_component")
	return bool(component) and any(row.salary_component == component for row in doc.earnings)
