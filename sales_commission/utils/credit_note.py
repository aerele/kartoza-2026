from frappe.utils import flt

from sales_commission.utils.calculation import LineCalculator
from sales_commission.utils.line_fields import clear_line, line_multiplier, set_line_amounts
from sales_commission.utils.original_lines import OriginalLines


class CreditNoteLines:
	"""Return lines take their classification, % and factor from the original invoice, else are worked out as new."""

	def __init__(self, doc):
		self.doc = doc

	def apply(self):
		originals = OriginalLines.for_note(self.doc)
		unmatched = []
		for row in self.doc.items:
			clear_line(row)
			found = originals.match(row)
			if not found:
				unmatched.append(row)
				continue
			line, _linked = found
			row.commission_classification = line.commission_classification or ""
			row.commission_percent = flt(line.commission_percent)
			row.commission_factor = flt(line.commission_factor)
			# a return keeps the multiplier of the invoice line it takes back
			set_line_amounts(row, line_multiplier(line))
			row.target_multiplier = line.get("target_multiplier") or ""
		if unmatched:
			LineCalculator(self.doc, rows=unmatched, plain=True).apply()
