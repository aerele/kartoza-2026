"""Scenario sweep of the payroll side: salary slips, stamping, reversals and closed periods."""

import json

import frappe
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import flt, get_first_day, get_last_day, nowdate

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.tests import payroll_helpers as pr
from sales_commission.tests import scenario_helpers as s
from sales_commission.utils.escalator import year_to_date_sales

APRIL = (h.date_in(0, "04-01"), h.date_in(0, "04-30"))
MAY = (h.date_in(0, "05-01"), h.date_in(0, "05-31"))
JUNE = (h.date_in(0, "06-01"), h.date_in(0, "06-30"))


class PayrollScenarioCase(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		pr.make_component(pr.COMMISSION_COMPONENT, "TSC", remove_if_zero=1)
		pr.ensure_holiday_list()
		pr.ensure_payroll_period()
		h.set_settings(
			factors=(1.0, 1.0),
			commission_salary_component=pr.COMMISSION_COMPONENT,
			include_commission_for_credit_note=0,
		)
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
		# reversals and advance moves are dated by the clock (the code posts them today), so this month is today's
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


class TestSlipAcrossAFiscalYear(PayrollScenarioCase):
	def test_slip_over_the_year_end_pays_exactly_the_rows_dated_in_its_period(self):
		last_year, this_year = h.date_in(1, "02-10"), h.date_in(0, "03-05")
		h.set_target(self.person, h.fiscal_year_of(last_year), 1000)
		h.set_target(self.person, h.fiscal_year_of(this_year), 500)
		# last year: 1000 reaches the target, so what follows earns 1.5. This year: 500 at 1 and the rest at 1.5
		self.paid(1000, h.date_in(1, "02-10"))
		self.paid(1000, h.date_in(1, "02-20"))
		self.paid(1000, h.date_in(0, "03-05"))
		self.paid(1000, h.date_in(0, "03-20"))
		period = (h.date_in(1, "02-15"), h.date_in(0, "03-15"))
		slip = self.slip(period, submit=True)
		self.assertEqual(self.amount(slip), 275)
		rows = frappe.get_all(
			"Commission Ledger",
			filters={"employee": self.employee, "is_cancelled": 0},
			fields=["posting_date", "fiscal_year", "multiplier", "commission_amount", "salary_slip"],
			order_by="posting_date asc, multiplier asc",
		)
		self.assertEqual(
			[(str(r.posting_date), flt(r.multiplier), flt(r.commission_amount), r.salary_slip) for r in rows],
			[
				(h.date_in(1, "02-10"), 1, 100, None),
				(h.date_in(1, "02-20"), 1.5, 150, slip.name),
				(h.date_in(0, "03-05"), 1, 50, slip.name),
				(h.date_in(0, "03-05"), 1.5, 75, slip.name),
				(h.date_in(0, "03-20"), 1.5, 150, None),
			],
		)
		self.assertEqual(len({r.fiscal_year for r in rows}), 2)
		# the rows outside the period wait for their own slips and are paid once
		self.assertEqual(
			self.amount(self.slip((h.date_in(0, "03-16"), h.date_in(0, "03-31")), submit=True)), 150
		)
		self.assertEqual(
			self.amount(self.slip((h.date_in(1, "02-01"), h.date_in(1, "02-14")), submit=True)), 100
		)


class TestPayrollDeductions(PayrollScenarioCase):
	def setUp(self):
		super().setUp()
		h.set_settings(include_commission_for_credit_note=1)

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
			"update `tabPayment Ledger Entry` set amount = amount * 18 / 17 where voucher_no = %s",
			payment.name,
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
				"from_date": f"{h.FY_YEAR - 1}-06-01",
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


class TestCreditNoteSetting(PayrollScenarioCase):
	def credit_note_case(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(
			h.make_customer(), [(item, 2, 1000)], date=h.date_in(0, "04-10"), team=[(self.person, 100)]
		)
		p.pay(invoice, 2000, date=h.date_in(0, "04-10"))
		return invoice

	def ledger_rows(self):
		return frappe.get_all(
			"Commission Ledger",
			filters={"employee": self.employee, "is_cancelled": 0},
			fields=["entry_type", "status", "salary_slip", "posting_date"],
			order_by="creation",
		)

	def test_off_leaves_the_credit_note_row_unpaid(self):
		invoice = self.credit_note_case()
		p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "04-12"))
		slip = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(slip), 200)
		rows = {r.entry_type: r for r in self.ledger_rows()}
		self.assertEqual(rows["Earned"].status, "Paid")
		self.assertEqual(rows["Credit Note Deduction"].status, "To Deduct")
		self.assertFalse(rows["Credit Note Deduction"].salary_slip)

	def test_on_deducts_and_stamps_the_credit_note_row(self):
		h.set_settings(include_commission_for_credit_note=1)
		invoice = self.credit_note_case()
		p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "04-12"))
		slip = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(slip), 100)
		rows = {r.entry_type: r for r in self.ledger_rows()}
		self.assertEqual(rows["Earned"].status, "Paid")
		self.assertEqual(rows["Credit Note Deduction"].status, "Deducted")
		self.assertEqual(rows["Credit Note Deduction"].salary_slip, slip.name)

	def test_off_excludes_a_cancelled_credit_note_and_its_reversal(self):
		invoice = self.credit_note_case()
		note = p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "04-12"))
		note.cancel()
		slip = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(slip), 200)
		rows = frappe.get_all(
			"Commission Ledger",
			filters={"employee": self.employee, "credit_note": note.name},
			fields=["entry_type", "salary_slip"],
		)
		self.assertEqual(sorted(r.entry_type for r in rows), ["Credit Note Deduction", "Reversal"])
		self.assertFalse([r for r in rows if r.salary_slip])

	def test_switched_on_later_takes_only_credit_notes_of_the_slip_period(self):
		invoice = self.credit_note_case()
		p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "04-12"))
		self.paid(3000, h.date_in(0, "05-05"))
		second = self.invoice(2000, h.date_in(0, "05-06"))
		p.pay(second, 2000, date=h.date_in(0, "05-06"))
		p.make_credit_note(second, date=h.date_in(0, "05-10"))
		h.set_settings(include_commission_for_credit_note=1)
		slip = self.slip(MAY, submit=True)
		# May: 300 + 200 earned, only the May note (-200) counts, the April note (-100) stays
		self.assertEqual(self.amount(slip), 300)
		april = [r for r in self.ledger_rows() if r.entry_type == "Credit Note Deduction"]
		stamped = {str(r.posting_date): r.salary_slip for r in april}
		self.assertFalse(stamped[str(h.date_in(0, "04-12"))])
		self.assertEqual(stamped[str(h.date_in(0, "05-10"))], slip.name)

	def test_cancelling_the_slip_releases_the_credit_note_rows(self):
		h.set_settings(include_commission_for_credit_note=1)
		invoice = self.credit_note_case()
		p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "04-12"))
		slip = self.slip(APRIL, submit=True)
		slip.cancel()
		self.assertEqual(sorted(r.status for r in self.ledger_rows()), ["Earned", "To Deduct"])
		self.assertFalse([r for r in self.ledger_rows() if r.salary_slip])


