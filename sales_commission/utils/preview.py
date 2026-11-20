import json

import frappe
from frappe import _
from frappe.utils import flt

from sales_commission.utils.validation import calculate_commission

PREVIEW_DOCTYPES = ("Sales Order", "Sales Invoice")


@frappe.whitelist()
def get_line_commission(doc):
	"""Commission of every line and the totals of an unsaved Sales Order / Sales Invoice, for the form."""
	data = parse_document(doc)
	check_access(data)
	document = frappe.get_doc(data)
	for row in document.items:
		# the Item Group comes from the Item, never from what the browser sent
		if row.item_code:
			row.item_group = frappe.get_cached_value("Item", row.item_code, "item_group")
	calculate_commission(document)
	return {
		"lines": [
			{
				"name": row.name,
				"commission_classification": row.commission_classification or "",
				"commission_percent": flt(row.commission_percent),
				"commission_factor": flt(row.commission_factor),
				"commission_amount": flt(row.commission_amount),
				"base_commission_amount": flt(row.base_commission_amount),
				"target_multiplier": row.target_multiplier or "",
			}
			for row in document.items
		],
		"incentives": [flt(row.incentives) for row in document.get("sales_team") or []],
		"total_commission_amount": flt(document.total_commission_amount),
		"base_total_commission_amount": flt(document.base_total_commission_amount),
	}


def parse_document(doc):
	data = json.loads(doc)
	if not isinstance(data, dict) or data.get("doctype") not in PREVIEW_DOCTYPES:
		frappe.throw(
			_("Commission can only be previewed for a Sales Order or a Sales Invoice."),
			title=_("Not Allowed"),
		)
	for field in ("customer", "company"):
		if data.get(field) is not None and not isinstance(data[field], str):
			frappe.throw(_("<b>{0}</b> must be text.").format(field.title()), title=_("Not Allowed"))
	return data


def check_access(data):
	frappe.has_permission(data["doctype"], "read", throw=True)
	for doctype, field in (("Customer", "customer"), ("Company", "company")):
		if data.get(field):
			frappe.has_permission(doctype, "read", doc=data[field], throw=True)
