import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import flt

from sales_commission.utils.ledger_rows import PRECISION, apportion
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
		for group in groups:
			for name in ("zar", "base", "recognised", "earned"):
				group[name] = -group[name] * entry.ratio
		return groups or [frappe._dict(multiplier=None, zar=0, base=0, recognised=0, earned=0)]

	def post_deduction(self, entry, old, diff, groups):
		"""One row per multiplier, the change over what the key holds is shared in the proportion of the groups."""
		total = {name: sum(g[name] for g in groups) for name in ("base", "zar", "recognised")}
		share = lambda name: [g[name] for g in groups]  # noqa: E731
		earned = apportion(diff, share("earned") if any(share("earned")) else share("base"))
		base = apportion(total["base"] - old.base, share("base"))
		zar = apportion(total["zar"] - old.zar, share("zar"))
		sales = apportion(total["recognised"] - old.recognised, share("recognised"))
		for index, group in enumerate(groups):
			row = self.base_row(entry)
			row.update(
				entry_type="Credit Note Deduction",
				posting_date=entry.date,
				earned_amount=earned[index],
				exchange_rate=flt(entry.get("rate")) or 1,
				base_commission=base[index],
				commission_amount=zar[index],
			)
			row.multiplier = group.get("multiplier") or (
				flt(row.commission_amount / row.base_commission, 6) if row.base_commission else 1
			)
			row.fiscal_year = get_fiscal_year(row.posting_date, company=row.company)[0]
			self.escalator.stamp(row, sales[index])
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
