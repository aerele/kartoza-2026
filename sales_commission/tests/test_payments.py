import frappe
from frappe.utils import flt

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p


class TestReconciledPayments(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.team = [(self.person, 100)]
		self.group = h.make_group()

	def total(self, doc):
		return flt(sum(r.commission_amount for r in h.ledger(doc.name)), 2)

	def test_unallocated_payment_earns_when_it_is_matched_at_its_own_rate(self):
		customer = h.make_customer("USD")
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(customer, [(item, 1, 1000)], team=self.team, currency="USD", rate=17)
		p.unallocated_payment(customer, 1000, currency="USD", rate=18)
		self.assertEqual(h.ledger(invoice.name), [])
		p.reconcile(customer, "USD")
		rows = h.ledger(invoice.name)
		self.assertEqual([flt(r.exchange_rate) for r in rows], [18])
		self.assertEqual(self.total(invoice), 900)

	def invoice_and_standalone_note(self, paid=0):
		"""An invoice (optionally part paid) and a credit note without an original for the same amount."""
		customer = h.make_customer()
		invoice = h.make_invoice(customer, [(h.make_item(self.group, percent=5), 1, 2760)], team=self.team)
		if paid:
			p.pay(invoice, paid)
		note = h.make_invoice(
			customer, [(h.make_item(self.group, percent=5), -1, 2760)], team=self.team, is_return=1
		)
		return customer, invoice, note

	def test_credit_note_settled_against_an_unpaid_invoice_takes_back_nothing(self):
		customer, invoice, note = self.invoice_and_standalone_note()
		self.assertEqual(self.total(note), -138)
		p.reconcile(customer)
		self.assertEqual((self.total(invoice), self.total(note)), (0, 0))
		self.assertIn("Reversal", [r.entry_type for r in h.ledger(note.name)])

	def test_credit_note_settled_against_a_part_paid_invoice_is_taken_back_once(self):
		customer, invoice, note = self.invoice_and_standalone_note(paid=1380)
		self.assertEqual((self.total(invoice), self.total(note)), (69, -138))
		p.reconcile(customer)
		# half of the invoice is credited, so half of what it earned is taken back from the invoice, but
		# the 1380 paid now covers all that is still owed, so the other half earns its rest (+34.5)
		# and the credit note keeps only the half that settled nothing
		self.assertEqual(self.total(invoice), 69)
		self.assertEqual(self.total(note), -69)
		deduction = [r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual([r.credit_note for r in deduction], [note.name])
