from frappe import _

LEDGER = "Commission Ledger"


def add_commission_ledger(data, fieldname):
	"""Show the Commission Ledger rows of the document in its Connections tab."""
	data.setdefault("non_standard_fieldnames", {})[LEDGER] = fieldname
	data.setdefault("transactions", []).append({"label": _("Commission"), "items": [LEDGER]})
	return data


def order_or_invoice(data):
	return add_commission_ledger(data, "reference_name")


def payment(data):
	return add_commission_ledger(data, "payment_name")


def salary_slip(data):
	return add_commission_ledger(data, "salary_slip")
