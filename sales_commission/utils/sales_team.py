import frappe
from frappe import _
from frappe.utils import flt

from sales_commission.utils.line_fields import line_multiplier, plain_amount
from sales_commission.utils.rates import person_ratio
from sales_commission.utils.target_estimate import TargetMultipliers


def validate_sales_person_not_group(doc, method=None):
	"""Customer / Sales Order / Sales Invoice: a Sales Team row must name a salesperson, not a group."""
	for row in doc.get("sales_team") or []:
		if frappe.db.get_value("Sales Person", row.sales_person, "is_group"):
			message = _(
				"Row {0}: Sales Person <b>{1}</b> is a group. Select a salesperson under that group instead."
			)
			frappe.throw(message.format(row.idx, row.sales_person), title=_("Group Sales Person"))


def validate_sales_person_employee(doc, method=None):
	"""Sales Order / Sales Invoice: the commission is paid to the Employee of every salesperson."""
	validate_sales_person_not_group(doc)
	for row in doc.get("sales_team") or []:
		if not frappe.db.get_value("Sales Person", row.sales_person, "employee"):
			message = _(
				"Sales Person <b>{0}</b> has no Employee. Link the Employee on the Sales Person before saving."
			)
			frappe.throw(message.format(row.sales_person), title=_("Employee Missing"))


def sales_team(doc):
	"""(sales person, allocated %) per Sales Team row. No row means nothing is posted."""
	rows = doc.get("sales_team") or []
	return [(row.sales_person, flt(row.allocated_percentage)) for row in rows]


def incentives_by_person(doc, multipliers=None):
	"""Incentive per Sales Team row: line commission times allocated %, own rate share and target multiplier."""
	multipliers = multipliers or TargetMultipliers(doc)
	return [
		flt(
			sum(
				plain_amount(line, base=True)
				* flt(row.allocated_percentage)
				/ 100
				* person_ratio(line, row.sales_person)
				* person_factor(doc, line, row.sales_person, multipliers)
				for line in doc.items
			),
			row.precision("incentives"),
		)
		for row in doc.get("sales_team") or []
	]


def person_factor(doc, line, sales_person, multipliers):
	"""A return follows the multiplier of the invoice line it takes back, anything else the person's own."""
	if doc.get("is_return"):
		return line_multiplier(line)
	return multipliers.for_person(sales_person)


def set_incentives(doc, multipliers=None):
	"""Replace ERPNext's incentives with the commission this app works out per salesperson."""
	for row, incentives in zip(
		doc.get("sales_team") or [], incentives_by_person(doc, multipliers), strict=True
	):
		row.incentives = incentives