class TestAdvanceMovedAfterPayroll(PayrollScenarioCase):
	"""An invoice taking an order advance that an earlier salary slip already paid."""

	def order_with_advance(self, advance=1000, lines=None):
		lines = lines or [(h.make_item(h.make_group(), percent=10), 1, 5000)]
		order = h.make_order(h.make_customer(), lines, date=h.date_in(0, "04-01"), team=[(self.person, 100)])
		p.pay(order, advance, date=h.date_in(0, "04-10"))
		return order

	def take_advance(self, order, date, quantities=None):
		invoice = p.invoice_from_order(order, quantities, date=date, submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		return invoice

	def adjustments(self, *docs):
		"""(document type, status, amount, posting date, salary slip) of the live Advance Adjustment rows."""
		rows = frappe.get_all(
			"Commission Ledger",
			filters={
				"reference_name": ["in", [doc.name for doc in docs]],
				"entry_type": "Advance Adjustment",
				"is_cancelled": 0,
			},
			fields=["reference_doctype", "status", "commission_amount", "posting_date", "salary_slip"],
		)
		return sorted(
			(r.reference_doctype, r.status, flt(r.commission_amount), str(r.posting_date), r.salary_slip)
			for r in rows
		)

	def test_advance_taken_after_the_slip_is_stamped_on_that_slip_and_the_balance_pays_normally(self):
		order = self.order_with_advance()
		april = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(april), 100)
		invoice = self.take_advance(order, h.date_in(0, "05-15"))
		may = h.date_in(0, "05-15")
		self.assertEqual(
			self.adjustments(order, invoice),
			[
				("Sales Invoice", "Paid", 100, may, april.name),
				("Sales Order", "Moved to Invoice", -100, may, april.name),
			],
		)
		for row in h.ledger(invoice.name):
			self.assertEqual(str(row.payment_date), h.date_in(0, "04-10"))
		may_slip = self.slip(MAY)
		self.assertIsNone(pr.commission_row(may_slip))
		p.pay(invoice, 4000, date=h.date_in(0, "05-20"))
		may_slip.save()
		self.assertEqual(self.amount(may_slip), 400)
		self.assertEqual(self.live_total(), 500)

	def test_advance_taken_before_the_slip_is_paid_once_and_the_order_row_shows_moved(self):
		order = self.order_with_advance()
		invoice = self.take_advance(order, h.date_in(0, "04-20"))
		self.assertEqual(
			self.adjustments(order, invoice),
			[
				("Sales Invoice", "Earned", 100, h.date_in(0, "04-20"), None),
				("Sales Order", "Moved to Invoice", -100, h.date_in(0, "04-20"), None),
			],
		)
		april = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(april), 100)
		self.assertEqual(
			self.adjustments(order, invoice),
			[
				("Sales Invoice", "Paid", 100, h.date_in(0, "04-20"), april.name),
				("Sales Order", "Moved to Invoice", -100, h.date_in(0, "04-20"), april.name),
			],
		)

	def test_moved_advance_keeps_the_order_split_after_the_target_is_crossed(self):
		h.set_target(self.person, h.fiscal_year_of(h.date_in(0, "04-10")), 3000)
		# the 5,000 order counts first: 3,000 within the target (60%), 2,000 above it (40%)
		order = self.order_with_advance()
		advance = sorted((flt(r.multiplier), flt(r.commission_amount)) for r in h.ledger(order.name))
		self.assertEqual(advance, [(1, 60), (1.5, 60)])
		# the 2,500 invoice comes after the order, so it is all above the target: 250 x 1.5
		self.paid(2500, h.date_in(0, "04-12"))
		self.assertEqual(self.amount(self.slip(APRIL, submit=True)), 495)
		invoice = self.take_advance(order, h.date_in(0, "05-15"))
		moved = sorted(
			(flt(r.multiplier), flt(r.commission_amount))
			for r in h.ledger(invoice.name)
			if r.entry_type == "Advance Adjustment"
		)
		self.assertEqual(moved, advance)
		# the balance of the order's invoice follows the order split: 240 at 1 and 160 x 1.5
		p.pay(invoice, 4000, date=h.date_in(0, "05-20"))
		self.assertEqual(self.amount(self.slip(MAY)), 480)

	def test_part_of_a_paid_advance_moved_stamps_only_that_part(self):
		first, second = h.make_item(h.make_group(), percent=10), h.make_item(h.make_group(), percent=10)
		order = self.order_with_advance(3000, [(first, 1, 1000), (second, 1, 4000)])
		april = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(april), 300)
		invoice = self.take_advance(order, h.date_in(0, "05-15"), {first: 1})
		moved = self.adjustments(order, invoice)
		self.assertEqual({row[4] for row in moved}, {april.name})
		self.assertEqual(sum(row[2] for row in moved), 0)
		self.assertEqual(sum(row[2] for row in moved if row[0] == "Sales Invoice"), 100)
		self.assertEqual({row[1] for row in moved if row[0] == "Sales Order"}, {"Moved to Invoice"})
		# 200 of the 300 stays on the order, paid on the April slip
		rest = h.ledger(order.name, entry_type="Earned")
		self.assertEqual({r.salary_slip for r in rest}, {april.name})
		self.assertEqual(s.person_total(order.name), 200)
		self.assertIsNone(pr.commission_row(self.slip(MAY)))

	def test_slip_cancelled_after_the_move_releases_every_row_and_the_new_slip_pays_once(self):
		order = self.order_with_advance()
		april = self.slip(APRIL, submit=True)
		invoice = self.take_advance(order, h.date_in(0, "05-15"))
		april.cancel()
		self.assertEqual({slip for _amount, slip in pr.ledger_slips(self.employee)}, {None})
		self.assertEqual([row[1] for row in self.adjustments(order, invoice)], ["Earned", "Moved to Invoice"])
		again = self.slip(APRIL, submit=True)
		self.assertEqual(self.amount(again), 100)
		self.assertEqual(
			sorted(pr.ledger_slips(self.employee), key=str), [(-100, None), (100, again.name), (100, None)]
		)
		# the pair nets to nothing in May
		self.assertIsNone(pr.commission_row(self.slip(MAY)))

	def test_invoice_cancelled_after_the_move_takes_the_commission_back_on_the_next_slip(self):
		order = self.order_with_advance()
		self.slip(APRIL, submit=True)
		invoice = self.take_advance(order, h.date_in(0, "05-15"))
		invoice.cancel()
		reversal = [r for r in h.ledger(invoice.name) if r.entry_type == "Reversal"]
		self.assertEqual(
			[(flt(r.commission_amount), r.status, r.salary_slip) for r in reversal],
			[(-100, "To Deduct", None)],
		)
		# the payment is unallocated again, so nothing is earned and the 100 paid in April is taken back
		self.assertEqual(self.live_total(), 0)
		self.assertEqual(year_to_date_sales(self.person, reversal[0].fiscal_year), 0)
		self.paid(2000, nowdate())
		self.assertEqual(self.amount(self.slip(self.this_month())), 100)

	def test_credit_note_after_the_move_deducts_and_keeps_the_year_to_date_right(self):
		h.set_settings(include_commission_for_credit_note=1)
		item = h.make_item(h.make_group(), percent=10)
		order = self.order_with_advance(1000, [(item, 2, 2500)])
		self.slip(APRIL, submit=True)
		invoice = self.take_advance(order, h.date_in(0, "05-15"))
		p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "05-20"))
		note_row = next(r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction")
		self.assertEqual((flt(note_row.commission_amount), flt(note_row.multiplier)), (-50, 1))
		# half of the line was returned and the 1000 advance now pays the 2500 left (40%), so 1000 counts
		self.assertEqual(year_to_date_sales(self.person, note_row.fiscal_year), 1000)
		self.paid(2000, h.date_in(0, "05-25"))
		# 200 on the new sale, -50 for the returned half and +50 because the advance now pays 40% of the half left
		self.assertEqual(self.amount(self.slip(MAY)), 200)

	def test_advance_reconciled_later_is_dated_by_the_reconciliation_and_stamped(self):
		# the Payment Reconciliation tool has no posting date: its day is the day the advance moved
		order = self.order_with_advance()
		april = self.slip(APRIL, submit=True)
		invoice = p.invoice_from_order(order, date=h.date_in(0, "05-15"))
		p.reconcile(order.customer)
		self.assertEqual(
			self.adjustments(order, invoice),
			[
				("Sales Invoice", "Paid", 100, nowdate(), april.name),
				("Sales Order", "Moved to Invoice", -100, nowdate(), april.name),
			],
		)
		self.assertIsNone(pr.commission_row(self.slip(MAY)))
