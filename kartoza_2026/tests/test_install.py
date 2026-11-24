import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from kartoza_2026.setup.custom_fields import sales_team_section, show_sales_team_section
from kartoza_2026.tests import helpers as h


class TestInstall(h.CommissionTestCase):
	def test_sales_team_section_is_shown_again_after_another_app_hides_it(self):
		for doctype in ("Sales Order", "Sales Invoice"):
			make_property_setter(doctype, sales_team_section(doctype), "hidden", 1, "Check")
		show_sales_team_section()
		for doctype in ("Sales Order", "Sales Invoice"):
			frappe.clear_cache(doctype=doctype)
			section = frappe.get_meta(doctype).get_field(sales_team_section(doctype))
			self.assertEqual(section.hidden, 0, doctype)
