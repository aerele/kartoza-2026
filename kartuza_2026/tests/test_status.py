import frappe

from kartuza_2026.tests import helpers as h
from kartuza_2026.tests import payment_helpers as p


class TestLedgerStatus(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.team = [(self.person, 100)]
		self.group = h.make_group()

	def statuses(self, doc):
		return sorted((r.entry_type, r.status) for r in h.ledger(doc.name))

	def test_advance_moves_to_the_invoice_and_the_balance_earns(self):
		item = h.make_item(self.group, percent=10)
		order = h.make_order(h.make_customer(), [(item, 1, 5000)], team=self.team)
		p.pay(order, 2000)
		self.assertEqual(self.statuses(order), [("Earned", "Advance")])
		invoice = p.invoice_from_order(order, submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		self.assertEqual(
			self.statuses(order), [("Advance Adjustment", "Moved to Invoice"), ("Earned", "Advance")]
		)
		self.assertEqual(self.statuses(invoice), [("Advance Adjustment", "Earned")])
		p.pay(invoice, 3000)
		self.assertEqual(self.statuses(invoice), [("Advance Adjustment", "Earned"), ("Earned", "Earned")])

	def test_credit_note_deduction_is_to_deduct(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=self.team)
		p.pay(invoice, 10000)
		p.make_credit_note(invoice, {0: 4})
		self.assertEqual(
			self.statuses(invoice), [("Credit Note Deduction", "To Deduct"), ("Earned", "Earned")]
		)

	def test_cancelled_payment_marks_its_rows_cancelled(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		payment = p.pay(invoice, 1000)
		payment.cancel()
		rows = h.ledger(invoice.name, is_cancelled=1)
		self.assertTrue(rows)
		self.assertEqual({r.status for r in rows}, {"Cancelled"})
		self.assertEqual(frappe.db.count("Commission Ledger", {"status": ["is", "not set"]}), 0)
