import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe.utils import flt, nowdate

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p


class TestCreditNotes(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.team = [(self.person, 100)]
		self.group = h.make_group()

	def total(self, invoice):
		return flt(sum(r.commission_amount for r in h.ledger(invoice.name)), 2)

	def test_credit_note_after_full_payment(self):
		customer = h.make_customer("EUR")
		line_a, line_b = h.make_item(self.group, percent=5), h.make_item(self.group, percent=5)
		invoice = h.make_invoice(
			customer, [(line_a, 1, 2040), (line_b, 1, 7960)], team=self.team, currency="EUR", rate=17.035705
		)
		p.pay(invoice, 10000, rate=17.035705)
		self.assertEqual(self.total(invoice), 8517.85)
		note = p.make_credit_note(invoice, {0: 1})
		self.assertEqual(flt(note.total_commission_amount, 2), -102)
		deduction = [r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual([flt(r.commission_amount, 2) for r in deduction], [-1737.64])

	def test_half_paid_then_credit_note_then_rest(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=self.team)
		p.pay(invoice, 5000)
		self.assertEqual(self.total(invoice), 250)
		p.make_credit_note(invoice, {0: 4})
		self.assertEqual(self.total(invoice), 150)
		p.pay(invoice, 1000)
		self.assertEqual(self.total(invoice), 300)

	def test_unpaid_invoice_fully_credited(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		p.make_credit_note(invoice)
		self.assertEqual(h.ledger(invoice.name), [])

	def test_cancelled_credit_note_gives_the_deduction_back(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=self.team)
		p.pay(invoice, 10000)
		note = p.make_credit_note(invoice, {0: 4})
		self.assertEqual(self.total(invoice), 300)
		self.assertEqual(note.items[0].commission_classification, "New")
		note.cancel()
		self.assertEqual(self.total(invoice), 500)
		history = h.ledger(invoice.name, is_cancelled=1)
		self.assertEqual([r.entry_type for r in history], ["Credit Note Deduction", "Reversal"])
		self.assertEqual(flt(sum(r.commission_amount for r in history), 2), 0)

	def unlinked_note(self, invoice, item, qty=1, rate=2760):
		"""A credit note against the invoice whose line is not linked to an invoice line."""
		note = make_sales_return(invoice.name)
		note.posting_date = nowdate()
		note.set_posting_time = 1
		note.items = note.items[:1]
		note.items[0].item_code = item
		note.items[0].qty = -qty
		note.items[0].rate = rate
		note.items[0].sales_invoice_item = None
		note.insert(ignore_permissions=True)
		note.submit()
		return note

	def earlier_sale_then_invoice(self, items):
		customer = h.make_customer()
		h.make_order(customer, [(h.make_item(self.group, percent=5), 1, 100)], team=self.team)
		invoice = h.make_invoice(customer, [(item, 1, 2760) for item in items], team=self.team)
		p.pay(invoice, invoice.grand_total)
		return customer, invoice

	def test_linked_return_line_copies_the_original_line(self):
		item = h.make_item(self.group, percent=5)
		_, invoice = self.earlier_sale_then_invoice([item])
		note = p.make_credit_note(invoice)
		line = note.items[0]
		self.assertEqual((line.commission_classification, flt(line.commission_percent)), ("Renewal", 5))
		self.assertEqual(flt(line.commission_amount, 2), -138)

	def test_unlinked_return_line_uses_the_first_original_line_of_the_item(self):
		item = h.make_item(self.group, percent=5)
		_, invoice = self.earlier_sale_then_invoice([item, item])
		self.assertEqual(self.total(invoice), 276)
		note = self.unlinked_note(invoice, item)
		self.assertEqual(
			(note.items[0].commission_classification, flt(note.items[0].commission_percent)), ("Renewal", 5)
		)
		deduction = [r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual(
			[(r.reference_row, flt(r.commission_amount, 2)) for r in deduction],
			[(invoice.items[0].name, -138)],
		)
		self.assertEqual(self.total(invoice), 138)

	def test_unlinked_return_line_falls_back_to_the_item_group(self):
		other = h.make_item(self.group, percent=5)
		_, invoice = self.earlier_sale_then_invoice([h.make_item(self.group, percent=5)])
		self.unlinked_note(invoice, other)
		deduction = [r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual([flt(r.commission_amount, 2) for r in deduction], [-138])

	def test_credit_note_without_an_original_takes_back_its_own_commission(self):
		item = h.make_item(self.group, percent=5)
		customer = h.make_customer()
		h.make_order(customer, [(h.make_item(self.group, percent=5), 1, 100)], team=self.team)
		note = h.make_invoice(customer, [(item, -1, 2760)], team=self.team, is_return=1)
		line = note.items[0]
		self.assertEqual((line.commission_classification, flt(line.commission_percent)), ("Renewal", 5))
		self.assertEqual(flt(line.commission_amount, 2), -138)
		rows = h.ledger(note.name)
		self.assertEqual([r.entry_type for r in rows], ["Credit Note Deduction"])
		self.assertEqual((rows[0].credit_note, rows[0].sales_person), (note.name, self.person))
		self.assertEqual(
			(flt(rows[0].commission_amount, 2), flt(rows[0].running_total_after - rows[0].running_total_before, 2)), (-138, -2760)
		)
		note.cancel()
		self.assertEqual(h.ledger(note.name), [])
		history = h.ledger(note.name, is_cancelled=1)
		self.assertEqual(flt(sum(r.commission_amount for r in history), 2), 0)
		self.assertEqual(len(history), 2)
