import frappe
from frappe import _
from frappe.utils import flt


def validate_sales_person_employee(doc, method=None):
	"""Sales Order / Sales Invoice: the commission is paid to the Employee of every salesperson."""
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
