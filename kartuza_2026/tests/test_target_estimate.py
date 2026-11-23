import frappe
from frappe.utils import add_days, flt, fmt_money

from kartuza_2026.tests import helpers as h
from kartuza_2026.tests import payment_helpers as p
from kartuza_2026.utils.line_fields import line_multiplier
from kartuza_2026.utils.preview import get_line_commission


class TestTargetEstimate(h.CommissionTestCase):
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

	def target(self, amount, person=None):
		h.set_target(person or self.person, h.fiscal_year_of(h.date_in(0, "04-01")), amount)

	def paid(self, amount, person=None):
		date = self.next_date()
		invoice = h.make_invoice(
			self.customer, [(self.item, 1, amount)], team=[(person or self.person, 100)], date=date
		)
		p.pay(invoice, amount, date=date)
		return invoice

	def over_target(self):
		self.target(1000)
		self.paid(1000)

	def test_person_over_target_is_estimated_and_paid_at_one_and_a_half_once(self):
		self.over_target()
		invoice = h.make_invoice(self.customer, [(self.item, 1, 1000)], team=self.team, date=self.next_date())
		line = invoice.items[0]
		self.assertEqual(line_multiplier(line), 1.5)
		self.assertEqual(flt(line.commission_amount), 150)
		self.assertEqual(flt(line.base_commission_amount), 150)
		self.assertEqual(flt(invoice.total_commission_amount), 150)
		self.assertEqual(flt(invoice.sales_team[0].incentives), 150)
		p.pay(invoice, 1000, date=invoice.posting_date)
		row = h.ledger(invoice.name)[-1]
		self.assertEqual(flt(row.multiplier), 1.5)
		# 100 x 1.5 once, not 150 x 1.5
		self.assertEqual(flt(row.commission_amount), 150)

	def test_person_below_target_stays_at_one(self):
		self.target(5000)
		self.paid(1000)
		order = h.make_order(self.customer, [(self.item, 1, 1000)], team=self.team, date=self.next_date())
		self.assertEqual(line_multiplier(order.items[0]), 1)
		self.assertEqual(flt(order.items[0].commission_amount), 100)
		self.assertEqual(flt(order.sales_team[0].incentives), 100)

	def test_no_target_stays_at_one(self):
		self.paid(1000)
		order = h.make_order(self.customer, [(self.item, 1, 1000)], team=self.team, date=self.next_date())
		self.assertEqual(flt(order.items[0].commission_amount), 100)

	def test_document_that_crosses_the_target_gets_a_blended_multiplier(self):
		self.target(1500)
		self.paid(1000)
		order = h.make_order(self.customer, [(self.item, 1, 2000)], team=self.team, date=self.next_date())
		# 500 within the target at 1 and 1,500 above it at 1.5
		self.assertAlmostEqual(line_multiplier(order.items[0]), 2750 / 2000, 6)
		self.assertEqual(flt(order.items[0].commission_amount), 275)

	def test_estimate_of_a_crossing_order_in_the_sheet_example(self):
		self.target(400000)
		self.paid(150000)
		order = h.make_order(self.customer, [(self.item, 1, 350000)], team=self.team, date=self.next_date())
		# 250,000 x 10% + 100,000 x 10% x 1.5
		self.assertEqual(flt(order.items[0].commission_amount), 40000)
		self.assertAlmostEqual(line_multiplier(order.items[0]), 40000 / 35000, 6)
		self.assertEqual(flt(order.sales_team[0].incentives), 40000)

	def test_two_persons_only_the_first_over_target(self):
		second = h.make_sales_person()
		self.over_target()
		order = h.make_order(
			self.customer,
			[(self.item, 1, 1000)],
			team=[(self.person, 60), (second, 40)],
			date=self.next_date(),
		)
		line = order.items[0]
		self.assertEqual([flt(r.incentives) for r in order.sales_team], [90, 40])
		self.assertAlmostEqual(flt(line.commission_amount), 130)
		self.assertAlmostEqual(flt(line.base_commission_amount), 130)
		self.assertAlmostEqual(flt(order.total_commission_amount), 130)
		self.assertAlmostEqual(flt(order.base_total_commission_amount), 130)
		self.assertAlmostEqual(sum(flt(r.incentives) for r in order.sales_team), 130)

	def test_draft_is_escalated_when_saved_again_after_an_earlier_document_is_submitted(self):
		self.target(1500)
		draft_date = add_days(h.date_in(0, "04-01"), 10)
		draft = h.make_invoice(
			self.customer, [(self.item, 1, 1000)], team=self.team, date=draft_date, submit=False
		)
		self.assertEqual(flt(draft.items[0].commission_amount), 100)
		# an unpaid invoice dated before the draft counts towards the target
		h.make_invoice(self.customer, [(self.item, 1, 2000)], team=self.team, date=self.next_date())
		draft.save()
		self.assertEqual(flt(draft.items[0].commission_amount), 150)
		self.assertEqual(flt(draft.sales_team[0].incentives), 150)

	def test_submitted_order_is_not_recalculated_when_the_target_is_crossed(self):
		self.target(1500)
		order = h.make_order(self.customer, [(self.item, 1, 1000)], team=self.team, date=self.next_date())
		self.paid(2000)
		order.reload()
		self.assertEqual(flt(order.items[0].commission_amount), 100)
		self.assertEqual(line_multiplier(order.items[0]), 1)
		self.assertEqual(flt(order.sales_team[0].incentives), 100)

	def test_credit_note_of_an_escalated_invoice(self):
		self.over_target()
		invoice = h.make_invoice(self.customer, [(self.item, 2, 500)], team=self.team, date=self.next_date())
		p.pay(invoice, 1000, date=invoice.posting_date)
		self.assertEqual(flt(h.ledger(invoice.name)[-1].commission_amount), 150)
		note = p.make_credit_note(invoice, {0: 1}, date=add_days(invoice.posting_date, 1))
		self.assertEqual(line_multiplier(note.items[0]), 1.5)
		self.assertEqual(flt(note.items[0].commission_amount), -75)
		self.assertEqual(flt(note.total_commission_amount), -75)
		self.assertEqual(flt(note.sales_team[0].incentives), -75)
		total = flt(sum(r.commission_amount for r in h.ledger(invoice.name)), 2)
		self.assertEqual(total, 75)

	def test_preview_matches_save(self):
		self.over_target()
		date = self.next_date()
		doc = {
			"doctype": "Sales Order",
			"customer": self.customer,
			"company": h.COMPANY,
			"transaction_date": date,
			"delivery_date": add_days(date, 30),
			"currency": "ZAR",
			"conversion_rate": 1,
			"items": [
				{**r, "net_amount": 1000, "base_net_amount": 1000, "grant_commission": 1}
				for r in h.item_rows([(self.item, 1, 1000)])
			],
			"sales_team": h.team_rows(self.team),
		}
		preview = get_line_commission(frappe.as_json(doc))
		order = h.make_order(self.customer, [(self.item, 1, 1000)], team=self.team, date=date)
		line = order.items[0]
		self.assertEqual(preview["lines"][0]["commission_amount"], flt(line.commission_amount))
		self.assertEqual(preview["lines"][0]["base_commission_amount"], flt(line.base_commission_amount))
		self.assertEqual(preview["lines"][0]["target_multiplier"], "1.5")
		self.assertEqual(preview["incentives"], [flt(order.sales_team[0].incentives)])
		self.assertEqual(preview["total_commission_amount"], flt(order.total_commission_amount))

	def money(self, amount):
		return fmt_money(amount, currency="ZAR")

	def crossing_order(self, lines, submit=True):
		self.target(400000)
		self.paid(150000)
		return h.make_order(self.customer, lines, team=self.team, date=self.next_date(), submit=submit)

	def test_target_multiplier_text_below_target_is_one(self):
		self.target(5000)
		self.paid(1000)
		order = h.make_order(self.customer, [(self.item, 1, 1000)], team=self.team, date=self.next_date())
		self.assertEqual(order.items[0].target_multiplier, "1")

	def test_target_multiplier_text_past_target_is_the_escalator(self):
		self.over_target()
		order = h.make_order(self.customer, [(self.item, 1, 1000)], team=self.team, date=self.next_date())
		self.assertEqual(order.items[0].target_multiplier, "1.5")

	def test_target_multiplier_text_of_a_crossing_order_shows_both_parts(self):
		order = self.crossing_order([(self.item, 1, 350000)])
		expected = f"1 ({self.money(250000)}), 1.5 ({self.money(100000)})"
		self.assertEqual(order.items[0].target_multiplier, expected)

	def test_target_multiplier_text_splits_every_line_in_proportion(self):
		order = self.crossing_order([(self.item, 1, 200000), (self.item, 1, 150000)])
		first, second = (row.target_multiplier for row in order.items)
		self.assertEqual(
			first, f"1 ({self.money(200000 * 250 / 350)}), 1.5 ({self.money(200000 * 100 / 350)})"
		)
		self.assertEqual(
			second, f"1 ({self.money(150000 * 250 / 350)}), 1.5 ({self.money(150000 * 100 / 350)})"
		)

	def test_target_multiplier_text_is_empty_without_commission(self):
		item = h.make_item(self.group, percent=0)
		order = h.make_order(self.customer, [(item, 1, 1000)], team=self.team, date=self.next_date())
		self.assertFalse(order.items[0].target_multiplier)

	def test_preview_returns_the_target_multiplier_text(self):
		self.target(400000)
		self.paid(150000)
		date = self.next_date()
		doc = {
			"doctype": "Sales Order",
			"customer": self.customer,
			"company": h.COMPANY,
			"transaction_date": date,
			"delivery_date": add_days(date, 30),
			"currency": "ZAR",
			"conversion_rate": 1,
			"items": [
				{**r, "net_amount": 350000, "base_net_amount": 350000, "grant_commission": 1}
				for r in h.item_rows([(self.item, 1, 350000)])
			],
			"sales_team": h.team_rows(self.team),
		}
		preview = get_line_commission(frappe.as_json(doc))
		order = h.make_order(self.customer, [(self.item, 1, 350000)], team=self.team, date=date)
		self.assertEqual(preview["lines"][0]["target_multiplier"], order.items[0].target_multiplier)
		self.assertIn("1.5 (", preview["lines"][0]["target_multiplier"])

	def test_credit_note_copies_the_target_multiplier_text(self):
		self.target(400000)
		self.paid(150000)
		invoice = h.make_invoice(
			self.customer, [(self.item, 1, 350000)], team=self.team, date=self.next_date()
		)
		self.assertIn("1.5 (", invoice.items[0].target_multiplier)
		note = p.make_credit_note(invoice, {0: 1}, date=add_days(invoice.posting_date, 1))
		self.assertEqual(note.items[0].target_multiplier, invoice.items[0].target_multiplier)
