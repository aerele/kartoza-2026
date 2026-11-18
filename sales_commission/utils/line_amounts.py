from collections import defaultdict

import frappe
from frappe.utils import flt

from sales_commission.utils.original_lines import OriginalLines

LINE_FIELDS = ["name", "idx", "item_code", "item_group", "net_amount", "base_net_amount"]


class LineAmounts:
	"""Net amounts (document and company currency) of the lines a document's ledger rows come from."""

	# A credit note row takes the net amount of the note's own line(s) that match the row's reference line.

	def __init__(self, doctype, name):
		self.doctype = doctype
		self.name = name
		self.lines = None
		self.notes = {}
		self.originals = None

	def get(self, reference_row, credit_note=None):
		"""(net_amount, base_net_amount) for a ledger row, or None when the line is gone."""
		lines = self.read_lines()
		if not credit_note or credit_note == self.name:
			return self.amounts([lines.get(reference_row)])
		if self.doctype != "Sales Invoice":
			return None
		if self.originals is None:
			self.originals = OriginalLines(lines.values())
		matched = []
		for row in self.read_note(credit_note):
			original = self.originals.match(row)
			if original and original[0].get("name") == reference_row:
				matched.append(row)
		return self.amounts(matched)

	def amounts(self, rows):
		rows = [r for r in rows if r]
		if not rows:
			return None
		return sum(flt(r.net_amount) for r in rows), sum(flt(r.base_net_amount) for r in rows)

	def read_lines(self):
		if self.lines is None:
			rows = frappe.get_all(
				f"{self.doctype} Item",
				filters={"parent": self.name},
				fields=LINE_FIELDS,
				parent_doctype=self.doctype,
			)
			self.lines = {row.name: row for row in rows}
		return self.lines

	def read_note(self, note):
		if note not in self.notes:
			self.notes[note] = frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": note},
				fields=[*LINE_FIELDS, "sales_invoice_item"],
				parent_doctype="Sales Invoice",
			)
		return self.notes[note]
