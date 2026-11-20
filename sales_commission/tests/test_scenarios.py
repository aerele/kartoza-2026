"""Scenario sweep: orders, invoices, payments, credit notes, escalator and ledger, with real documents."""

import json

import frappe
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import add_days, flt

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.tests import scenario_helpers as s
from sales_commission.utils.sync import sync_document


class ScenarioCase(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.team = [(self.person, 100)]
		self.group = h.make_group()
		self.customer = h.make_customer()

	def item(self, percent=10):
		return h.make_item(self.group, percent=percent)

	def total(self, doc, person=None):
		return s.person_total(doc.name, person)


class TestOrderInvoiceScenarios(ScenarioCase):
	def test_order_only_stores_commission_and_earns_nothing(self):
		order = h.make_order(self.customer, [(self.item(), 1, 1000)], team=self.team)
		self.assertEqual(flt(order.total_commission_amount), 100)
		self.assertEqual(h.ledger(order.name), [])

	def test_order_to_full_invoice_earns_on_the_invoice_payment(self):
		order = h.make_order(self.customer, [(self.item(), 1, 1000)], team=self.team)
		invoice = p.invoice_from_order(order)
		self.assertEqual(invoice.items[0].commission_classification, order.items[0].commission_classification)
		p.pay(invoice, 1000)
		self.assertEqual((self.total(invoice), self.total(order)), (100, 0))

	def test_order_to_partial_invoices_each_earns_its_part(self):
		item = self.item()
		order = h.make_order(self.customer, [(item, 10, 100)], team=self.team)
		first = p.invoice_from_order(order, {item: 4})
		second = p.invoice_from_order(order, {item: 6})
		p.pay(first, 400)
		p.pay(second, 600)
		self.assertEqual((self.total(first), self.total(second), self.total(order)), (40, 60, 0))

	def update_items(self, order, changes):
		update_child_qty_rate("Sales Order", json.dumps(changes), order.name)
		return order.reload()

	def test_update_items_qty_increase_after_an_advance(self):
		item = self.item()
		order = h.make_order(self.customer, [(item, 1, 1000)], team=self.team)
		p.pay(order, 1000)
		self.assertEqual(self.total(order), 100)
		order = self.update_items(
			order, [{"docname": order.items[0].name, "item_code": item, "qty": 2, "rate": 1000}]
		)
		self.assertEqual(flt(order.total_commission_amount), 200)
		# 1000 of 2000 paid, so half of the new commission
		self.assertEqual(self.total(order), 100)
		p.pay(order, 1000)
		self.assertEqual(self.total(order), 200)

	def test_update_items_new_line_after_an_advance(self):
		first, second = self.item(), self.item()
		order = h.make_order(self.customer, [(first, 1, 1000)], team=self.team)
		p.pay(order, 1000)
		changes = [
			{"docname": order.items[0].name, "item_code": first, "qty": 1, "rate": 1000},
			{"item_code": second, "qty": 1, "rate": 1000, "warehouse": h.WAREHOUSE},
		]
		order = self.update_items(order, changes)
		# 1000 of 2000 paid: half of each line's 100
		self.assertEqual(self.total(order), 100)
		p.pay(order, 1000)
		self.assertEqual(self.total(order), 200)

	def test_update_items_removed_line_that_already_earned(self):
		first, second = self.item(), self.item()
		order = h.make_order(self.customer, [(first, 1, 1000), (second, 1, 1000)], team=self.team)
		p.pay(order, 1000)
		self.assertEqual(self.total(order), 100)
		removed = order.items[1].name
		changes = [{"docname": order.items[0].name, "item_code": first, "qty": 1, "rate": 1000}]
		order = self.update_items(order, changes)
		self.assertEqual(len(order.items), 1)
		rows = h.ledger(order.name)
		# the removed line earns nothing any more, the kept one is fully paid now
		self.assertEqual(flt(sum(r.commission_amount for r in rows if r.reference_row == removed), 2), 0)
		self.assertEqual(self.total(order), 100)

	def test_amend_after_cancel_earns_once(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		payment = p.pay(invoice, 1000)
		payment.cancel()
		invoice.reload().cancel()
		self.assertEqual(self.total(invoice), 0)
		amended = frappe.copy_doc(frappe.get_doc("Sales Invoice", invoice.name))
		amended.docstatus = 0
		amended.amended_from = invoice.name
		amended.insert(ignore_permissions=True)
		amended.reload().submit()
		p.pay(amended, 1000)
		self.assertEqual((self.total(invoice), self.total(amended)), (0, 100))

	def test_duplicate_of_a_paid_invoice_is_a_renewal_and_earns_on_its_own_payment(self):
		h.set_settings(factors=(1.0, 0.5))
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		p.pay(invoice, 1000)
		copy = frappe.copy_doc(frappe.get_doc("Sales Invoice", invoice.name))
		copy.insert(ignore_permissions=True)
		copy.reload().submit()
		self.assertEqual(copy.items[0].commission_classification, "Renewal")
		self.assertEqual(self.total(copy), 0)
		p.pay(copy, 1000)
		self.assertEqual((self.total(invoice), self.total(copy)), (100, 50))

	def test_salesperson_changed_before_payment(self):
		other = h.make_sales_person()
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		s.change_team(invoice, [(other, 100)])
		p.pay(invoice, 1000)
		self.assertEqual((self.total(invoice, self.person), self.total(invoice, other)), (0, 100))

	def test_salesperson_changed_after_payment_moves_the_commission(self):
		other = h.make_sales_person()
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		p.pay(invoice, 1000)
		self.assertEqual(self.total(invoice, self.person), 100)
		s.change_team(invoice, [(other, 100)])
		self.assertEqual((self.total(invoice, self.person), self.total(invoice, other)), (0, 100))

	def test_team_split_changed_after_payment_follows_the_new_split(self):
		other = h.make_sales_person()
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		p.pay(invoice, 1000)
		s.change_team(invoice, [(self.person, 50), (other, 50)])
		self.assertEqual((self.total(invoice, self.person), self.total(invoice, other)), (50, 50))

	def test_old_document_is_unchanged_by_a_team_edit(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		for row in invoice.items:
			frappe.db.set_value(
				row.doctype,
				row.name,
				{
					"commission_classification": "",
					"commission_amount": 0,
					"base_commission_amount": 0,
					"commission_percent": 0,
					"commission_factor": 0,
				},
			)
		frappe.db.set_value(
			"Sales Invoice", invoice.name, {"total_commission_amount": 0, "base_total_commission_amount": 0}
		)
		s.change_team(invoice, [(h.make_sales_person(), 100)])
		p.pay(invoice, 1000)
		self.assertEqual(h.ledger(invoice.name), [])


class TestPaymentScenarios(ScenarioCase):
	def test_partial_payments_in_two_months_post_in_their_months(self):
		invoice = h.make_invoice(
			self.customer, [(self.item(), 1, 1000)], team=self.team, date=h.date_in(0, "04-01")
		)
		p.pay(invoice, 500, date=h.date_in(0, "04-20"))
		p.pay(invoice, 500, date=h.date_in(0, "05-05"))
		rows = h.ledger(invoice.name)
		self.assertEqual(
			[(str(r.posting_date), flt(r.commission_amount)) for r in rows],
			[(h.date_in(0, "04-20"), 50), (h.date_in(0, "05-05"), 50)],
		)

	def test_overpayment_earns_only_the_invoice_commission(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		s.overpay(invoice, 1000, 1500)
		rows = h.ledger(invoice.name)
		self.assertEqual((len(rows), self.total(invoice)), (1, 100))
		self.assertEqual(flt(rows[0].running_total_after - rows[0].running_total_before), 1000)

	def test_cancelling_one_of_two_partial_payments_keeps_the_other(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		p.pay(invoice, 500)
		second = p.pay(invoice, 500)
		second.cancel()
		self.assertEqual(self.total(invoice), 50)

	def test_paying_again_after_a_cancelled_payment_earns_once(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		p.pay(invoice, 1000).cancel()
		p.pay(invoice.reload(), 1000)
		self.assertEqual((self.total(invoice), len(h.ledger(invoice.name))), (100, 1))

	def test_payment_before_the_invoice_date_posts_on_the_payment_date(self):
		invoice = h.make_invoice(
			self.customer, [(self.item(), 1, 1000)], team=self.team, date=h.date_in(0, "05-10")
		)
		p.pay(invoice, 1000, date=h.date_in(0, "05-12"))
		self.assertEqual(str(h.ledger(invoice.name)[0].posting_date), h.date_in(0, "05-12"))


class TestCreditNoteScenarios(ScenarioCase):
	def test_full_credit_note_after_full_payment_takes_everything_back(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 1, 1000)], team=self.team)
		p.pay(invoice, 1000)
		p.make_credit_note(invoice)
		self.assertEqual(self.total(invoice), 0)
		deduction = [r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual([flt(r.commission_amount) for r in deduction], [-100])

	def test_credit_note_in_the_next_fiscal_year_posts_in_that_year(self):
		invoice = h.make_invoice(
			self.customer, [(self.item(), 1, 1000)], team=self.team, date=h.date_in(0, "06-10")
		)
		p.pay(invoice, 1000, date=h.date_in(0, "06-10"))
		p.make_credit_note(invoice, date=h.date_in(-1, "06-10"))
		rows = {r.entry_type: r for r in h.ledger(invoice.name)}
		self.assertEqual(rows["Credit Note Deduction"].fiscal_year, h.fiscal_year_of(h.date_in(-1, "06-10")))
		self.assertEqual(rows["Earned"].fiscal_year, h.fiscal_year_of(h.date_in(0, "06-10")))
		self.assertEqual(self.total(invoice), 0)

	def reconciled_part_paid(self):
		invoice = h.make_invoice(self.customer, [(self.item(5), 1, 2760)], team=self.team)
		p.pay(invoice, 1380)
		note = h.make_invoice(self.customer, [(self.item(5), -1, 2760)], team=self.team, is_return=1)
		p.reconcile(self.customer)
		return invoice, note

	def test_cancelling_the_credit_note_journal_restores_invoice_and_note(self):
		invoice, note = self.reconciled_part_paid()
		# the 1380 paid covers the half of the invoice left after the credit, so the invoice keeps all 69
		self.assertEqual((self.total(invoice), self.total(note)), (69, -69))
		journals = frappe.get_all(
			"Journal Entry Account",
			filters={
				"reference_type": "Sales Invoice",
				"reference_name": invoice.name,
				"parenttype": "Journal Entry",
			},
			pluck="parent",
		)
		journal = frappe.get_doc("Journal Entry", journals[0])
		self.assertEqual(journal.voucher_type, "Credit Note")
		journal.cancel()
		self.assertEqual((self.total(invoice), self.total(note)), (69, -138))

	def test_credit_note_cancelled_then_invoice_paid_later_is_not_deducted(self):
		invoice = h.make_invoice(self.customer, [(self.item(), 10, 100)], team=self.team)
		note = p.make_credit_note(invoice, {0: 4})
		note.cancel()
		p.pay(invoice, 1000)
		self.assertEqual(self.total(invoice), 100)


class TestEscalatorScenarios(ScenarioCase):
	def setUp(self):
		super().setUp()
		self.day = 0

	def next_date(self):
		self.day += 1
		return add_days(h.date_in(0, "04-01"), self.day)

	def paid(self, person, amount):
		date = self.next_date()
		invoice = h.make_invoice(self.customer, [(self.item(), 1, amount)], team=[(person, 100)], date=date)
		p.pay(invoice, amount, date=date)
		return invoice

	def last(self, invoice):
		return h.ledger(invoice.name)[-1]

	def test_target_is_per_salesperson(self):
		other = h.make_sales_person()
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-01")), 1000)
		mine = [self.paid(self.person, 1000), self.paid(self.person, 1000)]
		theirs = [self.paid(other, 1000), self.paid(other, 1000)]
		self.assertEqual([flt(self.last(i).multiplier) for i in mine], [1, 1.5])
		self.assertEqual([flt(self.last(i).multiplier) for i in theirs], [1, 1])

	def test_credit_note_after_crossing_takes_back_the_multiplied_amount(self):
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-01")), 1000)
		self.paid(self.person, 1000)
		second = self.paid(self.person, 1000)
		self.assertEqual(self.total(second), 150)
		p.make_credit_note(second, date=self.next_date())
		deduction = [r for r in h.ledger(second.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual([(flt(r.multiplier), flt(r.commission_amount)) for r in deduction], [(1.5, -150)])
		self.assertEqual(self.total(second), 0)
		self.assertEqual(
			flt(sum(r.running_total_after - r.running_total_before for r in h.ledger(second.name))), 0
		)


class TestSettingsChangedMidYear(ScenarioCase):
	def setUp(self):
		super().setUp()
		self.day = 0

	def next_date(self):
		self.day += 1
		return add_days(h.date_in(0, "04-01"), self.day)

	def invoice(self, amount=1000):
		return h.make_invoice(
			self.customer, [(self.item(), 1, amount)], team=self.team, date=self.next_date()
		)

	def paid(self, invoice):
		p.pay(invoice, invoice.grand_total, date=self.next_date())
		return invoice

	def figures(self, invoice):
		return [
			(r.entry_type, flt(r.commission_factor), flt(r.multiplier), flt(r.commission_amount, 2))
			for r in h.ledger(invoice.name)
		]

	def test_rows_already_posted_keep_their_figures_and_new_payments_use_the_new_settings(self):
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-01")), 1000)
		first, second = self.paid(self.invoice()), self.paid(self.invoice())
		waiting = self.invoice()
		self.assertEqual(self.figures(first), [("Earned", 1, 1, 100)])
		self.assertEqual(self.figures(second), [("Earned", 1, 1.5, 150)])
		posted = {i.name: [r.name for r in h.ledger(i.name)] for i in (first, second)}

		h.set_settings(factors=(0.5, 0.5), escalator_multiplier=2)
		for invoice in (first, second):
			sync_document("Sales Invoice", invoice.name)
			self.assertEqual([r.name for r in h.ledger(invoice.name)], posted[invoice.name])
		self.assertEqual(self.figures(first), [("Earned", 1, 1, 100)])
		self.assertEqual(self.figures(second), [("Earned", 1, 1.5, 150)])

		# submitted before the change but paid after it: its own factor of 1 and the split frozen on submit
		self.assertEqual(self.figures(self.paid(waiting)), [("Earned", 1, 1.5, 150)])
		# made after the change: factor 0.5 and multiplier 2
		self.assertEqual(self.figures(self.paid(self.invoice())), [("Earned", 0.5, 2, 100)])
		# a credit note of a row posted before the change takes back what that row earned
		p.make_credit_note(second, date=self.next_date())
		deduction = [r for r in h.ledger(second.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual([(flt(r.multiplier), flt(r.commission_amount)) for r in deduction], [(1.5, -150)])


class TestLedgerScenarios(ScenarioCase):
	def test_sync_twice_after_payment_and_credit_note_posts_nothing_new(self):
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-01")), 1)
		invoice = h.make_invoice(
			self.customer, [(self.item(), 10, 100)], team=self.team, date=h.date_in(0, "04-01")
		)
		p.pay(invoice, 1000, date=h.date_in(0, "04-02"))
		p.make_credit_note(invoice, {0: 4}, date=h.date_in(0, "04-03"))
		before = [(r.name) for r in h.ledger(invoice.name)]
		sync_document("Sales Invoice", invoice.name)
		sync_document("Sales Invoice", invoice.name)
		self.assertEqual([r.name for r in h.ledger(invoice.name)], before)

	def test_ledger_list_sorts_newest_first(self):
		meta = frappe.get_meta("Commission Ledger")
		self.assertEqual((meta.sort_field, meta.sort_order), ("creation", "DESC"))
