import frappe
from frappe.utils import flt

from kartuza_2026.utils.advance_source import AdvanceSource
from kartuza_2026.utils.escalator import shares_of
from kartuza_2026.utils.ledger_rows import PRECISION, apportion
from kartuza_2026.utils.ledger_writer import LedgerWriter
from kartuza_2026.utils.payroll import latest_slip


def is_positive(row):
	return flt(row.earned_amount) > 0 or flt(row.paid_share) > 0


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
			self.post_parts(entry, old)
		elif old.rows:
			self.post_reversal_of(entry, old, diff_earned, diff_paid)

	def post_parts(self, entry, old):
		"""One row per payment and per credit note catch-up, so each lands on its own date."""
		parts = entry.get("parts") or [
			frappe._dict(
				kind="payment", date=entry.date, earned=entry.earned, paid=entry.paid, counted=entry.paid
			)
		]
		done_earned, done_paid = old.earned, old.paid
		cum_earned = cum_paid = 0.0
		for index, part in enumerate(parts):
			start_earned, start_paid = flt(cum_earned, PRECISION), flt(cum_paid, PRECISION)
			cum_earned += part.earned
			cum_paid += part.paid
			diff_earned = max(flt(flt(cum_earned, PRECISION) - max(done_earned, start_earned), PRECISION), 0)
			diff_paid = max(flt(flt(cum_paid, PRECISION) - max(done_paid, start_paid), PRECISION), 0)
			done_earned = max(done_earned, flt(cum_earned, PRECISION))
			done_paid = max(done_paid, flt(cum_paid, PRECISION))
			if not (diff_earned or diff_paid):
				continue
			exact = not old.rows and not index
			exact_paid = part.paid if exact else diff_paid
			# a part posted only in part counts its sales in the same proportion
			counted = part.counted * exact_paid / part.paid if part.paid else 0
			self.post_earned(
				entry,
				diff_earned,
				diff_paid,
				part.earned if exact else diff_earned,
				exact_paid,
				part,
				counted,
			)

	def post_reversal_of(self, entry, old, diff_earned, diff_paid):
		moved = bool(entry.get("adjusted_on"))
		kind = "Advance Adjustment" if moved else "Reversal"
		slip = None
		if moved:
			# the advance was already paid: the row leaves the order on the slip that paid it, so the
			# pair with the invoice's row never reaches a later slip (several slips: the latest one)
			slip = latest_slip(r.salary_slip for r in old.rows if flt(r.commission_amount) > 0)
		self.post_reversal(entry, old, diff_earned, diff_paid, kind, entry.get("adjusted_on"), slip)

	def post_earned(self, entry, diff_earned, diff_paid, exact_earned, exact_paid, part=None, counted=None):
		"""exact_*: the unrounded amounts, so the multiplier rounds the result only once."""
		rate = flt(entry.rate) or 1
		row = self.base_row(entry)
		catch_up = bool(part and part.kind == "catch_up")
		moved = entry.advance and not catch_up
		row.update(
			entry_type="Advance Adjustment" if moved else "Earned",
			posting_date=part.date
			if catch_up
			else (entry.adjusted_on if entry.advance and entry.adjusted_on else entry.date),
			earned_amount=diff_earned,
			paid_share=diff_paid,
			exchange_rate=rate,
		)
		# entry.paid is already the person's allocated share
		recognised = flt((exact_paid if counted is None else counted) * rate * entry.line_share, PRECISION)
		source = AdvanceSource(entry) if moved else None
		earlier = [r for r in self.ledger.totals(entry.key).rows if is_positive(r)] if catch_up else []
		if earlier:
			# a catch-up keeps the multipliers and year of the payment rows it tops up
			row.fiscal_year = earlier[-1].fiscal_year
			shares = shares_of(earlier)
		elif source and source.rows:
			source.copy_to(row)
			shares = shares_of(source.rows)
		else:
			self.escalator.set_fiscal_year(row)
			shares = self.document_splits.shares(entry)
		self.insert_parts(row, shares, recognised, exact_earned * rate)

	def insert_parts(self, row, shares, recognised, exact_base):
		"""One row per multiplier of the split; amounts follow the shares and the last row takes the cents left."""
		weights = [share for _m, share in shares]
		earned = apportion(row.earned_amount, weights)
		paid = apportion(row.paid_share, weights)
		base = apportion(flt(exact_base, PRECISION), weights)
		sales = apportion(recognised, weights)
		for index, (multiplier, share) in enumerate(shares):
			part = frappe._dict(row)
			part.update(
				multiplier=multiplier,
				earned_amount=earned[index],
				paid_share=paid[index],
				base_commission=base[index],
				commission_amount=flt(exact_base * share * multiplier, PRECISION),
			)
			self.escalator.stamp(part, sales[index])
			self.insert(part)

	def correct_rate(self, entry):
		"""The amounts in the document currency are right but the rate or ZAR amount posted is not."""
		old = self.ledger.totals(entry.key)
		if not old.rows:
			return
		rate = flt(entry.rate)
		diff_base = flt(flt(entry.earned * rate, PRECISION) - old.base, PRECISION)
		recognised = entry.get("counted", entry.paid) * rate * entry.line_share
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
