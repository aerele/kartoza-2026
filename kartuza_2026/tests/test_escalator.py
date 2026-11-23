import frappe
from frappe.utils import add_days, flt, fmt_money

from kartuza_2026.tests import helpers as h
from kartuza_2026.tests import payment_helpers as p
from kartuza_2026.tests import payroll_helpers as pr
from kartuza_2026.utils.escalator import year_to_date_sales


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

	def paid(self, amount, date=None, percent=10, qty=1):
		date = date or self.next_date()
		item = h.make_item(self.group, percent=percent)
		invoice = h.make_invoice(
			self.customer, [(item, qty, amount / qty)], team=[(self.person, 100)], date=date
		)
		return p.pay(invoice, amount, date=date), invoice

	def last(self, invoice):
		return h.ledger(invoice.name)[-1]

	def target(self, amount, date=None):
		h.set_target(self.person, h.fiscal_year_of(date or h.date_in(0, "04-01")), amount)

	def year_to_date(self):
		return flt(year_to_date_sales(self.person, h.fiscal_year_of(h.date_in(0, "04-01"))))

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
		self.assertEqual({flt(r.commission_factor) for r in h.ledger(invoices[1].name)}, {1})
		eligible = [flt(r.eligible_amount) for r in h.ledger(invoices[1].name)]
		self.assertEqual(sorted(eligible), [100000, 250000])

	def test_full_credit_note_on_a_crossing_invoice_takes_back_both_parts_top_first(self):
		self.target(1500)
		self.paid(1000)
		_, second = self.paid(1000)
		note_date = add_days(self.next_date(), 1)
		p.make_credit_note(second, date=note_date)
		rows = [r for r in h.ledger(second.name) if r.entry_type == "Credit Note Deduction"]
		self.assertEqual(
			[(flt(r.multiplier), flt(r.commission_amount), flt(r.earned_amount)) for r in rows],
			[(1.5, -75, -50), (1, -50, -50)],
		)
		self.assertEqual([flt(r.eligible_amount) for r in rows], [-500, -500])
		self.assertEqual(flt(sum(r.commission_amount for r in h.ledger(second.name))), 0)
		self.assertEqual(
			flt(sum(r.running_total_after - r.running_total_before for r in h.ledger(second.name))), 0
		)

	def test_cancelled_payment_leaves_no_rows_and_its_invoice_still_counts(self):
		self.target(1500)
		self.paid(1000)
		payment, second = self.paid(1000)
		self.assertEqual(len(self.shape(second)), 2)
		payment.cancel()
		self.assertEqual(h.ledger(second.name), [])
		# the second invoice is still submitted, so the third one starts at 2000 and is all above the target
		_, third = self.paid(1000)
		self.assertEqual(self.shape(third), [(1.5, 1000, 2000, 150)])

	def test_cancelled_payment_reversals_step_the_year_to_date_down_once(self):
		self.paid(400)
		date = self.next_date()
		lines = [(h.make_item(self.group, percent=10), 1, 1000), (h.make_item(self.group, percent=5), 1, 600)]
		invoice = h.make_invoice(self.customer, lines, team=[(self.person, 100)], date=date)
		payment = p.pay(invoice, 1600, date=date)
		payment.cancel()
		reversals = h.ledger(invoice.name, is_cancelled=1, entry_type="Reversal")
		steps = sorted(
			((flt(r.running_total_before), flt(r.running_total_after)) for r in reversals), reverse=True
		)
		self.assertEqual(steps, [(2000, 1000), (1000, 400)])
		self.assertEqual(self.year_to_date(), 400)

	def test_every_partial_payment_follows_the_invoice_split(self):
		self.target(1500)
		self.paid(1000)
		date = self.next_date()
		item = h.make_item(self.group, percent=10)
		invoice = h.make_invoice(self.customer, [(item, 1, 1000)], team=[(self.person, 100)], date=date)
		p.pay(invoice, 300, date=date)
		p.pay(invoice, 700, date=add_days(date, 1))
		# half of the invoice is within the target, so each payment is half at 1 and half at 1.5
		self.assertEqual(
			self.shape(invoice),
			[(1, 1000, 1150, 15), (1.5, 1150, 1300, 22.5), (1, 1300, 1650, 35), (1.5, 1650, 2000, 52.5)],
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

	def paid_with_vat(self, net, percent=10):
		"""Pays a whole invoice of the net amount plus 15% VAT, dated after the earlier ones."""
		date = self.next_date()
		item = h.make_item(self.group, percent=percent)
		invoice = h.make_invoice(
			self.customer, [(item, 1, net)], team=[(self.person, 100)], date=date, taxes=h.vat_rows()
		)
		p.pay(invoice, invoice.grand_total, date=date)
		return invoice

	def test_vat_does_not_count_towards_the_target(self):
		self.target(1500)
		first, second = self.paid_with_vat(1000), self.paid_with_vat(1000)
		self.assertEqual(first.grand_total, 1150)
		self.assertEqual(self.shape(first), [(1, 0, 1000, 100)])
		# the multiplier starts at the net target of 1500, not at the 1150 + 1150 paid
		self.assertEqual(self.shape(second), [(1, 1000, 1500, 50), (1.5, 1500, 2000, 75)])
		self.assertEqual([flt(r.eligible_amount) for r in h.ledger(first.name)], [1000])
		self.assertEqual([flt(r.eligible_amount) for r in h.ledger(second.name)], [500, 500])
		self.assertEqual(self.year_to_date(), 2000)

	def test_part_payments_of_a_vat_invoice_count_their_net_share(self):
		self.target(1500)
		invoice = h.make_invoice(
			self.customer,
			[(h.make_item(self.group, percent=10), 1, 2500)],
			team=[(self.person, 100)],
			date=self.next_date(),
			taxes=h.vat_rows(),
		)
		self.assertEqual(invoice.grand_total, 2875)
		p.pay(invoice, 1150, date=self.next_date())
		p.pay(invoice, 1725, date=self.next_date())
		# net 2500 against a 1500 target: 60% at 1, 40% at 1.5 on every payment
		# 1150 of 2875 counts 1000 of the net, 1725 counts 1500
		self.assertEqual(
			self.shape(invoice),
			[(1, 0, 600, 60), (1.5, 600, 1000, 60), (1, 1000, 1900, 90), (1.5, 1900, 2500, 90)],
		)
		self.assertEqual(self.year_to_date(), 2500)

	def test_invoice_crossing_the_target_splits_every_line_in_proportion(self):
		self.target(1000)
		self.paid(800)
		date = self.next_date()
		low, high = h.make_item(self.group, percent=5), h.make_item(self.group, percent=10)
		invoice = h.make_invoice(
			self.customer, [(low, 1, 500), (high, 1, 500)], team=[(self.person, 100)], date=date
		)
		p.pay(invoice, 1000, date=date)
		by_line = {}
		for row in h.ledger(invoice.name):
			by_line.setdefault(row.reference_row, []).append(
				(flt(row.multiplier), flt(row.eligible_amount), flt(row.commission_amount))
			)
		# 200 of the invoice's 1000 is within the target: every line has 20% at 1 and 80% at 1.5
		self.assertEqual(by_line[invoice.items[0].name], [(1, 100, 5), (1.5, 400, 30)])
		self.assertEqual(by_line[invoice.items[1].name], [(1, 100, 10), (1.5, 400, 60)])
		self.assertEqual(flt(sum(r.commission_amount for r in h.ledger(invoice.name))), 105)
		self.assertEqual(self.year_to_date(), 1800)

	def test_foreign_invoice_splits_at_the_invoice_rate_and_pays_at_the_payment_rate(self):
		self.target(1500)
		self.paid(1000)
		date = self.next_date()
		customer = h.make_customer("USD")
		item = h.make_item(self.group, percent=10)
		invoice = h.make_invoice(
			customer, [(item, 1, 100)], team=[(self.person, 100)], date=date, currency="USD", rate=18
		)
		p.pay(invoice, 100, date=date, rate=19)
		# the invoice is 1800 rand at 18: 500 up to the target (5/18), 1300 above it (13/18)
		# the payment is 1900 rand at 19, commission 190 rand split the same way
		self.assertEqual(self.shape(invoice), [(1, 1000, 1527.78, 52.78), (1.5, 1527.78, 2900, 205.83)])
		self.assertEqual([flt(r.eligible_amount) for r in h.ledger(invoice.name)], [527.78, 1372.22])
		self.assertEqual(self.year_to_date(), 2900)

	def test_cancelled_invoices_bring_the_target_total_back_below_the_target(self):
		self.target(1500)
		first, first_invoice = self.paid(1000)
		second, second_invoice = self.paid(1000)
		self.paid(1000)
		for payment, invoice in ((first, first_invoice), (second, second_invoice)):
			payment.cancel()
			invoice.reload()
			invoice.cancel()
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

	def test_cancelled_payment_does_not_lower_the_target_total(self):
		self.target(1500)
		first, _ = self.paid(1000)
		self.paid(1000)
		first.cancel()
		_, third = self.paid(1000)
		# the running totals follow the money, the target total follows the submitted invoices
		self.assertEqual(self.shape(third), [(1.5, 1000, 2000, 150)])

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

	def kumar(self):
		"""Target 400,000 at 5%: 150,000 earned, then 350,000 split 250,000 at 1 and 100,000 at 1.5."""
		self.target(400000)
		self.paid(150000, percent=5)
		return self.paid(350000, percent=5, qty=35)[1]

	def deductions(self, invoice):
		"""(multiplier, eligible sales, earned, commission, running total before, after) of each live deduction."""
		return [
			(
				flt(r.multiplier),
				flt(r.eligible_amount),
				flt(r.earned_amount),
				flt(r.commission_amount),
				flt(r.running_total_before),
				flt(r.running_total_after),
			)
			for r in h.ledger(invoice.name)
			if r.entry_type == "Credit Note Deduction"
		]

	def test_a_return_comes_off_the_part_above_the_target_first(self):
		invoice = self.kumar()
		p.make_credit_note(invoice, lines={0: 15}, date=add_days(self.next_date(), 1))
		self.assertEqual(
			self.deductions(invoice),
			[(1.5, -100000, -5000, -7500, 500000, 400000), (1, -50000, -2500, -2500, 400000, 350000)],
		)
		self.assertEqual(self.year_to_date(), 350000)
		total = sum(r.commission_amount for r in h.ledger(sales_person=self.person))
		self.assertEqual(flt(total, 2), 17500)  # 5% of the net sales 350,000

	def test_a_small_return_is_taken_entirely_above_the_target(self):
		invoice = self.kumar()
		p.make_credit_note(invoice, lines={0: 6}, date=add_days(self.next_date(), 1))
		self.assertEqual(self.deductions(invoice), [(1.5, -60000, -3000, -4500, 500000, 440000)])

	def test_full_return_of_the_crossing_invoice_takes_back_both_parts(self):
		invoice = self.kumar()
		p.make_credit_note(invoice, date=add_days(self.next_date(), 1))
		self.assertEqual(
			self.deductions(invoice),
			[(1.5, -100000, -5000, -7500, 500000, 400000), (1, -250000, -12500, -12500, 400000, 150000)],
		)
		self.assertEqual(self.year_to_date(), 150000)

	def test_later_returns_continue_where_the_earlier_one_stopped(self):
		invoice = self.kumar()
		p.make_credit_note(invoice, lines={0: 6}, date=add_days(self.next_date(), 1))
		p.make_credit_note(invoice, lines={0: 9}, date=add_days(self.next_date(), 2))
		self.assertEqual(
			self.deductions(invoice),
			[
				(1.5, -60000, -3000, -4500, 500000, 440000),
				(1.5, -40000, -2000, -3000, 440000, 400000),
				(1, -50000, -2500, -2500, 400000, 350000),
			],
		)
		self.assertEqual(self.year_to_date(), 350000)

	def test_cancelled_credit_note_gives_back_exactly_its_pieces(self):
		invoice = self.kumar()
		before = self.shape(invoice)
		note = p.make_credit_note(invoice, lines={0: 15}, date=add_days(self.next_date(), 1))
		note.cancel()
		self.assertEqual(self.deductions(invoice), [])
		self.assertEqual(self.shape(invoice), before)
		self.assertEqual(self.year_to_date(), 500000)

	def test_payment_cancelled_after_payroll_is_taken_back_top_first_per_part(self):
		pr.make_component(pr.COMMISSION_COMPONENT, "TSC", remove_if_zero=1)
		pr.ensure_holiday_list()
		pr.ensure_payroll_period()
		h.set_settings(commission_salary_component=pr.COMMISSION_COMPONENT)
		employee = frappe.db.get_value("Sales Person", self.person, "employee")
		pr.make_structure_assignment(employee)
		self.target(400000)
		self.paid(150000, percent=5)
		payment, invoice = self.paid(350000, percent=5)
		pr.make_slip(employee, h.date_in(0, "04-01"), h.date_in(0, "04-30"), submit=True)
		payment.cancel()
		rows = [r for r in h.ledger(invoice.name) if r.entry_type == "Reversal"]
		self.assertEqual(
			[
				(
					flt(r.multiplier),
					flt(r.commission_amount),
					flt(r.running_total_before),
					flt(r.running_total_after),
				)
				for r in rows
			],
			[(1.5, -7500, 500000, 400000), (1, -12500, 400000, 150000)],
		)
		self.assertEqual(self.year_to_date(), 150000)

	def crossing(self, pay=True):
		"""Target 20,000 at 10%: 10,000 paid before, then 3 x 5,000 split 10,000 at 1 and 5,000 at 1.5."""
		self.target(20000)
		self.paid(10000)
		if pay:
			return self.paid(15000, qty=3)[1]
		return h.make_invoice(
			self.customer,
			[(h.make_item(self.group, percent=10), 3, 5000)],
			team=[(self.person, 100)],
			date=self.next_date(),
		)

	def return_of(self, invoice, qty):
		note = p.make_credit_note(invoice, lines={0: qty}, date=add_days(self.next_date(), 1))
		line = note.items[0]
		taken = flt(sum(r.commission_amount for r in h.ledger(invoice.name) if r.credit_note == note.name), 2)
		return line, taken, note

	def test_a_return_estimate_takes_the_part_above_the_target_first(self):
		line, taken, note = self.return_of(self.crossing(), 1)
		self.assertEqual((flt(line.commission_amount), taken), (-750, -750))
		self.assertEqual(line.target_multiplier, f"1.5 ({fmt_money(5000, currency='ZAR')})")
		self.assertEqual(flt(note.sales_team[0].incentives), -750)

	def test_a_return_bigger_than_the_part_above_the_target_lists_both_parts(self):
		line, taken, _note = self.return_of(self.crossing(), 2)
		self.assertEqual((flt(line.commission_amount), taken), (-1250, -1250))
		self.assertEqual(
			line.target_multiplier,
			f"1.5 ({fmt_money(5000, currency='ZAR')}), 1 ({fmt_money(5000, currency='ZAR')})",
		)

	def test_the_second_return_continues_with_the_part_within_the_target(self):
		invoice = self.crossing()
		self.return_of(invoice, 1)
		line, taken, _note = self.return_of(invoice, 1)
		self.assertEqual((flt(line.commission_amount), taken), (-500, -500))
		self.assertEqual(line.target_multiplier, f"1 ({fmt_money(5000, currency='ZAR')})")

	def test_a_return_of_a_line_below_the_target_keeps_one_multiplier(self):
		self.target(100000)
		line, taken, _note = self.return_of(self.paid(15000, qty=3)[1], 1)
		self.assertEqual((flt(line.commission_amount), taken), (-500, -500))
		self.assertEqual(line.target_multiplier, "1")

	def test_a_return_of_an_unpaid_crossing_invoice_keeps_the_blended_estimate(self):
		# nothing is paid, so the ledger has no split yet and no deduction: the original's blended multiplier
		invoice = self.crossing(pay=False)
		line, taken, _note = self.return_of(invoice, 1)
		self.assertEqual(taken, 0)
		self.assertEqual(flt(line.commission_amount), flt(-500 * 7 / 6, 2))
