import frappe

LINE_FIELDS = [
	"name",
	"idx",
	"item_code",
	"item_group",
	"commission_classification",
	"commission_percent",
	"commission_factor",
	"commission_amount",
	"net_amount",
	"base_net_amount",
	"target_multiplier",
]


class OriginalLines:
	"""Finds the line of the original invoice a credit note line belongs to."""

	# Order: the linked line, the first line (by idx) with the same item, the first with the same Item Group.

	def __init__(self, lines):
		lines = sorted(lines, key=lambda line: line.get("idx") or 0)
		self.by_name = {line.get("name"): line for line in lines}
		self.by_item = {}
		self.by_group = {}
		for line in lines:
			self.by_item.setdefault(line.get("item_code"), line)
			self.by_group.setdefault(line.get("item_group"), line)

	@classmethod
	def for_note(cls, note):
		"""Original lines of a credit note, read in one query."""
		linked = [r.sales_invoice_item for r in note.items if r.get("sales_invoice_item")]
		or_filters = []
		if note.get("return_against"):
			or_filters.append(["parent", "=", note.return_against])
		if linked:
			or_filters.append(["name", "in", linked])
		if not or_filters:
			return cls([])
		return cls(frappe.get_all("Sales Invoice Item", or_filters=or_filters, fields=LINE_FIELDS))

	def match(self, row):
		"""(original line, linked) for a credit note line. None when the original has no such line."""
		line = self.by_name.get(row.get("sales_invoice_item"))
		if line:
			return line, True
		line = self.by_item.get(row.get("item_code")) or self.by_group.get(row.get("item_group"))
		return (line, False) if line else None
