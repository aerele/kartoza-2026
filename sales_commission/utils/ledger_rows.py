from collections import defaultdict

import frappe
from frappe.utils import flt

PRECISION = 2


def row_key(row):
	"""The key of a row. A credit note's rows ignore the payment fields, they only show its refund."""
	note = row.credit_note or ""
	return (
		row.reference_row,
		"" if note else row.payment_doctype or "",
		"" if note else row.payment_name or "",
		note,
		row.sales_person or "",
	)


def apportion(total, weights):
	"""total split in the proportion of the weights, the last part takes the rounding rest."""
	whole = sum(weights)
	shares = [w / whole if whole else 1 / len(weights) for w in weights]
	parts = [flt(total * share, PRECISION) for share in shares[:-1]]
	return [*parts, flt(total - sum(parts), PRECISION)]


def top_first(multipliers, rooms, sales):
	"""(index, amount) pieces taking `sales` from the multipliers, in the order the pieces must be posted.

	A reduction (sales < 0) comes off the highest multiplier first, since that part is the highest in the
	year-to-date; a give-back (sales > 0) fills the lowest first. `rooms` is what each can give or take."""
	sign = 1 if sales > 0 else -1
	order = sorted(range(len(rooms)), key=lambda i: multipliers[i], reverse=sales < 0)
	order = [i for i in order if rooms[i] > 0]
	left = abs(sales)
	pieces = []
	for i in order:
		take = min(left, rooms[i])
		if take > 0:
			pieces.append([i, take])
		left -= take
	if pieces:
		pieces[-1][1] += left  # the last piece takes what the rooms miss
	return [(i, sign * flt(take, PRECISION)) for i, take in pieces]


def piece_weights(groups, pieces, name):
	"""Weight of each piece for an amount: the part of its multiplier's group that the piece takes."""
	return [abs(take) * abs(groups[i][name]) / abs(groups[i].recognised) for i, take in pieces]


def multiplier_groups(rows):
	"""Sums of the rows per multiplier, each with the latest positive row as its source."""
	groups = {}
	for row in rows:
		group = groups.setdefault(
			flt(row.multiplier, 6),
			frappe._dict(multiplier=flt(row.multiplier), earned=0, paid=0, zar=0, base=0, recognised=0),
		)
		group.earned += flt(row.earned_amount)
		group.paid += flt(row.paid_share)
		group.zar += flt(row.commission_amount)
		group.base += flt(row.base_commission)
		group.recognised += flt(row.running_total_after) - flt(row.running_total_before)
		if flt(row.earned_amount) > 0 or flt(row.paid_share) > 0 or not group.get("source"):
			group.source = row
	return list(groups.values())


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

	def credit_notes(self):
		"""Names of the credit notes that have live rows."""
		return {r.credit_note for rows in self.by_key.values() for r in rows if r.credit_note}

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
