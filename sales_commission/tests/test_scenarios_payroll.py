"""Scenario sweep of the payroll side: salary slips, stamping, reversals and closed periods."""

import json

import frappe
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import flt, get_first_day, get_last_day, nowdate

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.tests import payroll_helpers as pr
from sales_commission.tests import scenario_helpers as s

APRIL = (h.date_in(0, "04-01"), h.date_in(0, "04-30"))
MAY = (h.date_in(0, "05-01"), h.date_in(0, "05-31"))
JUNE = (h.date_in(0, "06-01"), h.date_in(0, "06-30"))


class PayrollScenarioCase(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		pr.make_component(pr.COMMISSION_COMPONENT, "TSC", remove_if_zero=1)
		pr.ensure_holiday_list()
		pr.ensure_payroll_period()
		h.set_settings(factors=(1.0, 1.0), commission_salary_component=pr.COMMISSION_COMPONENT)
		self.person = h.make_sales_person()
		self.employee = frappe.db.get_value("Sales Person", self.person, "employee")
		pr.make_structure_assignment(self.employee)

	def invoice(self, amount, date, person=None, percent=10, customer=None):
		item = h.make_item(h.make_group(), percent=percent)
		return h.make_invoice(
			customer or h.make_customer(), [(item, 1, amount)], date=date, team=[(person or self.person, 100)]
		)

	def paid(self, amount, date, person=None, percent=10):
		invoice = self.invoice(amount, date, person, percent)
		return invoice, p.pay(invoice, amount, date=date)

	def slip(self, period, employee=None, submit=False):
		return pr.make_slip(employee or self.employee, *period, submit=submit)

	def amount(self, slip):
		row = pr.commission_row(slip)
		return flt(row.amount) if row else None

	def this_month(self):
		return (get_first_day(nowdate()), get_last_day(nowdate()))

	def live_total(self, employee=None):
		return flt(sum(a for a, _ in pr.ledger_slips(employee or self.employee)), 2)


class TestSlipPeriods(PayrollScenarioCase):
	def test_payment_on_last_day_is_in_this_month_and_on_first_day_in_the_next(self):
		self.paid(1000, h.date_in(0, "04-30"))
		self.paid(1000, h.date_in(0, "05-01"))
		self.assertEqual(self.amount(self.slip(APRIL)), 100)
		self.assertEqual(self.amount(self.slip(MAY)), 100)

	def test_cancelled_slip_releases_the_rows_and_the_new_slip_pays_once(self):
		self.paid(1000, h.date_in(0, "04-10"))
		first = self.slip(APRIL, submit=True)
		first.cancel()
		second = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(second), 100)
		self.assertEqual(pr.ledger_slips(self.employee), [(100, second.name)])
		self.assertIsNone(pr.commission_row(self.slip((h.date_in(0, "04-15"), h.date_in(0, "05-15")))))

	def test_a_second_slip_for_the_same_period_is_blocked(self):
		self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL)
		with self.assertRaises(frappe.ValidationError):
			self.slip(APRIL)

	def test_saving_a_draft_slip_again_does_not_add_commission_twice(self):
		self.paid(1000, h.date_in(0, "04-10"))
		slip = self.slip(APRIL)
		slip.save()
		slip.save()
		rows = [r for r in slip.earnings if r.salary_component == pr.COMMISSION_COMPONENT]
		self.assertEqual([flt(r.amount) for r in rows], [100])

	def test_overlapping_draft_slips_do_not_pay_the_same_rows_twice(self):
		self.paid(1000, h.date_in(0, "04-10"))
		first = self.slip(APRIL)
		second = self.slip((h.date_in(0, "04-10"), h.date_in(0, "05-10")))
		first.submit()
		second.submit()
		paid = sum(filter(None, [self.amount(first), self.amount(second)]))
		self.assertEqual(paid, 100)

	def test_draft_slip_is_recomputed_when_a_later_payment_lands_in_its_period(self):
		self.paid(1000, h.date_in(0, "04-10"))
		slip = self.slip(APRIL)
		self.paid(2000, h.date_in(0, "04-20"))
		slip.save()
		self.assertEqual(self.amount(slip), 300)


