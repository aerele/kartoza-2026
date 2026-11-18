from frappe.utils import flt


def status_of(row):
	"""Status of a ledger row. Only the system sets it, from the row's own fields."""
	if row.get("is_cancelled"):
		return "Cancelled"
	amount = flt(row.get("commission_amount"))
	on_order = row.get("reference_doctype") == "Sales Order"
	if on_order and row.get("entry_type") == "Advance Adjustment" and amount < 0:
		return "Moved to Invoice"  # on a slip or not, it is the advance leaving the order
	if row.get("salary_slip"):
		return "Paid" if amount >= 0 else "Deducted"
	return unpaid_status(row, amount)


def unpaid_status(row, amount):
	on_order = row.get("reference_doctype") == "Sales Order"
	if amount < 0:
		return "To Deduct"
	return "Advance" if on_order else "Earned"
