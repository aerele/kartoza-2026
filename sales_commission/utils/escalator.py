import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import flt


class Escalator:
	"""Sets fiscal year, target, running totals and multiplier on a new positive ledger entry."""

	def __init__(self):
		self.totals = {}
		self.targets = {}

	def apply(self, entry, recognised):
		"""recognised: the sales amount this row adds to the person's year-to-date sales."""
		entry.fiscal_year = get_fiscal_year(entry.posting_date, company=entry.company)[0]
		target = self.target(entry.sales_person, entry.fiscal_year)
		entry.multiplier = 1.0
		self.stamp(entry, recognised)
		# no target means no escalator, never a multiplier on a 0 target
		if target and entry.running_total_before >= target:
			entry.multiplier = flt(frappe.get_cached_doc("Commission Settings").escalator_multiplier)

	def stamp(self, row, recognised):
		"""Year-to-date sales before and after the row; the difference is what the row counts."""
		before = 0.0
		if row.sales_person and row.fiscal_year:
			before = self.running_total(row.sales_person, row.fiscal_year)
		row.running_total_before = before
		row.running_total_after = flt(before + flt(recognised), 2)

	def target(self, sales_person, fiscal_year):
		"""Sales Person > Targets of the fiscal year without an Item Group, read once per sync."""
		key = (sales_person, fiscal_year)
		if key not in self.targets:
			self.targets[key] = annual_target(sales_person, fiscal_year)
		return self.targets[key]

	def running_total(self, sales_person, fiscal_year):
		"""Read once per person and year in a sync, then carried forward row by row."""
		key = (sales_person, fiscal_year)
		if key not in self.totals:
			# the row lock makes two payments of one person count one after the other
			frappe.db.get_value("Sales Person", sales_person, "name", for_update=True)
			self.totals[key] = year_to_date_sales(sales_person, fiscal_year)
		return self.totals[key]

	def add(self, row):
		"""A row posted in this sync moves the running total of its person and year."""
		key = (row.sales_person, row.fiscal_year)
		if key in self.totals:
			self.totals[key] = flt(self.totals[key] + counted(row), 2)

	def forget(self):
		"""Rows were cancelled, so the totals are read again when needed."""
		self.totals = {}


def annual_target(sales_person, fiscal_year):
	target = frappe.qb.DocType("Target Detail")
	total = (
		frappe.qb.from_(target)
		.select(frappe.query_builder.functions.Sum(target.target_amount))
		.where(target.parenttype == "Sales Person")
		.where(target.parent == sales_person)
		.where(target.fiscal_year == fiscal_year)
		.where((target.item_group.isnull()) | (target.item_group == ""))
		.run()
	)
	return flt(total[0][0] if total else 0)


def counted(row):
	return flt(row.running_total_after) - flt(row.running_total_before)


def year_to_date_sales(sales_person, fiscal_year):
	ledger = frappe.qb.DocType("Commission Ledger")
	total = (
		frappe.qb.from_(ledger)
		.select(frappe.query_builder.functions.Sum(ledger.running_total_after - ledger.running_total_before))
		.where(ledger.sales_person == sales_person)
		.where(ledger.fiscal_year == fiscal_year)
		.where(ledger.is_cancelled == 0)
		.run()
	)
	return flt(total[0][0] if total else 0, 2)
