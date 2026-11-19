from frappe.utils import flt

from sales_commission.utils.calculation import LineCalculator
from sales_commission.utils.line_fields import clear_line, line_multiplier, set_line_amounts
from sales_commission.utils.original_lines import OriginalLines
from sales_commission.utils.return_split import ReturnSplits, blended, describe_parts


class CreditNoteLines:
	"""Return lines take their classification, % and factor from the original invoice, else are worked out as new."""

	def __init__(self, doc):
		self.doc = doc

	def apply(self):
		originals = OriginalLines.for_note(self.doc)
		unmatched = []
		splits = ReturnSplits(self.doc, originals.by_name.values())
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
			# a return takes back the parts of the invoice line the ledger takes it from, else its multiplier
			parts = splits.take(line, abs(flt(row.base_net_amount)))
			if parts:
				set_line_amounts(row, blended(parts))
				row.target_multiplier = describe_parts(parts, self.doc.get("company"))
				continue
			set_line_amounts(row, line_multiplier(line))
			row.target_multiplier = line.get("target_multiplier") or ""
		if unmatched:
			LineCalculator(self.doc, rows=unmatched, plain=True).apply()
