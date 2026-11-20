import frappe
from frappe.utils import add_days, flt, fmt_money

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.utils.line_fields import line_multiplier


class TestTargetSales(h.CommissionTestCase):
	"""The annual target counts the person's submitted orders and invoices, not the money received."""

	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.team = [(self.person, 100)]
		self.group = h.make_group()
		self.customer = h.make_customer()
		self.item = h.make_item(self.group, percent=10)
		self.day = 0

	def next_date(self):
		self.day += 1
		return add_days(h.date_in(0, "04-01"), self.day)

	def target(self, amount):
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-01")), amount)

	def order(self, amount, date=None, submit=True):
		return h.make_order(
			self.customer,
			[(self.item, 1, amount)],
			team=self.team,
			date=date or self.next_date(),
			submit=submit,
		)

	def multipliers(self, rows):
		return [(flt(r.multiplier), flt(r.commission_amount)) for r in rows]

	def test_commission_is_worked_out_once_a_salesperson_is_added(self):
		self.target(1000)
		self.order(1000)
		draft = h.make_order(self.customer, [(self.item, 1, 500)], date=self.next_date(), submit=False)
		line = draft.items[0]
		self.assertEqual((line.commission_classification, flt(line.commission_amount)), ("", 0))
		self.assertEqual((line.target_multiplier, flt(draft.total_commission_amount)), ("", 0))
		draft.append("sales_team", {"sales_person": self.person, "allocated_percentage": 100})
		draft.save()
		line = draft.items[0]
		self.assertEqual((line.target_multiplier, flt(line.commission_amount)), ("1.5", 75))
		self.assertEqual(flt(draft.sales_team[0].incentives), 75)

	def test_unpaid_submitted_order_counts_towards_the_target(self):
		self.target(1000)
		self.order(1000)
		second = self.order(500)
		self.assertEqual(line_multiplier(second.items[0]), 1.5)
		self.assertEqual(flt(second.items[0].commission_amount), 75)

	def test_deleted_draft_does_not_count(self):
		self.target(1000)
		draft = self.order(5000, submit=False)
		frappe.delete_doc("Sales Order", draft.name, ignore_permissions=True)
		order = self.order(500)
		self.assertEqual(line_multiplier(order.items[0]), 1)
		self.assertEqual(order.items[0].target_multiplier, "1")

	def test_split_is_frozen_when_the_order_is_submitted(self):
		self.target(1000)
		self.order(800)
		order = self.order(400)
		self.assertEqual(order.items[0].target_multiplier, f"1 ({money(200)}), 1.5 ({money(200)})")
		person = frappe.get_doc("Sales Person", self.person)
		person.targets[0].target_amount = 5000
		person.save(ignore_permissions=True)
		p.pay(order, 400, date=order.transaction_date)
		# the target changed after the order was submitted: its payment keeps the split it had then
		self.assertEqual(self.multipliers(h.ledger(order.name)), [(1, 20), (1.5, 30)])
		later = self.order(400)
		self.assertEqual(later.items[0].target_multiplier, "1")

	def test_back_dated_order_does_not_change_a_submitted_order(self):
		self.target(1000)
		first = self.order(800, date=add_days(h.date_in(0, "04-01"), 10))
		back_dated = self.order(800, date=add_days(h.date_in(0, "04-01"), 5))
		self.assertEqual(line_multiplier(back_dated.items[0]), 1)
		p.pay(first, 800, date=first.transaction_date)
		self.assertEqual(self.multipliers(h.ledger(first.name)), [(1, 80)])
		after_both = self.order(500, date=add_days(h.date_in(0, "04-01"), 11))
		self.assertEqual(line_multiplier(after_both.items[0]), 1.5)

	def test_invoice_made_from_an_order_is_not_counted_twice(self):
		self.target(1000)
		order = self.order(800)
		p.invoice_from_order(order, date=self.next_date())
		later = self.order(300)
		# 800 counted once: 200 left within the target, 100 above it
		self.assertEqual(later.items[0].target_multiplier, f"1 ({money(200)}), 1.5 ({money(100)})")

	def test_invoice_lines_from_an_order_follow_the_order_split(self):
		self.target(1000)
		self.order(800)
		order = self.order(400)
		self.order(2000)
		invoice = p.invoice_from_order(order, date=self.next_date())
		self.assertEqual(invoice.items[0].target_multiplier, order.items[0].target_multiplier)
		self.assertEqual(flt(invoice.items[0].commission_amount), flt(order.items[0].commission_amount))
		p.pay(invoice, 400, date=invoice.posting_date)
		self.assertEqual(self.multipliers(h.ledger(invoice.name)), [(1, 20), (1.5, 30)])

	def test_invoice_without_an_order_counts_only_its_own_lines(self):
		self.target(1000)
		order = self.order(600)
		invoice = p.invoice_from_order(order, date=self.next_date(), submit=False)
		invoice.append("items", {**h.item_rows([(self.item, 1, 300)])[0]})
		invoice.save()
		invoice.submit()
		later = self.order(300)
		# 600 from the order plus the invoice's own 300 line: 100 left within the target
		self.assertEqual(later.items[0].target_multiplier, f"1 ({money(100)}), 1.5 ({money(200)})")

	def test_credit_note_counts_only_when_the_setting_is_ticked(self):
		self.target(1000)
		invoice = h.make_invoice(self.customer, [(self.item, 2, 600)], team=self.team, date=self.next_date())
		p.make_credit_note(invoice, {0: 1}, date=self.next_date())
		h.set_settings(include_commission_for_credit_note=1)
		self.assertEqual(line_multiplier(self.order(200, submit=False).items[0]), 1)
		h.set_settings(include_commission_for_credit_note=0)
		self.assertEqual(line_multiplier(self.order(200, submit=False).items[0]), 1.5)


def money(amount):
	return fmt_money(amount, currency="ZAR")
