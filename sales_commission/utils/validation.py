from sales_commission.utils.calculation import LineCalculator
from sales_commission.utils.credit_note import CreditNoteLines
from sales_commission.utils.line_fields import has_stored_commission, set_totals


def calculate_commission(doc, method=None):
	"""Sales Order / Sales Invoice validate: fill the commission of every line and the header totals."""
	if doc.get("is_return"):
		CreditNoteLines(doc).apply()
	else:
		LineCalculator(doc).apply()
	set_totals(doc)


def update_commission_after_submit(doc, method=None):
	"""Update Items: stored lines keep classification and %, new lines are worked out, totals follow."""
	# documents made before the app existed never earn and stay editable
	if doc.get("is_return") or not has_stored_commission(doc):
		return
	LineCalculator(doc, keep_stored=True).apply()
	set_totals(doc)
