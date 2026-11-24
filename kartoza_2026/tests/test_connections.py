import frappe
from frappe.desk.notifications import get_open_count

from kartoza_2026.tests import helpers as h
from kartoza_2026.tests import payment_helpers as p

LEDGER = "Commission Ledger"


class TestConnections(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.item = h.make_item(h.make_group(), percent=10)

	def ledger_count(self, doctype, name):
		found = get_open_count(doctype, name)["count"]["external_links_found"]
		return next((row["count"] for row in found if row["doctype"] == LEDGER), None)

	def test_invoice_and_its_payment_show_their_ledger_rows(self):
		invoice = h.make_invoice(h.make_customer(), [(self.item, 1, 1000)], team=[(self.person, 100)])
		payment = p.pay(invoice, 1000)
		rows = len(h.ledger(invoice.name))
		self.assertGreater(rows, 0)
		self.assertEqual(self.ledger_count("Sales Invoice", invoice.name), rows)
		self.assertEqual(self.ledger_count("Payment Entry", payment.name), rows)

	def test_order_shows_the_rows_of_its_advance(self):
		order = h.make_order(h.make_customer(), [(self.item, 1, 1000)], team=[(self.person, 100)])
		p.pay(order, 400)
		self.assertEqual(self.ledger_count("Sales Order", order.name), len(h.ledger(order.name)))

	def test_every_ledger_source_lists_the_commission_ledger(self):
		for doctype, fieldname in (
			("Sales Order", "reference_name"),
			("Sales Invoice", "reference_name"),
			("Payment Entry", "payment_name"),
			("Journal Entry", "payment_name"),
			("Salary Slip", "salary_slip"),
		):
			data = frappe.get_meta(doctype).get_dashboard_data()
			items = [item for group in data.transactions for item in group.get("items")]
			self.assertIn(LEDGER, items, doctype)
			self.assertEqual(data.non_standard_fieldnames[LEDGER], fieldname, doctype)
