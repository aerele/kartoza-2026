from frappe.utils import add_days, flt

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p


class TestEscalator(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.group = h.make_group()
		self.customer = h.make_customer()
		self.day = 0

	def next_date(self):
		"""Increasing dates, so the order of the payments never depends on the creation time."""
		self.day += 1
		return add_days(h.date_in(0, "04-01"), self.day)

	def paid(self, amount, date=None, percent=10):
		date = date or self.next_date()
		item = h.make_item(self.group, percent=percent)
		invoice = h.make_invoice(self.customer, [(item, 1, amount)], team=[(self.person, 100)], date=date)
		return p.pay(invoice, amount, date=date), invoice

	def last(self, invoice):
		return h.ledger(invoice.name)[-1]

	def target(self, amount, date=None):
		h.set_target(self.person, h.fiscal_year_of(date or h.date_in(0, "04-01")), amount)

	def shape(self, invoice):
		"""(multiplier, running total before, after, commission) of each live Earned row."""
		return [
			(
				flt(r.multiplier),
				flt(r.running_total_before),
				flt(r.running_total_after),
				flt(r.commission_amount),
			)
			for r in h.ledger(invoice.name)
			if r.entry_type == "Earned"
		]

	def test_only_the_part_above_the_target_earns_the_multiplier(self):
		self.target(1500)
		_, first = self.paid(1000)
		_, second = self.paid(1000)
		_, third = self.paid(1000)
		self.assertEqual(self.shape(first), [(1, 0, 1000, 100)])
		self.assertEqual(self.shape(second), [(1, 1000, 1500, 50), (1.5, 1500, 2000, 75)])
		self.assertEqual(self.shape(third), [(1.5, 2000, 3000, 150)])

	def test_sales_example_with_a_crossing_sale(self):
		self.target(400000)
		invoices = [self.paid(amount, percent=5)[1] for amount in (150000, 350000, 200000)]
		self.assertEqual(self.shape(invoices[0]), [(1, 0, 150000, 7500)])
		self.assertEqual(self.shape(invoices[1]), [(1, 150000, 400000, 12500), (1.5, 400000, 500000, 7500)])
		self.assertEqual(self.shape(invoices[2]), [(1.5, 500000, 700000, 15000)])
		total = sum(r.commission_amount for i in invoices for r in h.ledger(i.name))
		self.assertEqual(flt(total, 2), 42500)

	def test_credit_note_on_a_crossing_invoice_takes_back_from_both_parts(self):
		self.target(1500)
		self.paid(1000)
		_, second = self.paid(1000)
		note_date = add_days(self.next_date(), 1)
		p.make_credit_note(second, date=note_date)
		rows = [r for r in h.ledger(second.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual(
			[(flt(r.multiplier), flt(r.commission_amount), flt(r.earned_amount)) for r in rows],
			[(1, -50, -50), (1.5, -75, -50)],
		)
		self.assertEqual(flt(sum(r.commission_amount for r in h.ledger(second.name))), 0)
		self.assertEqual(
			flt(sum(r.running_total_after - r.running_total_before for r in h.ledger(second.name))), 0
		)

	def test_cancelled_crossing_payment_leaves_no_rows(self):
		self.target(1500)
		self.paid(1000)
		payment, second = self.paid(1000)
		self.assertEqual(len(self.shape(second)), 2)
		payment.cancel()
		self.assertEqual(h.ledger(second.name), [])
		_, third = self.paid(1000)
		self.assertEqual(self.shape(third), [(1, 1000, 1500, 50), (1.5, 1500, 2000, 75)])

	def test_second_partial_payment_crosses(self):
		self.target(1500)
		self.paid(1000)
		date = self.next_date()
		item = h.make_item(self.group, percent=10)
		invoice = h.make_invoice(self.customer, [(item, 1, 1000)], team=[(self.person, 100)], date=date)
		p.pay(invoice, 300, date=date)
		p.pay(invoice, 700, date=add_days(date, 1))
		self.assertEqual(
			self.shape(invoice), [(1, 1000, 1300, 30), (1, 1300, 1500, 20), (1.5, 1500, 2000, 75)]
		)

	def test_reaching_the_target_exactly_stays_at_one(self):
		self.target(1000)
		_, first = self.paid(1000)
		_, second = self.paid(1000)
		self.assertEqual(self.shape(first), [(1, 0, 1000, 100)])
		self.assertEqual(self.shape(second), [(1.5, 1000, 2000, 150)])

	def test_moved_advance_keeps_the_parts_of_the_order(self):
		self.target(1500)
		self.paid(1000)
		date = self.next_date()
		item = h.make_item(self.group, percent=10)
		order = h.make_order(self.customer, [(item, 1, 1000)], team=[(self.person, 100)], date=date)
		p.pay(order, 1000, date=date)
		invoice = p.invoice_from_order(order, date=add_days(date, 1), submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		moved = [r for r in h.ledger(invoice.name) if r.entry_type == "Advance Adjustment"]
		self.assertEqual([(flt(r.multiplier), flt(r.commission_amount)) for r in moved], [(1, 50), (1.5, 75)])

	def test_new_fiscal_year_starts_again(self):
		last_year, this_year = h.date_in(1, "06-10"), h.date_in(0, "06-10")
		self.target(1000, last_year)
		self.target(1000, this_year)
		self.paid(1000, date=last_year)
		_, second = self.paid(1000, date=add_days(last_year, 1))
		self.assertEqual(flt(self.last(second).multiplier), 1.5)
		_, later = self.paid(1000, date=this_year)
		row = self.last(later)
		self.assertEqual(flt(row.multiplier), 1)
		self.assertNotEqual(row.fiscal_year, h.fiscal_year_of(last_year))
		self.assertEqual(row.fiscal_year, h.fiscal_year_of(this_year))

	def test_reversal_below_target_returns_to_one(self):
		self.target(1500)
		first, _ = self.paid(1000)
		second, _ = self.paid(1000)
		self.paid(1000)
		first.cancel()
		second.cancel()
		_, fourth = self.paid(1000)
		self.assertEqual(self.shape(fourth), [(1, 1000, 1500, 50), (1.5, 1500, 2000, 75)])

	def test_sheet_arithmetic(self):
		h.set_settings(factors=(1.0, 0.5))
		self.target(1)
		self.paid(1)
		# the earlier sale in the group makes this one a Renewal: 10% x 0.5
		item = h.make_item(self.group, percent=10)
		invoice = h.make_invoice(self.customer, [(item, 1, 113732.17)], team=[(self.person, 100)])
		p.pay(invoice, invoice.outstanding_amount)
		row = self.last(invoice)
		self.assertEqual(flt(row.multiplier), 1.5)
		self.assertEqual(flt(row.commission_amount, 2), 8529.91)

	def test_cancelled_payment_leaves_the_running_total(self):
		self.target(1500)
		first, _ = self.paid(1000)
		self.paid(1000)
		first.cancel()
		_, third = self.paid(1000)
		self.assertEqual(self.shape(third), [(1, 1000, 1500, 50), (1.5, 1500, 2000, 75)])

	def test_lines_paid_together_follow_each_other(self):
		self.target(100000)
		item_a, item_b = h.make_item(self.group, percent=10), h.make_item(self.group, percent=10)
		invoice = h.make_invoice(
			self.customer,
			[(item_a, 1, 500), (item_b, 1, 500)],
			team=[(self.person, 100)],
			date=self.next_date(),
		)
		p.pay(invoice, 1000, date=self.next_date())
		rows = h.ledger(invoice.name)
		self.assertEqual(
			[(flt(r.running_total_before), flt(r.running_total_after)) for r in rows], [(0, 500), (500, 1000)]
		)

	def test_no_target_row_means_no_escalator(self):
		first, second = self.paid(1000)[1], self.paid(1000)[1]
		self.assertEqual([flt(self.last(i).multiplier) for i in (first, second)], [1, 1])
		self.assertEqual(flt(self.last(second).running_total_after), 2000)

	def test_item_group_target_is_not_the_annual_target(self):
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-01")), 1, item_group=self.group)
		self.paid(1000)
		_, second = self.paid(1000)
		self.assertEqual(flt(self.last(second).multiplier), 1)

	def test_each_fiscal_year_uses_its_own_target(self):
		last_year, this_year = h.date_in(1, "06-10"), h.date_in(0, "06-10")
		self.target(1000, last_year)
		self.target(5000, this_year)
		self.paid(1000, date=last_year)
		_, second = self.paid(1000, date=add_days(last_year, 1))
		self.assertEqual(flt(self.last(second).multiplier), 1.5)
		self.paid(1000, date=this_year)
		_, next_one = self.paid(1000, date=add_days(this_year, 1))
		row = self.last(next_one)
		self.assertEqual((flt(row.multiplier), flt(row.running_total_before)), (1, 1000))
