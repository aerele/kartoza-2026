import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import flt, getdate

from sales_commission.utils.ledger_rows import (
	PRECISION,
	apportion,
	multiplier_groups,
	piece_weights,
	top_first,
)
from sales_commission.utils.ledger_writer import LedgerWriter


class DeductionPoster(LedgerWriter):
	"""Posts what a credit note takes back, and gives it back when the note changes."""

	def settle(self, entry):
		old = self.ledger.totals(entry.key)
		diff = flt(flt(entry.deduction, PRECISION) - old.earned, PRECISION)
		if diff < 0:
			self.post_deduction(entry, old, diff, self.deduction_totals(entry))
		elif diff > 0 and old.rows:
			self.post_reversal(entry, old, diff, 0, "Reversal", None)

	def deduction_totals(self, entry):
		"""ZAR, base and recognised amounts the note takes back, one group per multiplier of the rows it takes from."""
		if entry.get("standalone"):
			return [frappe._dict(base=entry.zar, zar=entry.zar, recognised=entry.recognised, earned=0)]
		before = self.earned_before(entry)
		groups = [g for g in before if any(g[name] for name in ("zar", "base", "recognised"))]
		taken = self.taken_by_other_notes(entry)
		for group in groups:
			# sales a reduction can still take from this part: earlier notes took theirs from the top first
			group.room = max(group.recognised + taken.get(flt(group.multiplier, 6), 0), 0)
			for name in ("zar", "base", "recognised", "earned"):
				group[name] = -group[name] * entry.ratio
		return groups or [frappe._dict(multiplier=None, zar=0, base=0, recognised=0, earned=0)]

	def taken_by_other_notes(self, entry):
		"""Sales (negative) per multiplier that the earlier credit notes of the line took back."""
		taken = {}
		for row in self.ledger.line_rows(entry.reference_row, entry.sales_person):
			if (
				row.credit_note
				and row.credit_note != entry.credit_note
				and getdate(row.posting_date) <= entry.date
			):
				multiplier = flt(row.multiplier, 6)
				taken[multiplier] = (
					taken.get(multiplier, 0) + flt(row.running_total_after) - flt(row.running_total_before)
				)
		return taken

	def post_deduction(self, entry, old, diff, groups):
		"""One row per multiplier. A split line is taken back from its top part first, otherwise by shares."""
		total = {name: sum(g[name] for g in groups) for name in ("base", "zar", "recognised")}
		pieces = []
		if len(groups) > 1 and total["recognised"] < 0:
			pieces = top_first([g.multiplier for g in groups], [g.room for g in groups], total["recognised"])
		if pieces:
			self.post_top_first(entry, old, groups, pieces)
			return
		share = lambda name: [g[name] for g in groups]  # noqa: E731
		earned = apportion(diff, share("earned") if any(share("earned")) else share("base"))
		base = apportion(total["base"] - old.base, share("base"))
		zar = apportion(total["zar"] - old.zar, share("zar"))
		sales = apportion(total["recognised"] - old.recognised, share("recognised"))
		for index, group in enumerate(groups):
			self.insert_deduction(
				entry, group.get("multiplier"), earned[index], base[index], zar[index], sales[index]
			)

	def post_top_first(self, entry, old, groups, pieces):
		"""What the note takes in all goes piece by piece from the top; each part posts what it lacks yet."""
		weight = lambda name: piece_weights(groups, pieces, name)  # noqa: E731
		earned = apportion(
			flt(entry.deduction, PRECISION), weight("earned") if any(weight("earned")) else weight("base")
		)
		base = apportion(-sum(weight("base")), weight("base"))
		zar = apportion(-sum(weight("zar")), weight("zar"))
		posted = {flt(g.multiplier, 6): g for g in multiplier_groups(old.rows)}
		for index, (i, take) in enumerate(pieces):
			multiplier = groups[i].multiplier
			held = posted.get(flt(multiplier, 6), frappe._dict(earned=0, base=0, zar=0, recognised=0))
			amounts = (
				flt(earned[index] - held.earned, PRECISION),
				flt(base[index] - held.base, PRECISION),
				flt(zar[index] - held.zar, PRECISION),
				flt(take - held.recognised, PRECISION),
			)
			if any(amounts):
				self.insert_deduction(entry, multiplier, *amounts)

	def insert_deduction(self, entry, multiplier, earned, base, zar, sales):
		row = self.base_row(entry)
		row.update(
			entry_type="Credit Note Deduction",
			posting_date=entry.date,
			earned_amount=earned,
			exchange_rate=flt(entry.get("rate")) or 1,
			base_commission=base,
			commission_amount=zar,
		)
		row.multiplier = multiplier or (flt(zar / base, 6) if base else 1)
		row.fiscal_year = get_fiscal_year(row.posting_date, company=row.company)[0]
		self.escalator.stamp(row, sales)
		self.insert(row)

	def earned_before(self, entry):
		"""ZAR amounts earned so far on the line (before the credit note, so before its catch-up) per multiplier."""
		payments = entry.before
		groups = {}
		for row in self.ledger.line_rows(entry.reference_row, entry.sales_person):
			shares = payments.get((row.payment_doctype, row.payment_name))
			if shares:
				earned, counted = shares
				group = groups.setdefault(
					flt(row.multiplier, 6),
					frappe._dict(
						multiplier=flt(row.multiplier), zar=0.0, base=0.0, recognised=0.0, earned=0.0
					),
				)
				group.zar += flt(row.commission_amount) * earned
				group.base += flt(row.base_commission) * earned
				group.earned += flt(row.earned_amount) * earned
				group.recognised += (flt(row.running_total_after) - flt(row.running_total_before)) * counted
		return list(groups.values())
