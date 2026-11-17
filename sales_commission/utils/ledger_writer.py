import frappe
from frappe.utils import flt, getdate

from sales_commission.utils.ledger_rows import PRECISION


class LedgerWriter:
	"""Builds and inserts the Commission Ledger rows of one document."""

	def __init__(self, doctype, name, ledger, escalator, settings):
		self.doctype = doctype
		self.name = name
		self.ledger = ledger
		self.escalator = escalator
		self.settings = settings

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

	def post_reversal(self, entry, old, diff_earned, diff_paid, kind, date):
		"""Takes back a share of what the key holds, at the multiplier of the rows it reverses."""
		base_fraction = self.fraction(diff_earned, old.earned, diff_paid, old.paid)
		paid_fraction = self.fraction(diff_paid, old.paid, diff_earned, old.earned)
		source = old.latest
		row = self.base_row(entry)
		row.update(
			entry_type=kind,
			posting_date=date or getdate(),
			reverses_entry=source.name,
			earned_amount=diff_earned if base_fraction > -1 else -old.earned,
			paid_share=diff_paid if paid_fraction > -1 else -old.paid,
			exchange_rate=source.exchange_rate,
			base_commission=flt(old.base * base_fraction, PRECISION),
			commission_amount=flt(old.zar * base_fraction, PRECISION),
			multiplier=source.multiplier,
			fiscal_year=source.fiscal_year,
			sales_date=source.sales_date,
			payment_date=source.payment_date,
		)
		self.escalator.stamp(row, flt(old.recognised * paid_fraction, PRECISION))
		self.insert(row)

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
		doc = frappe.get_doc(row)
		doc.insert(ignore_permissions=True)
		self.ledger.add(doc.as_dict())
		self.escalator.add(doc)
