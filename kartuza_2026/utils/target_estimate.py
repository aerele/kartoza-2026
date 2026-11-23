import frappe
from frappe.utils import flt, fmt_money

from kartuza_2026.utils.target_sales import NO_SPLIT, document_split, order_split, stored_split


class TargetMultipliers:
	"""Annual target multiplier of each salesperson on a document, read once per person and order."""

	# The person's submitted orders and invoices dated before this document are added up (net sales x
	# contribution %). The part of the document within the annual target is at 1, the part above it at the
	# Settings multiplier, so a document that crosses the target gets a blended multiplier. The split is
	# frozen on submit. Invoice lines made from an order follow that order's split. No target, no Sales
	# Team or a return means 1.

	def __init__(self, doc, enabled=True):
		self.doc = doc
		self.enabled = enabled and not doc.get("is_return")
		self.team = [(r.sales_person, flt(r.allocated_percentage)) for r in doc.get("sales_team") or []]
		self.splits = {}

	def for_person(self, sales_person, row=None):
		"""Weighted multiplier of the person on the line (on the document when no line is given)."""
		if not self.enabled or not sales_person:
			return 1.0
		return sum(m * share for m, share in self.shares_of(sales_person, row))

	def for_line(self, row=None):
		"""Allocation weighted multiplier of the team: the factor of a line's estimate."""
		allocated = sum(percent for _person, percent in self.team)
		if not self.enabled or not allocated:
			return 1.0
		return sum(self.for_person(person, row) * percent for person, percent in self.team) / allocated

	def describe(self, row):
		"""Text of the multipliers a line earns at, with the sales of each part when the target is crossed."""
		if not row.get("commission_classification") or not row.get("commission_percent"):
			return ""
		people = list(dict.fromkeys(person for person, _percent in self.team if person))
		parts = NO_SPLIT
		if self.enabled and len(people) == 1:
			parts = self.shares_of(people[0], row)
		elif self.enabled and people:
			multipliers = sorted({m for person in people for m, _share in self.shares_of(person, row)})
			return ", ".join(f"{m:g}" for m in multipliers)
		if len(parts) == 1:
			return f"{parts[0][0]:g}"
		currency = frappe.get_cached_value("Company", self.doc.get("company"), "default_currency")
		sales = flt(row.get("base_net_amount"))
		return ", ".join(f"{m:g} ({fmt_money(sales * share, currency=currency)})" for m, share in parts)

	def shares_of(self, sales_person, row=None):
		"""(multiplier, share) of the person's split for the line: its order's split or the document's."""
		order = self.order_of(row)
		key = (order, sales_person)
		if key not in self.splits:
			self.splits[key] = self.read(sales_person, order)
		return self.splits[key]

	def read(self, sales_person, order):
		if order:
			return order_split(order, sales_person)
		if self.doc.docstatus == 1:
			split = stored_split(self.doc, sales_person)
			if split is not None:
				return split
		return document_split(self.doc, sales_person)

	def order_of(self, row):
		if row is None or self.doc.doctype != "Sales Invoice":
			return None
		return row.get("sales_order") or None
