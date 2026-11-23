import frappe
from frappe.utils import flt, fmt_money

from kartuza_2026.utils.ledger_rows import top_first


class ReturnSplits:
	"""Multiplier parts a credit note takes back from its original lines, read from the ledger in one query."""

	# The ledger takes a return from the part above the annual target first, so the estimate does the same.
	# The parts are the original line's paid ledger rows per multiplier, less earlier notes, scaled to the
	# line's net amount. A line without rows or with one multiplier has no split: the caller keeps the original's.

	def __init__(self, note, lines):
		self.note = note
		self.lines = {line.get("name"): line for line in lines}
		self.rooms = {}
		self.read()

	def read(self):
		if not self.lines:
			return
		rows = frappe.get_all(
			"Commission Ledger",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_row": ["in", list(self.lines)],
				"is_cancelled": 0,
			},
			fields=[
				"reference_row",
				"multiplier",
				"credit_note",
				"running_total_before",
				"running_total_after",
			],
			order_by="creation asc, name asc",
		)
		sales = {}
		for row in rows:
			if row.credit_note and row.credit_note == self.note.get("name"):
				continue
			parts = sales.setdefault(row.reference_row, {})
			multiplier = flt(row.multiplier, 6)
			parts[multiplier] = (
				parts.get(multiplier, 0) + flt(row.running_total_after) - flt(row.running_total_before)
			)
		for name, parts in sales.items():
			earned = sum(max(v, 0) for v in parts.values())
			if len(parts) > 1 and earned:
				scale = abs(flt(self.lines[name].get("base_net_amount"))) / earned
				self.rooms[name] = {m: v * scale for m, v in parts.items()}

	def take(self, line, amount):
		"""[(multiplier, amount)] the return of `amount` takes back from the line, top part first. [] without a split."""
		rooms = self.rooms.get(line.get("name"))
		if not rooms or not amount:
			return []
		multipliers = list(rooms)
		pieces = top_first(multipliers, [rooms[m] for m in multipliers], -amount)
		for i, piece in pieces:
			rooms[multipliers[i]] -= abs(piece)
		return [(multipliers[i], abs(piece)) for i, piece in pieces]


def blended(parts):
	"""Multiplier of the parts, weighted by the sales of each."""
	return sum(m * a for m, a in parts) / sum(a for _m, a in parts)


def describe_parts(parts, company):
	"""Text of the parts, like the invoice line text: multiplier and the sales of each part."""
	currency = frappe.get_cached_value("Company", company, "default_currency")
	return ", ".join(f"{m:g} ({fmt_money(a, currency=currency)})" for m, a in parts)
