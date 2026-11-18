import frappe
from frappe.utils import flt, getdate

from sales_commission.utils.ledger_rows import PRECISION, apportion, multiplier_groups
from sales_commission.utils.line_amounts import LineAmounts


class LedgerWriter:
	"""Builds and inserts the Commission Ledger rows of one document."""

	def __init__(self, doctype, name, ledger, escalator, settings):
		self.doctype = doctype
		self.name = name
		self.ledger = ledger
		self.escalator = escalator
		self.settings = settings
		self.line_amounts = LineAmounts(doctype, name)

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
		frappe.db.set_value(
			"Commission Ledger",
			{"name": ["in", [r.name for r in rows]]},
			{"is_cancelled": 1, "status": "Cancelled"},
			update_modified=False,
		)
		self.escalator.forget()
		if self.holds_value(old):
			self.insert_cancelled_reverse(entry, old)
		self.ledger.drop_rows(entry.key, rows)

	def reverse_paid_rows(self, entry, old):
		"""Rows already paid on a salary slip stay live: a normal reversal is taken off a later slip."""
		if self.holds_value(old):
			self.post_reversal(entry, old, -old.earned, -old.paid, "Reversal", None)

	def holds_value(self, old):
		return any(flt(old[name]) for name in ("earned", "paid", "zar", "base", "recognised"))

	def insert_cancelled_reverse(self, entry, old):
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
		self.escalator.stamp(row, flt(-old.recognised, PRECISION))
		frappe.get_doc(row).insert(ignore_permissions=True)

	def post_reversal(self, entry, old, diff_earned, diff_paid, kind, date, salary_slip=None):
		"""Takes back a share of what the key holds, from each multiplier's rows at that multiplier."""
		base_fraction = self.fraction(diff_earned, old.earned, diff_paid, old.paid)
		paid_fraction = self.fraction(diff_paid, old.paid, diff_earned, old.earned)
		groups = [g for g in multiplier_groups(old.rows) if self.holds_value(g)]
		groups = groups or multiplier_groups([old.latest])
		earned = self.part_amounts(diff_earned, base_fraction, [g.earned for g in groups])
		paid = self.part_amounts(diff_paid, paid_fraction, [g.paid for g in groups])
		for index, group in enumerate(groups):
			source = group.source
			row = self.base_row(entry)
			row.update(
				entry_type=kind,
				posting_date=date or getdate(),
				reverses_entry=source.name,
				earned_amount=earned[index],
				paid_share=paid[index],
				exchange_rate=source.exchange_rate,
				base_commission=flt(group.base * base_fraction, PRECISION),
				commission_amount=flt(group.zar * base_fraction, PRECISION),
				multiplier=group.multiplier,
				fiscal_year=source.fiscal_year,
				sales_date=source.sales_date,
				payment_date=source.payment_date,
				salary_slip=salary_slip,
			)
			self.escalator.stamp(row, flt(group.recognised * paid_fraction, PRECISION))
			self.insert(row)

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
		net, base_net = self.line_amounts.get(entry.reference_row, entry.credit_note) or (0, 0)
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
			net_amount=net,
			base_net_amount=base_net,
			sales_date=entry.get("sales_date"),
			payment_date=entry.get("payment_date"),
			multiplier=1.0,
			paid_share=0,
		)

	def insert(self, row):
		doc = frappe.get_doc(row)
		doc.insert(ignore_permissions=True)
		self.ledger.add(doc.as_dict())
		self.escalator.add(doc)
