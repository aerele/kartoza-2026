import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import flt, fmt_money, getdate

from sales_commission.utils.escalator import annual_target, split_at_target, year_to_date_sales


class TargetMultipliers:
	"""Annual target multiplier of each salesperson on a document, read once per person."""

	# Only the sales above the annual target earn the Settings multiplier (as at payment): the document's
	# recognised sales are split against the person's paid sales of the fiscal year, so a document that
	# crosses the target gets a blended multiplier. No target, no Sales Team or a return means 1.

	def __init__(self, doc, enabled=True):
		self.doc = doc
		self.enabled = enabled and not doc.get("is_return")
		self.team = [(r.sales_person, flt(r.allocated_percentage)) for r in doc.get("sales_team") or []]
		self.fiscal_year = None
		self.cache = {}
		self.splits = {}

	def for_person(self, sales_person):
		if not self.enabled or not sales_person:
			return 1.0
		if sales_person not in self.cache:
			self.cache[sales_person] = self.read(sales_person)
		return self.cache[sales_person]

	def describe(self, row):
		"""Text of the multipliers a line earns at, with the sales of each part when the target is crossed."""
		if not row.get("commission_classification") or not row.get("commission_percent"):
			return ""
		people = list(dict.fromkeys(person for person, _percent in self.team if person))
		parts = [(1.0, 1.0)]
		if self.enabled and len(people) == 1:
			parts = self.shares_of(people[0])
		elif self.enabled and people:
			multipliers = sorted({m for person in people for m, _share in self.shares_of(person)})
			return ", ".join(f"{m:g}" for m in multipliers)
		if len(parts) == 1:
			return f"{parts[0][0]:g}"
		currency = frappe.get_cached_value("Company", self.doc.get("company"), "default_currency")
		sales = flt(row.get("base_net_amount"))
		return ", ".join(f"{m:g} ({fmt_money(sales * share, currency=currency)})" for m, share in parts)

	def for_line(self):
		"""Allocation weighted multiplier of the team: the factor of a line's estimate."""
		allocated = sum(percent for _person, percent in self.team)
		if not self.enabled or not allocated:
			return 1.0
		return sum(self.for_person(person) * percent for person, percent in self.team) / allocated

	def read(self, sales_person):
		year = self.get_fiscal_year()
		if not year:
			return 1.0
		target = annual_target(sales_person, year)
		if not target:
			return 1.0
		parts = self.split(sales_person, year, target)
		amount = sum(a for _m, a in parts)
		return sum(m * a for m, a in parts) / amount if amount else parts[0][0]

	def split(self, sales_person, year, target):
		"""(multiplier, amount) parts of the document's sales for the person, kept for the line texts."""
		multiplier = flt(frappe.get_cached_doc("Commission Settings").escalator_multiplier) or 1.0
		amount = self.sales_of(sales_person)
		parts = split_at_target(year_to_date_sales(sales_person, year), amount, target, multiplier)
		self.splits[sales_person] = parts
		return parts

	def shares_of(self, sales_person):
		"""(multiplier, share of the document's sales) per part of the person's split."""
		self.for_person(sales_person)
		parts = self.splits.get(sales_person) or [(1.0, 1.0)]
		total = sum(a for _m, a in parts)
		if not total:
			return [(parts[0][0], 1.0)]
		return [(m, a / total) for m, a in parts]

	def sales_of(self, sales_person):
		"""Net sales in company currency of the lines that earn commission, times the person's share."""
		percent = sum(p for person, p in self.team if person == sales_person)
		lines = (
			r
			for r in self.doc.get("items") or []
			if r.get("commission_classification") and r.get("commission_percent")
		)
		return sum(flt(r.get("base_net_amount")) for r in lines) * percent / 100

	def get_fiscal_year(self):
		if self.fiscal_year is None:
			date = self.doc.get("transaction_date") or self.doc.get("posting_date")
			try:
				self.fiscal_year = get_fiscal_year(getdate(date), company=self.doc.get("company"))[0]
			except frappe.ValidationError:
				# no fiscal year means no target to read
				self.fiscal_year = ""
		return self.fiscal_year
