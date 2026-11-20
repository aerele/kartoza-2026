import frappe
from frappe.utils import flt

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.utils.sync import sync_document


class TestEarnedOnPayment(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		self.person = h.make_sales_person()
		self.team = [(self.person, 100)]

	def rows(self, *docs):
		return [r for doc in docs for r in h.ledger(doc.name)]

	def total(self, *docs):
		return flt(sum(r.commission_amount for r in self.rows(*docs)), 2)

	def test_vat_is_not_commissioned(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 2500)], team=self.team, taxes=h.vat_rows())
		self.assertEqual(flt(invoice.grand_total), 2875)
		self.assertEqual(flt(invoice.items[0].commission_amount), 250)
		p.pay(invoice, 2875)
		self.assertEqual(self.total(invoice), 250)

	def test_foreign_invoice_paid_at_two_rates(self):
		item = h.make_item(h.make_group(), percent=5)
		invoice = h.make_invoice(
			h.make_customer("USD"), [(item, 1, 10000)], team=self.team, currency="USD", rate=17
		)
		p.pay(invoice, 4000, rate=17)
		p.pay(invoice, 6000, rate=18)
		self.assertEqual([flt(r.commission_amount, 2) for r in self.rows(invoice)], [3400, 5400])
		self.assertEqual(self.total(invoice), 8800)

	def test_advance_moves_from_order_to_invoices(self):
		hosting = h.make_item(h.make_group(), percent=10)
		training = h.make_item(h.make_group(), percent=5)
		customer = h.make_customer()
		order = h.make_order(customer, [(hosting, 1, 5000), (training, 1, 5000)], team=self.team)
		self.assertEqual(flt(order.total_commission_amount), 750)
		p.pay(order, 5000)
		self.assertEqual(self.total(order), 375)

		first = p.invoice_from_order(order, {hosting: 1}, submit=False)
		first.set_advances()
		first.save()
		self.assertEqual(flt(first.advances[0].allocated_amount), 5000)
		first.submit()
		self.assertEqual(self.total(order), 0)
		self.assertEqual(self.total(first), 500)
		self.assertEqual({r.entry_type for r in self.rows(order, first)}, {"Earned", "Advance Adjustment"})

		second = p.invoice_from_order(order, {training: 1})
		p.pay(second, 5000)
		self.assertEqual(self.total(second), 250)
		self.assertEqual(self.total(order, first, second), 750)

	def assert_cancelled_history(self, doc, entry_types):
		"""Like GL Entries: the original row and its reverse stay, both cancelled, adding up to 0."""
		history = h.ledger(doc.name, is_cancelled=1)
		self.assertEqual([r.entry_type for r in history], entry_types)
		self.assertEqual(flt(sum(r.commission_amount for r in history), 2), 0)
		self.assertEqual(history[1].reverses_entry, history[0].name)

	def test_payment_cancelled_reverses(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		payment = p.pay(invoice, 1000)
		self.assertEqual(self.total(invoice), 100)
		payment.cancel()
		self.assertEqual(self.rows(invoice), [])
		self.assert_cancelled_history(invoice, ["Earned", "Reversal"])

	def test_unreconciled_payment_reverses(self):
		from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import (
			create_unreconcile_doc_for_selection,
		)

		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		payment = p.pay(invoice, 1000)
		self.assertEqual(self.total(invoice), 100)
		selection = {
			"company": h.COMPANY,
			"voucher_type": "Payment Entry",
			"voucher_no": payment.name,
			"against_voucher_type": "Sales Invoice",
			"against_voucher_no": invoice.name,
		}
		create_unreconcile_doc_for_selection(frappe.as_json([selection]))
		self.assertEqual(self.total(invoice), 0)
		self.assertEqual(self.rows(invoice)[-1].entry_type, "Reversal")
		self.assertEqual(h.ledger(invoice.name, is_cancelled=1), [])

	def test_cancelled_order_with_advance_reverses(self):
		item = h.make_item(h.make_group(), percent=10)
		order = h.make_order(h.make_customer(), [(item, 1, 1000)], team=self.team)
		payment = p.pay(order, 1000)
		self.assertEqual(self.total(order), 100)
		payment.cancel()
		self.assertEqual(self.rows(order), [])
		self.assert_cancelled_history(order, ["Earned", "Reversal"])
		order.reload().cancel()
		self.assertEqual(self.rows(order), [])

	def test_sync_twice_posts_nothing_new(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 2, 500)], team=self.team)
		p.pay(invoice, 400)
		before = len(self.rows(invoice))
		sync_document("Sales Invoice", invoice.name)
		sync_document("Sales Invoice", invoice.name)
		self.assertEqual(len(self.rows(invoice)), before)
		self.assertEqual(self.total(invoice), 40)

	def test_no_sales_team_posts_no_ledger_rows(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)])
		p.pay(invoice, 1000)
		# no salesperson, no estimate and no ledger rows
		self.assertEqual(flt(invoice.items[0].commission_amount), 0)
		self.assertEqual(self.rows(invoice), [])

	def test_foreign_advance_is_posted_at_the_payment_rate(self):
		item = h.make_item(h.make_group(), percent=5)
		order = h.make_order(
			h.make_customer("USD"), [(item, 1, 10000)], team=self.team, currency="USD", rate=17
		)
		p.pay(order, 1000, rate=17)
		row = self.rows(order)[0]
		self.assertEqual((flt(row.exchange_rate), flt(row.commission_amount, 2)), (17, 850))
		invoice = p.invoice_from_order(order, submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		moved = [r for r in self.rows(invoice) if r.entry_type == "Advance Adjustment"]
		self.assertEqual([flt(r.exchange_rate) for r in moved], [17])
		self.assertEqual(flt(moved[0].commission_amount, 2), 850)
		self.assertEqual(self.total(order), 0)

	def test_journal_entry_is_not_a_payment(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		p.journal_payment(invoice, 1000)
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount"), 0)
		self.assertEqual(self.rows(invoice), [])

	def test_one_payment_for_two_invoices_earns_each_its_share(self):
		h.set_settings(factors=(1.0, 1.0))
		item = h.make_item(h.make_group(), percent=10)
		customer = h.make_customer()
		first = h.make_invoice(customer, [(item, 1, 1000)], team=self.team)
		second = h.make_invoice(customer, [(item, 1, 2000)], team=self.team)
		p.pay_many([(first, 1000), (second, 2000)])
		self.assertEqual((self.total(first), self.total(second)), (100, 200))

	def test_payment_that_is_never_matched_earns_nothing(self):
		item = h.make_item(h.make_group(), percent=10)
		customer = h.make_customer()
		invoice = h.make_invoice(customer, [(item, 1, 1000)], team=self.team)
		p.unallocated_payment(customer, 1000)
		self.assertEqual(self.rows(invoice), [])

	def test_changed_payment_rate_posts_a_correcting_row(self):
		item = h.make_item(h.make_group(), percent=5)
		invoice = h.make_invoice(
			h.make_customer("USD"), [(item, 1, 10000)], team=self.team, currency="USD", rate=17
		)
		payment = p.pay(invoice, 10000, rate=17)
		self.assertEqual(self.total(invoice), 8500)
		# the payment is restated at another rate in the Payment Ledger
		frappe.db.sql(
			"update `tabPayment Ledger Entry` set amount = amount * 18 / 17 where voucher_no = %s",
			payment.name,
		)
		sync_document("Sales Invoice", invoice.name)
		self.assertEqual(self.total(invoice), 9000)
		rows = self.rows(invoice)
		self.assertEqual([flt(r.exchange_rate) for r in rows], [17, 18])
		self.assertEqual((rows[1].earned_amount, rows[1].entry_type), (0, "Earned"))
		sync_document("Sales Invoice", invoice.name)
		self.assertEqual(len(self.rows(invoice)), 2)
