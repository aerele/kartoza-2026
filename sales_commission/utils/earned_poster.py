from frappe.utils import flt

from sales_commission.utils.ledger_rows import PRECISION
from sales_commission.utils.ledger_writer import LedgerWriter


class EarnedPoster(LedgerWriter):
	"""Posts what a payment earned on a line, and the reversals when that changes."""

	def settle(self, entry):
		old = self.ledger.totals(entry.key)
		diff_earned = flt(flt(entry.earned, PRECISION) - old.earned, PRECISION)
		diff_paid = flt(flt(entry.paid, PRECISION) - old.paid, PRECISION)
		if diff_earned or diff_paid:
			self.settle_difference(entry, old, diff_earned, diff_paid)
		elif entry.get("rate"):
			self.correct_rate(entry)

	def settle_difference(self, entry, old, diff_earned, diff_paid):
		increase = diff_paid > 0 or (not diff_paid and diff_earned > 0)
		if diff_earned < 0 and old.rows:
			# a smaller amount is a reversal at the old multiplier, never a new row for the escalator
			self.post_reversal_of(entry, old, diff_earned, diff_paid)
		elif increase:
			first = not old.rows
			self.post_earned(
				entry,
				diff_earned,
				diff_paid,
				entry.earned if first else diff_earned,
				entry.paid if first else diff_paid,
			)
		elif old.rows:
			self.post_reversal_of(entry, old, diff_earned, diff_paid)

	def post_reversal_of(self, entry, old, diff_earned, diff_paid):
		kind = "Advance Adjustment" if entry.get("adjusted_on") else "Reversal"
		self.post_reversal(entry, old, diff_earned, diff_paid, kind, entry.get("adjusted_on"))

	def post_earned(self, entry, diff_earned, diff_paid, exact_earned, exact_paid):
		"""exact_*: the unrounded amounts, so the multiplier rounds the result only once."""
		rate = flt(entry.rate) or 1
		row = self.base_row(entry)
		row.update(
			entry_type="Advance Adjustment" if entry.advance else "Earned",
			posting_date=entry.date,
			earned_amount=diff_earned,
			paid_share=diff_paid,
			exchange_rate=rate,
			base_commission=flt(exact_earned * rate, PRECISION),
		)
		# entry.paid is already the person's allocated share
		self.escalator.apply(row, flt(exact_paid * rate * entry.line_share, PRECISION))
		row.commission_amount = flt(exact_earned * rate * row.multiplier, PRECISION)
		self.insert(row)

	def correct_rate(self, entry):
		"""The amounts in the document currency are right but the rate or ZAR amount posted is not."""
		old = self.ledger.totals(entry.key)
		if not old.rows:
			return
		rate = flt(entry.rate)
		diff_base = flt(flt(entry.earned * rate, PRECISION) - old.base, PRECISION)
		recognised = entry.paid * rate * entry.line_share
		diff_recognised = flt(flt(recognised, PRECISION) - old.recognised, PRECISION)
		# each row rounds its amount on its own, so a rate times half a cent per row is no correction
		tolerance = 0.01 + 0.005 * rate * len(old.rows)
		if abs(diff_base) <= tolerance and abs(diff_recognised) <= tolerance:
			return
		source = old.latest
		row = self.base_row(entry)
		row.update(
			entry_type="Reversal" if diff_base < 0 else ("Advance Adjustment" if entry.advance else "Earned"),
			posting_date=entry.date,
			reverses_entry=source.name if diff_base < 0 else None,
			exchange_rate=rate,
			base_commission=diff_base,
			commission_amount=flt(diff_base * flt(source.multiplier), PRECISION),
			multiplier=source.multiplier,
			fiscal_year=source.fiscal_year,
			sales_date=source.sales_date,
			payment_date=source.payment_date,
		)
		self.escalator.stamp(row, diff_recognised)
		self.insert(row)