class TestPayrollDeductions(PayrollScenarioCase):
	def test_credit_note_after_a_paid_month_is_deducted_next_month(self):
		first, _ = self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL, submit=True)
		self.paid(3000, h.date_in(0, "05-05"))
		p.make_credit_note(first, date=h.date_in(0, "05-10"))
		self.assertEqual(self.amount(self.slip(MAY)), 200)

	def test_credit_note_cancelled_after_the_deduction_slip_is_paid_back_on_the_next_slip(self):
		first, _ = self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL, submit=True)
		self.paid(3000, h.date_in(0, "05-05"))
		note = p.make_credit_note(first, date=h.date_in(0, "05-10"))
		self.slip(MAY, submit=True)
		note.cancel()
		reversal = frappe.get_all(
			"Commission Ledger",
			filters={"employee": self.employee, "credit_note": note.name, "is_cancelled": 0},
			fields=["commission_amount", "salary_slip", "entry_type"],
		)
		self.assertEqual(
			sorted((r.entry_type, r.commission_amount, bool(r.salary_slip)) for r in reversal),
			[("Credit Note Deduction", -100, True), ("Reversal", 100, False)],
		)
		self.assertEqual(self.amount(self.slip(self.this_month())), 100)

	def test_negative_only_month_is_neither_deducted_nor_carried_forward(self):
		# CURRENT BEHAVIOUR (gap D1): the -100 of May is lost, June pays its own 300 in full
		first, _ = self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL, submit=True)
		p.make_credit_note(first, date=h.date_in(0, "05-10"))
		self.assertIsNone(pr.commission_row(self.slip(MAY, submit=True)))
		self.paid(3000, h.date_in(0, "06-05"))
		self.assertEqual(self.amount(self.slip(JUNE, submit=True)), 300)
		# the employee was paid 100 + 300 while the ledger holds 100 - 100 + 300
		self.assertEqual(self.live_total(), 300)

	def test_credit_note_cancelled_after_payroll_then_next_slip_nets_the_reversal(self):
		_first, payment = self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL, submit=True)
		self.paid(2000, h.date_in(0, "05-05"))
		payment.cancel()
		self.assertEqual(self.amount(self.slip(MAY)), 200)


class TestPayrollLedgerGaps(PayrollScenarioCase):
	def test_backdated_payment_after_the_slip_is_never_picked_up(self):
		# CURRENT BEHAVIOUR (gap): the row is dated April, April's slip is done, no later slip reaches it
		self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL, submit=True)
		self.paid(2000, h.date_in(0, "04-20"))
		late = [r for r in pr.ledger_slips(self.employee) if r[1] is None]
		self.assertEqual(late, [(200, None)])
		self.assertIsNone(pr.commission_row(self.slip(MAY)))
		self.assertIsNone(pr.commission_row(self.slip(self.this_month())))

	def test_partly_paid_key_cancelled_deducts_only_what_was_paid(self):
		# row 1 is paid on the April slip, row 2 (a rate correction, dated April) was posted afterwards and
		# never paid. Cancelling the payment must claw back what was paid (850), not 900.
		item = h.make_item(h.make_group(), percent=5)
		invoice = h.make_invoice(
			h.make_customer("USD"),
			[(item, 1, 1000)],
			team=[(self.person, 100)],
			currency="USD",
			rate=17,
			date=h.date_in(0, "04-01"),
		)
		payment = p.pay(invoice, 1000, rate=17, date=h.date_in(0, "04-10"))
		self.assertEqual(self.amount(self.slip(APRIL, submit=True)), 850)
		frappe.db.sql(
			"update `tabPayment Ledger Entry` set amount = amount * 18 / 17 where voucher_no = %s", payment.name
		)
		from sales_commission.utils.sync import sync_document

		sync_document("Sales Invoice", invoice.name)
		self.assertEqual(self.live_total(), 900)
		self.paid(20000, nowdate())
		payment.cancel()
		current = self.amount(self.slip(self.this_month()))
		# 2000 earned this month minus the 850 that was paid
		self.assertEqual(current, 1150)

	def test_order_advance_cancelled_after_payroll_deducts_what_was_paid_and_cancels_the_correction(self):
		# advance earns 100 (50 per line) and is paid in April. Removing line 2 later posts an unpaid
		# correction on line 1 and an unpaid reversal on line 2. Cancelling the advance must take back
		# exactly the 100 paid, and the unpaid correction rows are cancelled.
		first, second = h.make_item(h.make_group(), percent=10), h.make_item(h.make_group(), percent=10)
		order = h.make_order(
			h.make_customer(),
			[(first, 1, 1000), (second, 1, 1000)],
			team=[(self.person, 100)],
			date=h.date_in(0, "04-01"),
		)
		payment = p.pay(order, 1000, date=h.date_in(0, "04-10"))
		self.assertEqual(self.amount(self.slip(APRIL, submit=True)), 100)
		changes = [{"docname": order.items[0].name, "item_code": first, "qty": 1, "rate": 1000}]
		update_child_qty_rate("Sales Order", json.dumps(changes), order.name)
		self.assertEqual(self.live_total(), 100)
		unpaid = frappe.get_all(
			"Commission Ledger",
			filters={
				"reference_name": order.name,
				"is_cancelled": 0,
				"salary_slip": ["is", "not set"],
				"commission_amount": [">", 0],
			},
			pluck="name",
		)
		self.assertEqual(len(unpaid), 1)
		payment.cancel()
		self.assertEqual(frappe.db.count("Commission Ledger", {"name": ["in", unpaid], "is_cancelled": 0}), 0)
		self.paid(2000, nowdate())
		# 200 earned this month minus the 100 that was paid
		self.assertEqual(self.amount(self.slip(self.this_month())), 100)


