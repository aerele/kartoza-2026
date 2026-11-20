import frappe
from frappe.utils import flt, getdate

from sales_commission.utils.ledger_rows import (
	PRECISION,
	apportion,
	multiplier_groups,
	piece_weights,
	top_first,
)
from sales_commission.utils.line_factors import LineFactors
from sales_commission.utils.target_sales import DocumentSplits


class LedgerWriter:
	"""Builds and inserts the Commission Ledger rows of one document."""

	def __init__(self, doctype, name, ledger, escalator, settings):
		self.doctype = doctype
		self.name = name
		self.ledger = ledger
		self.escalator = escalator
		self.settings = settings
		self.line_factors = LineFactors(doctype, name)
		self.document_splits = DocumentSplits(doctype, name)

	def cancel_key(self, entry):
		"""Unpaid rows are marked cancelled like a GL Entry, rows already on a slip get a normal reversal."""
		old = self.ledger.totals(entry.key)
		unpaid = [r for r in old.rows if not r.get("salary_slip")]
		paid = [r for r in old.rows if r.get("salary_slip")]
		if paid and not self.holds_value(old):
			return  # already netted to zero (a repeated sync), nothing left to take back
		if unpaid:
			self.cancel_unpaid_rows(entry, unpaid)
		if paid:
			self.reverse_paid_rows(entry, self.ledger.totals_of(paid))

	def cancel_unpaid_rows(self, entry, rows):
		old = self.ledger.totals_of(rows)
		before = self.live_total(old.latest)
		frappe.db.set_value(
			"Commission Ledger",
			{"name": ["in", [r.name for r in rows]]},
			{"is_cancelled": 1, "status": "Cancelled"},
			update_modified=False,
		)
		self.escalator.forget()
		if self.holds_value(old):
			self.insert_cancelled_reverse(entry, old, before)
		self.ledger.drop_rows(entry.key, rows)

	def live_total(self, source):
		"""Year-to-date sales while the rows being cancelled still count."""
		if not (source and source.sales_person and source.fiscal_year):
			return 0.0
		return self.escalator.running_total(source.sales_person, source.fiscal_year)

	def reverse_paid_rows(self, entry, old):
		"""Rows already paid on a salary slip stay live: a normal reversal is taken off a later slip."""
		if self.holds_value(old):
			self.post_reversal(entry, old, -old.earned, -old.paid, "Reversal", None)

	def holds_value(self, old):
		return any(flt(old[name]) for name in ("earned", "paid", "zar", "base", "recognised"))

	def insert_cancelled_reverse(self, entry, old, before):
		source = old.latest
		row = self.base_row(entry)
		row.update(
			entry_type="Reversal",
			posting_date=source.posting_date,
			reverses_entry=source.name,
			exchange_rate=source.exchange_rate,
			earned_amount=-old.earned,
			paid_share=-old.paid,
			base_commission=flt(-old.base, PRECISION),
			commission_amount=flt(-old.zar, PRECISION),
			multiplier=source.multiplier,
			fiscal_year=source.fiscal_year,
			sales_date=source.sales_date,
			payment_date=source.payment_date,
			is_cancelled=1,
		)
		# starts from the total that still holds the cancelled rows, so their sales come off only once
		row.running_total_before = before
		row.running_total_after = flt(before - flt(old.recognised, PRECISION), 2)
		self.line_factors.set_net_amounts(row)
		frappe.get_doc(row).insert(ignore_permissions=True)

	def post_reversal(self, entry, old, diff_earned, diff_paid, kind, date, salary_slip=None):
		"""Takes back a share of what the key holds, from each multiplier's rows at that multiplier."""
		base_fraction = self.fraction(diff_earned, old.earned, diff_paid, old.paid)
		paid_fraction = self.fraction(diff_paid, old.paid, diff_earned, old.earned)
		groups = [g for g in multiplier_groups(old.rows) if self.holds_value(g)]
		groups = groups or multiplier_groups([old.latest])
		parts = self.reversal_parts(groups, diff_earned, diff_paid, base_fraction, paid_fraction)
		for group, earned, paid, base, zar, sales in parts:
			source = group.source
			row = self.base_row(entry)
			row.update(
				entry_type=kind,
				posting_date=date or getdate(),
				reverses_entry=source.name,
				earned_amount=earned,
				paid_share=paid,
				exchange_rate=source.exchange_rate,
				base_commission=base,
				commission_amount=zar,
				multiplier=group.multiplier,
				fiscal_year=source.fiscal_year,
				sales_date=source.sales_date,
				payment_date=source.payment_date,
				salary_slip=salary_slip,
			)
			self.escalator.stamp(row, sales)
			self.insert(row)

	def reversal_parts(self, groups, diff_earned, diff_paid, base_fraction, paid_fraction):
		"""(group, earned, paid, base, zar, sales) per row: a split key is taken back from its top part first."""
		sales = flt(sum(g.recognised for g in groups) * paid_fraction, PRECISION)
		rooms = [max(g.recognised * (1 if sales < 0 else -1), 0) for g in groups]
		pieces = top_first([g.multiplier for g in groups], rooms, sales) if len(groups) > 1 else []
		if not pieces:
			earned = self.part_amounts(diff_earned, base_fraction, [g.earned for g in groups])
			paid = self.part_amounts(diff_paid, paid_fraction, [g.paid for g in groups])
			return [
				(
					g,
					earned[i],
					paid[i],
					flt(g.base * base_fraction, PRECISION),
					flt(g.zar * base_fraction, PRECISION),
					flt(g.recognised * paid_fraction, PRECISION),
				)
				for i, g in enumerate(groups)
			]
		weights = {name: piece_weights(groups, pieces, name) for name in ("earned", "paid", "base", "zar")}
		earned = apportion(diff_earned, weights["earned"])
		paid = apportion(diff_paid, weights["paid"])
		base = apportion(flt(sum(g.base for g in groups) * base_fraction, PRECISION), weights["base"])
		zar = apportion(flt(sum(g.zar for g in groups) * base_fraction, PRECISION), weights["zar"])
		return [(groups[i], earned[n], paid[n], base[n], zar[n], take) for n, (i, take) in enumerate(pieces)]

	def part_amounts(self, diff, fraction, held):
		"""The change split over the multipliers' rows; everything held goes back when the fraction is -1."""
		if fraction <= -1:
			return [-flt(value, PRECISION) for value in held]
		return apportion(diff, held)

	def fraction(self, diff, total, other_diff, other_total):
		"""Share of what is held that this change takes back, between -1 and 0 or 0 and 1."""
		if total:
			ratio = diff / total
		elif other_total:
			ratio = other_diff / other_total
		else:
			return 0
		return max(ratio, -1)

	def base_row(self, entry):
		return frappe._dict(
			doctype="Commission Ledger",
			company=entry.company,
			customer=entry.customer,
			sales_person=entry.sales_person or None,
			employee=entry.employee,
			allocated_percentage=entry.allocated,
			reference_doctype=self.doctype,
			reference_name=self.name,
			reference_row=entry.reference_row,
			item_code=entry.item_code,
			item_group=entry.item_group,
			payment_doctype=entry.payment_doctype,
			payment_name=entry.payment_name,
			credit_note=entry.credit_note,
			classification=entry.classification,
			commission_percent=entry.commission_percent,
			currency=entry.currency,
			sales_date=entry.get("sales_date"),
			payment_date=entry.get("payment_date"),
			multiplier=1.0,
			paid_share=0,
		)

	def insert(self, row):
		self.line_factors.set_net_amounts(row)
		doc = frappe.get_doc(row)
		doc.insert(ignore_permissions=True)
		self.ledger.add(doc.as_dict())
		self.escalator.add(doc)
