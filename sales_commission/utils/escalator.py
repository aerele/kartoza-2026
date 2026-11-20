import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.utils import flt


class Escalator:
	"""Sets the fiscal year and the year-to-date paid sales before and after each new ledger row."""

	def __init__(self):
		self.totals = {}

	def set_fiscal_year(self, entry):
		"""The fiscal year of the row's posting date, which its year-to-date sales belong to."""
		entry.fiscal_year = get_fiscal_year(entry.posting_date, company=entry.company)[0]

	def stamp(self, row, recognised):
		"""Year-to-date sales before and after the row; the difference is what the row counts."""
		before = 0.0
		if row.sales_person and row.fiscal_year:
			before = self.running_total(row.sales_person, row.fiscal_year)
		row.running_total_before = before
		row.running_total_after = flt(before + flt(recognised), 2)

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


def split_at_target(before, amount, target, multiplier):
	"""(multiplier, amount) parts of sales added after `before`: within the target at 1, above it at the multiplier."""
	# no target means no escalator, never a multiplier on a 0 target
	if not target or multiplier == 1:
		return [(1.0, amount)]
	if before >= target:
		return [(multiplier, amount)]
	room = flt(target - before, 2)
	if amount <= room:
		return [(1.0, amount)]
	return [(1.0, room), (multiplier, flt(amount - room, 2))]


def shares_of(rows):
	"""(multiplier, share) of the rows by the sales each counted, so a top-up or a moved advance follows the split."""
	weights = {}
	for row in rows:
		weight = flt(row.running_total_after) - flt(row.running_total_before)
		weights[flt(row.multiplier, 6)] = weights.get(flt(row.multiplier, 6), 0) + weight
	total = sum(weights.values())
	if not total:
		return [(flt(rows[-1].multiplier), 1.0)]
	return [(multiplier, weight / total) for multiplier, weight in weights.items()]


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
