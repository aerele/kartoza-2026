from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import flt

from sales_commission.utils.ledger_rows import PRECISION
from sales_commission.utils.ledger_writer import LedgerWriter


class DeductionPoster(LedgerWriter):
	"""Posts what a credit note takes back, and gives it back when the note changes."""

	def settle(self, entry):
		old = self.ledger.totals(entry.key)
		diff = flt(flt(entry.deduction, PRECISION) - old.earned, PRECISION)
		if diff < 0:
			self.post_deduction(entry, old, diff, self.deduction_totals(entry))
		elif diff > 0 and old.rows:
			kind = "Override Reversal" if entry.override else "Reversal"
			self.post_reversal(entry, old, diff, 0, kind, None)

	def deduction_totals(self, entry):
		"""ZAR, base and recognised amounts the note takes back."""
		if entry.get("standalone"):
			return {"base": entry.zar, "zar": entry.zar, "recognised": entry.recognised}
		before = self.earned_before(entry)
		return {name: -value * entry.ratio for name, value in before.items()}

	def post_deduction(self, entry, old, diff, total):
		row = self.base_row(entry)
		row.update(
			entry_type="Credit Note Deduction",
			posting_date=entry.date,
			earned_amount=diff,
			exchange_rate=flt(entry.get("rate")) or 1,
			base_commission=flt(total["base"] - old.base, PRECISION),
			commission_amount=flt(total["zar"] - old.zar, PRECISION),
		)
		row.multiplier = flt(row.commission_amount / row.base_commission, 6) if row.base_commission else 1
		row.fiscal_year = get_fiscal_year(row.posting_date, company=row.company)[0]
		self.escalator.stamp(row, flt(total["recognised"] - old.recognised, PRECISION))
		self.insert(row)

	def earned_before(self, entry):
		"""ZAR amounts earned so far on the line (before the credit note) for this sales person."""
		payments = set(entry.before)
		total = {"zar": 0.0, "base": 0.0, "recognised": 0.0}
		for row in self.ledger.line_rows(entry.reference_row, entry.sales_person):
			if (row.payment_doctype, row.payment_name) in payments:
				total["zar"] += flt(row.commission_amount)
				total["base"] += flt(row.base_commission)
				total["recognised"] += flt(row.running_total_after) - flt(row.running_total_before)
		return total
