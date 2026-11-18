from sales_commission.utils.calculation import LineCalculator
from sales_commission.utils.credit_note import CreditNoteLines
from sales_commission.utils.line_fields import has_stored_commission, set_totals
from sales_commission.utils.sales_team import set_incentives
from sales_commission.utils.target_estimate import TargetMultipliers


def calculate_commission(doc, method=None):
	"""Sales Order / Sales Invoice validate: fill the commission of every line and the header totals."""
	multipliers = TargetMultipliers(doc)
	if doc.get("is_return"):
		CreditNoteLines(doc).apply()
	else:
		LineCalculator(doc, multipliers=multipliers).apply()
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
