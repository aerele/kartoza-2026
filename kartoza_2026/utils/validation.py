from kartoza_2026.utils.calculation import LineCalculator
from kartoza_2026.utils.credit_note import CreditNoteLines
from kartoza_2026.utils.line_fields import clear_line, has_stored_commission, set_totals
from kartoza_2026.utils.sales_team import set_incentives
from kartoza_2026.utils.target_estimate import TargetMultipliers


def calculate_commission(doc, method=None):
	"""Sales Order / Sales Invoice validate: fill the commission of every line and the header totals."""
	multipliers = TargetMultipliers(doc)
	if doc.get("is_return"):
		CreditNoteLines(doc).apply()
	elif has_sales_person(doc):
		LineCalculator(doc, multipliers=multipliers).apply()
	else:
		# the % and the annual target belong to the salesperson, so nothing is worked out without one
		for row in doc.items:
			clear_line(row)
	set_totals(doc)
	set_incentives(doc, multipliers)


def update_commission_after_submit(doc, method=None):
	"""Update Items: stored lines keep classification and %, new lines are worked out, totals follow."""
	# documents made before the app existed never earn and stay editable
	if doc.get("is_return") or not has_stored_commission(doc):
		return
	multipliers = TargetMultipliers(doc)
	LineCalculator(doc, keep_stored=True, multipliers=multipliers).apply()
	set_totals(doc)
	set_incentives(doc, multipliers)


def has_sales_person(doc):
	return any(row.sales_person for row in doc.get("sales_team") or [])
