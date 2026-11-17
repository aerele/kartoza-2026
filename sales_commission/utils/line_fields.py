from frappe.utils import flt


def clear_line(row):
	row.commission_classification = ""
	row.commission_percent = 0
	row.commission_factor = 0
	row.commission_amount = 0
	row.base_commission_amount = 0
	row.commission_note = ""


def unrounded_commission(row, base=False):
	"""Net amount times the line % and factor, before any rounding."""
	net = row.base_net_amount if base else row.net_amount
	return flt(net) * flt(row.commission_percent) / 100 * flt(row.commission_factor)


def exact_commission(row, base=False):
	"""Line commission to earn from: unrounded, unless the stored amount is a cent fix or typed by hand."""
	# the ledger multiplies by rate and multiplier and rounds once, so it starts from the unrounded value
	field = "base_commission_amount" if base else "commission_amount"
	unrounded = unrounded_commission(row, base)
	stored = flt(row.get(field))
	if flt(unrounded, row.precision(field)) == flt(stored, row.precision(field)):
		return unrounded
	return stored


def set_line_amounts(row):
	"""Commission of a line from its net amount and stored %."""
	row.commission_amount = flt(unrounded_commission(row), row.precision("commission_amount"))
	row.base_commission_amount = flt(
		unrounded_commission(row, base=True), row.precision("base_commission_amount")
	)


def set_totals(doc):
	doc.total_commission_amount = flt(
		sum(flt(r.commission_amount) for r in doc.items), doc.precision("total_commission_amount")
	)
	doc.base_total_commission_amount = flt(
		sum(flt(r.base_commission_amount) for r in doc.items), doc.precision("base_total_commission_amount")
	)


def has_stored_commission(doc):
	"""False for a document made before the app existed: no line was ever classified."""
	return bool(
		flt(doc.get("total_commission_amount")) or any(r.commission_classification for r in doc.items)
	)
