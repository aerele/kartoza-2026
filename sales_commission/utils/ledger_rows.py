from collections import defaultdict

import frappe
from frappe.utils import flt

PRECISION = 2


def row_key(row):
	return (
		row.reference_row,
		row.payment_doctype or "",
		row.payment_name or "",
		row.credit_note or "",
		row.sales_person or "",
	)


class LedgerRows:
	"""The live (not cancelled) Commission Ledger rows of a document, indexed once."""

	def __init__(self, doctype, name):
		self.by_key = defaultdict(list)
		self.by_line = defaultdict(list)
		rows = frappe.get_all(
			"Commission Ledger",
			filters={"reference_doctype": doctype, "reference_name": name, "is_cancelled": 0},
			fields=["*"],
			order_by="creation asc, name asc",
		)
		for row in rows:
			self.add(row)

	def add(self, row):
		self.by_key[row_key(row)].append(row)
		self.by_line[(row.reference_row, row.sales_person or "")].append(row)

	def drop_rows(self, key, rows):
		"""Removes these rows of a key from the live index."""
		names = {r.name for r in rows}
		self.by_key[key] = [r for r in self.by_key.get(key, []) if r.name not in names]
		if not self.by_key[key]:
			del self.by_key[key]
		for row in rows:
			self.by_line[(row.reference_row, row.sales_person or "")].remove(row)

	def keys(self):
		return list(self.by_key)

	def first_row(self, key):
		return self.by_key[key][0]

	def line_rows(self, reference_row, sales_person):
		return self.by_line.get((reference_row, sales_person or ""), [])

	def totals(self, key):
		return self.totals_of(self.by_key.get(key, []))

	def totals_of(self, rows):
		positive = [r for r in rows if flt(r.earned_amount) > 0 or flt(r.paid_share) > 0]
		return frappe._dict(
			rows=rows,
			latest=(positive or rows or [None])[-1],
			earned=flt(sum(flt(r.earned_amount) for r in rows), PRECISION),
			paid=flt(sum(flt(r.paid_share) for r in rows), PRECISION),
			zar=sum(flt(r.commission_amount) for r in rows),
			base=sum(flt(r.base_commission) for r in rows),
			recognised=sum(flt(r.running_total_after) - flt(r.running_total_before) for r in rows),
		)
