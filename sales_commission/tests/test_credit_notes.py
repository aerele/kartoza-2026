import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe.utils import add_days, flt, getdate, nowdate

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

	def spread_invoice(self, person=None):
		"""Consulting 100 (5%), Computer Expenses 100 (no commission), Hosting 100 (5%), one salesperson."""
		lines = [
			h.make_item(self.group, percent=5),
			h.make_item(self.group),
			h.make_item(self.group, percent=5),
		]
		team = [(person or self.person, 100)]
		return h.make_invoice(
			h.make_customer(), [(item, 1, 100) for item in lines], team=team, date=add_days(nowdate(), -5)
		)

	def ytd(self, invoice):
		rows = h.ledger(invoice.name)
		return flt(sum(r.running_total_after - r.running_total_before for r in rows), 2)

	def amounts(self, invoice, entry_type=None):
		rows = [r for r in h.ledger(invoice.name) if not entry_type or r.entry_type == entry_type]
		return sorted(flt(r.commission_amount, 2) for r in rows)

	def test_return_of_unpaid_line_pays_the_rest_of_the_others(self):
		invoice = self.spread_invoice()
		p.pay(invoice, 200, date=add_days(nowdate(), -3))
		self.assertEqual(self.total(invoice), 6.66)
		p.make_credit_note(invoice, {1: 1})
		self.assertEqual(self.total(invoice), 10)
		catch_up = [r for r in h.ledger(invoice.name) if r.posting_date == getdate(nowdate())]
		self.assertEqual([flt(r.commission_amount, 2) for r in catch_up], [1.67, 1.67])
		self.assertEqual({r.entry_type for r in catch_up}, {"Earned"})
		self.assertEqual(self.ytd(invoice), 200)

	def test_reconciling_the_return_later_changes_nothing(self):
		invoice = self.spread_invoice()
		p.pay(invoice, 200, date=add_days(nowdate(), -3))
		note = p.make_credit_note(invoice, {1: 1}, submit=False)
		note.update_outstanding_for_self = 1
		note.save(ignore_permissions=True)
		note.submit()
		self.assertEqual(self.total(invoice), 10)
		count = len(h.ledger(invoice.name))
		p.reconcile(invoice.customer)
		self.assertEqual(self.total(invoice), 10)
		self.assertEqual(len(h.ledger(invoice.name)), count)
		self.assertEqual(self.ytd(invoice), 200)

	def test_return_of_a_commission_line_after_part_payment(self):
		invoice = self.spread_invoice()
		p.pay(invoice, 200)
		p.make_credit_note(invoice, {0: 1})
		self.assertEqual(self.total(invoice), 5)
		self.assertEqual(self.ytd(invoice), 100)
		by_line = {}
		for row in h.ledger(invoice.name):
			by_line[row.reference_row] = by_line.get(row.reference_row, 0) + flt(row.commission_amount)
		self.assertEqual(
			(flt(by_line[invoice.items[0].name], 2), flt(by_line[invoice.items[2].name], 2)), (0, 5)
		)

	def test_return_first_then_pay_the_rest(self):
		invoice = self.spread_invoice()
		p.make_credit_note(invoice, {1: 1})
		self.assertEqual(h.ledger(invoice.name), [])
		p.pay(invoice, 200)
		self.assertEqual(self.total(invoice), 10)
		self.assertEqual(self.ytd(invoice), 200)

	def test_catch_up_is_shared_by_the_payments_in_proportion(self):
		invoice = self.spread_invoice()
		first = p.pay(invoice, 100, date=add_days(nowdate(), -3))
		second = p.pay(invoice, 100, date=add_days(nowdate(), -2))
		p.make_credit_note(invoice, {1: 1})
		self.assertEqual(self.total(invoice), 10)
		for payment in (first, second):
			rows = [r for r in h.ledger(invoice.name) if r.payment_name == payment.name]
			self.assertEqual(flt(sum(r.commission_amount for r in rows), 2), 5)
			self.assertEqual(len(rows), 4)

	def test_cancelled_credit_note_takes_the_catch_up_back(self):
		invoice = self.spread_invoice()
		p.pay(invoice, 200)
		note = p.make_credit_note(invoice, {1: 1})
		self.assertEqual(self.total(invoice), 10)
		note.cancel()
		# each line goes back to the two thirds of its 5 it had earned on the 200 paid: 3.33 + 3.33
		self.assertEqual(self.total(invoice), 6.66)
		self.assertEqual(self.ytd(invoice), 133.34)

	def test_catch_up_keeps_the_escalator_multiplier(self):
		earlier = h.make_invoice(
			h.make_customer(), [(h.make_item(self.group, percent=5), 1, 1000)], team=self.team
		)
		p.pay(earlier, 1000)
		h.set_target(self.person, h.fiscal_year_of(nowdate()), 500)
		invoice = self.spread_invoice()
		p.pay(invoice, 200)
		p.make_credit_note(invoice, {1: 1})
		rows = h.ledger(invoice.name)
		self.assertEqual({flt(r.multiplier) for r in rows}, {1.5})
		self.assertEqual(self.total(invoice), 15)

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
		# the 5000 paid is now 5000 of the 6000 still owed, so the line has earned 5/6 of 300
		self.assertEqual(self.total(invoice), 250)
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
			(
				flt(rows[0].commission_amount, 2),
				flt(rows[0].running_total_after - rows[0].running_total_before, 2),
			),
			(-138, -2760),
		)
		note.cancel()
		self.assertEqual(h.ledger(note.name), [])
		history = h.ledger(note.name, is_cancelled=1)
		self.assertEqual(flt(sum(r.commission_amount for r in history), 2), 0)
		self.assertEqual(len(history), 2)
