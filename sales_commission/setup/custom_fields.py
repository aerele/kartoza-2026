import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

CLASSIFICATION_OPTIONS = "\nNew\nRenewal"
COMPANY_CURRENCY = "Company:company:default_currency"


def line_fields(insert_after):
	"""Per-line commission on Sales Order Item / Sales Invoice Item. Hidden on print."""
	common = {"read_only": 1, "no_copy": 1, "print_hide": 1}
	return [
		{
			"fieldname": "commission_section",
			"label": "Commission",
			"fieldtype": "Section Break",
			"insert_after": insert_after,
			"collapsible": 1,
		},
		{
			**common,
			"fieldname": "commission_classification",
			"label": "Classification",
			"fieldtype": "Select",
			"options": CLASSIFICATION_OPTIONS,
			"insert_after": "commission_section",
		},
		{
			**common,
			"fieldname": "commission_note",
			"label": "Commission Note",
			"fieldtype": "Data",
			"insert_after": "commission_classification",
		},
		{
			**common,
			"fieldname": "commission_percent",
			"label": "Commission %",
			"fieldtype": "Percent",
			"insert_after": "commission_note",
		},
		{
			**common,
			"fieldname": "commission_factor",
			"label": "Classification Factor",
			"fieldtype": "Float",
			"insert_after": "commission_percent",
		},
		{
			"fieldname": "commission_column_break",
			"fieldtype": "Column Break",
			"insert_after": "commission_factor",
		},
		{
			**common,
			"fieldname": "commission_amount",
			"label": "Commission Amount",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": "commission_column_break",
		},
		{
			**common,
			"fieldname": "base_commission_amount",
			"label": "Estimated Commission (Company Currency)",
			"fieldtype": "Currency",
			"options": COMPANY_CURRENCY,
			"insert_after": "commission_amount",
		},
		{
			**common,
			"fieldname": "target_multiplier",
			"label": "Annual Target Escalator",
			"fieldtype": "Data",
			"insert_after": "base_commission_amount",
		},
	]


def header_fields(insert_after):
	"""Order / invoice line commission totals. Hidden on print."""
	totals = {"read_only": 1, "no_copy": 1, "print_hide": 1}
	return [
		{
			"fieldname": "sales_commission_section",
			"label": "Sales Commission",
			"fieldtype": "Section Break",
			"insert_after": insert_after,
			"collapsible": 1,
		},
		{
			"fieldname": "commission_column_break",
			"fieldtype": "Column Break",
			"insert_after": "sales_commission_section",
		},
		{
			**totals,
			"fieldname": "total_commission_amount",
			"label": "Total Commission Amount",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": "commission_column_break",
		},
		{
			**totals,
			"fieldname": "base_total_commission_amount",
			"label": "Total Commission Amount (Company Currency)",
			"fieldtype": "Currency",
			"options": COMPANY_CURRENCY,
			"insert_after": "total_commission_amount",
		},
	]


CUSTOM_FIELDS = {
	"Sales Order": header_fields("sales_team"),
	"Sales Invoice": header_fields("sales_team"),
	"Sales Order Item": line_fields("gross_profit"),
	"Sales Invoice Item": line_fields("base_net_amount"),
	"Item": [
		{
			"fieldname": "commission_percent",
			"label": "Commission %",
			"fieldtype": "Percent",
			"insert_after": "grant_commission",
			"depends_on": "eval:doc.grant_commission",
		}
	],
}


def create_commission_fields():
	create_custom_fields(with_module(CUSTOM_FIELDS), update=True)


def with_module(custom_fields):
	"""Every field belongs to the app's module, so uninstalling the app removes them."""
	return {
		doctype: [{**field, "module": "Sales Commission"} for field in fields]
		for doctype, fields in custom_fields.items()
	}


# Section holding the Sales Partner commission fields (Sales Invoice moved it in v16)
STANDARD_COMMISSION_SECTIONS = {
	"Sales Order": "sales_team_section_break",
	"Sales Invoice": "commission_section",
}


def hide_standard_commission_section():
	"""Hide ERPNext's Sales Partner commission section; only the app's own section shows."""
	for doctype, fieldname in STANDARD_COMMISSION_SECTIONS.items():
		name = f"{doctype}-{fieldname}-hidden"
		if frappe.db.get_value("Property Setter", name, "value") == "1":
			continue
		make_property_setter(doctype, fieldname, "hidden", 1, "Check")
		frappe.db.set_value("Property Setter", name, "module", "Sales Commission")


def make_target_distribution_optional():
	"""Sales Person Targets: the annual target needs no Monthly Distribution."""
	name = "Target Detail-distribution_id-reqd"
	if frappe.db.get_value("Property Setter", name, "value") == "0":
		return
	make_property_setter("Target Detail", "distribution_id", "reqd", 0, "Check")
	frappe.db.set_value("Property Setter", name, "module", "Sales Commission")


def hide_sales_team_commission_rate():
	"""Sales Team: the Commission Rate column is not used, the incentives come from the line commission."""
	for property_name in ("hidden", "in_list_view"):
		name = f"Sales Team-commission_rate-{property_name}"
		value = "1" if property_name == "hidden" else "0"
		if frappe.db.get_value("Property Setter", name, "value") == value:
			continue
		make_property_setter("Sales Team", "commission_rate", property_name, int(value), "Check")
		frappe.db.set_value("Property Setter", name, "module", "Sales Commission")