class TestPayrollSetup(PayrollScenarioCase):
	def test_component_also_in_the_salary_structure_gives_one_row_with_the_commission(self):
		pr.make_component(pr.BASIC_COMPONENT, "TBP")
		structure = frappe.get_doc(
			{
				"doctype": "Salary Structure",
				"name": h.unique("Commission Test Structure"),
				"company": h.COMPANY,
				"payroll_frequency": "Monthly",
				"currency": "ZAR",
				"is_active": "Yes",
				"earnings": [
					{"salary_component": pr.BASIC_COMPONENT, "amount": 1000},
					{"salary_component": pr.COMMISSION_COMPONENT, "amount": 500},
				],
			}
		).insert(ignore_permissions=True)
		structure.submit()
		frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": self.employee,
				"salary_structure": structure.name,
				"company": h.COMPANY,
				"from_date": f"{h.THIS_YEAR - 1}-06-01",
				"base": 1000,
			}
		).insert(ignore_permissions=True).submit()
		self.paid(1000, h.date_in(0, "04-10"))
		slip = self.slip(APRIL)
		rows = [r for r in slip.earnings if r.salary_component == pr.COMMISSION_COMPONENT]
		self.assertEqual(len(rows), 1)
		# the structure's own 500 is replaced by the commission (no addition, no second row)
		self.assertEqual(flt(rows[0].amount), 100)

	def test_one_employee_cannot_be_on_two_salespersons(self):
		# ERPNext blocks it, so "an employee with two salespersons" cannot exist
		with self.assertRaises(frappe.ValidationError):
			s.make_person_for_employee(self.employee)

	def test_salesperson_employee_changed_after_payment_keeps_old_rows_with_the_old_employee(self):
		self.paid(1000, h.date_in(0, "04-10"))
		other = h.make_employee()
		pr.make_structure_assignment(other)
		person = frappe.get_doc("Sales Person", self.person)
		person.employee = other
		person.save(ignore_permissions=True)
		self.paid(2000, h.date_in(0, "04-12"))
		self.assertEqual(self.amount(self.slip(APRIL)), 100)
		self.assertEqual(self.amount(self.slip(APRIL, employee=other)), 200)

	def test_salesperson_changed_after_payroll_claws_back_from_the_first_person(self):
		invoice, _ = self.paid(1000, h.date_in(0, "04-10"))
		self.slip(APRIL, submit=True)
		other = h.make_sales_person()
		s.change_team(invoice, [(other, 100)])
		first_rows = [r for r in h.ledger(invoice.name) if r.sales_person == self.person]
		self.assertEqual(
			sorted((r.entry_type, flt(r.commission_amount), bool(r.salary_slip)) for r in first_rows),
			[("Earned", 100, True), ("Reversal", -100, False)],
		)
		self.assertEqual(s.person_total(invoice.name, other), 100)
