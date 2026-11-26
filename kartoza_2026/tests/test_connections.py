import frappe
from frappe.desk.notifications import get_open_count
from frappe.utils import nowdate

from kartoza_2026.tests import helpers as h
from kartoza_2026.tests import payment_helpers as p
from kartoza_2026.tests.test_scenarios_payroll import PayrollScenarioCase

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
			# the form only asks for the counts when the dashboard has a fieldname
			self.assertTrue(data.fieldname, doctype)


class TestConnectionsFollowTheFlow(PayrollScenarioCase):
	"""The Connections count of each source document follows its ledger rows after every step."""

	def count(self, doctype, name):
		found = get_open_count(doctype, name)["count"]["external_links_found"]
		return next((row["count"] for row in found if row["doctype"] == LEDGER), None)

	def rows(self, **filters):
		return frappe.db.count(LEDGER, filters)

	def assert_count(self, doctype, name, **filters):
		expected = self.rows(**filters)
		self.assertGreater(expected, 0, f"{doctype} {name} has no ledger rows")
		self.assertEqual(self.count(doctype, name), expected, f"{doctype} {name}")

	def test_order_payment_invoice_and_salary_slip_counts_update(self):
		today = nowdate()
		item = h.make_item(h.make_group(), percent=10)
		order = h.make_order(h.make_customer(), [(item, 1, 1000)], team=[(self.person, 100)], date=today)
		self.assertEqual(self.count("Sales Order", order.name), 0)

		advance = p.pay(order, 400, date=today)
		self.assert_count("Sales Order", order.name, reference_name=order.name)
		self.assert_count("Payment Entry", advance.name, payment_name=advance.name)

		invoice = p.invoice_from_order(order, date=today, submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		self.assert_count("Sales Order", order.name, reference_name=order.name)
		self.assert_count("Sales Invoice", invoice.name, reference_name=invoice.name)

		balance = p.pay(invoice, 600, date=today)
		self.assert_count("Sales Invoice", invoice.name, reference_name=invoice.name)
		self.assert_count("Payment Entry", balance.name, payment_name=balance.name)

		slip = self.slip(self.this_month())
		self.assertEqual(self.count("Salary Slip", slip.name), 0)
		slip.submit()
		self.assert_count("Salary Slip", slip.name, salary_slip=slip.name)

	def test_journal_entry_of_a_reconciled_credit_note_counts_its_rows(self):
		customer = h.make_customer()
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(customer, [(item, 1, 1000)], team=[(self.person, 100)])
		p.pay(invoice, 1000)
		note = p.make_credit_note(invoice, {0: 1})
		h.make_invoice(customer, [(h.make_item(h.make_group()), 1, 100)], team=[(self.person, 100)])
		p.reconcile(customer)
		journals = frappe.get_all(
			"Journal Entry Account",
			filters={"reference_type": "Sales Invoice", "reference_name": note.name, "docstatus": 1},
			pluck="parent",
		)
		self.assertTrue(journals)
		for journal in set(journals):
			self.assert_count("Journal Entry", journal, payment_name=journal)
