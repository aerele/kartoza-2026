import frappe
from frappe.utils import add_days, flt, get_first_day, get_last_day, nowdate

from sales_commission.overrides.salary_slip import CommissionSalarySlip
from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.tests import payroll_helpers as pr

APRIL = (h.date_in(0, "04-01"), h.date_in(0, "04-30"))


class TestPayroll(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		pr.make_component(pr.COMMISSION_COMPONENT, "TSC", remove_if_zero=1)
		pr.ensure_holiday_list()
		pr.ensure_payroll_period()
		h.set_settings(
			commission_salary_component=pr.COMMISSION_COMPONENT, include_commission_for_credit_note=0
		)
		self.person = h.make_sales_person()
		self.employee = frappe.db.get_value("Sales Person", self.person, "employee")
		pr.make_structure_assignment(self.employee)

	def pay_invoice(self, amount, date, person=None, company=None):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(
			h.make_customer(), [(item, 1, amount)], date=date, team=[(person or self.person, 100)]
		)
		return invoice, p.pay(invoice, amount, date=date)

	def statuses(self):
		rows = frappe.get_all(
			"Commission Ledger", filters={"employee": self.employee, "is_cancelled": 0}, pluck="status"
		)
		return sorted(rows)

	def april_slip(self, submit=False):
		return pr.make_slip(self.employee, *APRIL, submit=submit)

	def test_new_salary_slip_is_a_commission_salary_slip(self):
		slip = frappe.new_doc("Salary Slip")
		self.assertIsInstance(slip, CommissionSalarySlip)

	def test_slip_pays_the_commission_of_its_period_only(self):
		self.pay_invoice(1000, h.date_in(0, "04-10"))
		self.pay_invoice(2000, h.date_in(0, "04-20"))
		self.pay_invoice(5000, h.date_in(0, "05-05"))
		row = pr.commission_row(self.april_slip())
		self.assertEqual(flt(row.amount), 300)

	def test_submit_stamps_the_rows_and_cancel_clears_them(self):
		self.pay_invoice(1000, h.date_in(0, "04-10"))
		slip = self.april_slip(submit=True)
		self.assertEqual(pr.ledger_slips(self.employee), [(100, slip.name)])
		self.assertEqual(self.statuses(), ["Paid"])
		slip.cancel()
		self.assertEqual(pr.ledger_slips(self.employee), [(100, None)])
		self.assertEqual(self.statuses(), ["Earned"])

	def test_slip_marks_negative_rows_deducted_and_cancel_puts_them_back(self):
		h.set_settings(include_commission_for_credit_note=1)
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(
			h.make_customer(), [(item, 2, 1000)], date=h.date_in(0, "04-10"), team=[(self.person, 100)]
		)
		p.pay(invoice, 2000, date=h.date_in(0, "04-10"))
		note = p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "04-12"))
		self.assertTrue(note)
		slip = self.april_slip(submit=True)
		self.assertEqual(self.statuses(), ["Deducted", "Paid"])
		slip.cancel()
		self.assertEqual(self.statuses(), ["Earned", "To Deduct"])

	def test_stamped_rows_are_not_paid_on_another_slip(self):
		self.pay_invoice(1000, h.date_in(0, "04-10"))
		self.april_slip(submit=True)
		other = pr.make_slip(self.employee, h.date_in(0, "04-15"), h.date_in(0, "05-15"))
		self.assertIsNone(pr.commission_row(other))

	def test_payment_cancelled_after_the_slip_is_a_normal_reversal(self):
		_invoice, payment = self.pay_invoice(1000, h.date_in(0, "04-10"))
		self.april_slip(submit=True)
		self.pay_invoice(3000, nowdate())
		payment.cancel()
		rows = frappe.get_all(
			"Commission Ledger",
			filters={"employee": self.employee, "entry_type": "Reversal"},
			fields=["commission_amount", "is_cancelled", "salary_slip", "posting_date"],
		)
		self.assertEqual(
			[(r.commission_amount, r.is_cancelled, r.salary_slip) for r in rows], [(-100, 0, None)]
		)
		self.assertEqual(
			frappe.db.count("Commission Ledger", {"employee": self.employee, "is_cancelled": 1}), 0
		)
		slip = pr.make_slip(self.employee, get_first_day(nowdate()), get_last_day(nowdate()))
		self.assertEqual(flt(pr.commission_row(slip).amount), 200)

	def test_negative_only_period_adds_no_row(self):
		_invoice, payment = self.pay_invoice(1000, h.date_in(0, "04-10"))
		self.april_slip(submit=True)
		payment.cancel()
		slip = pr.make_slip(self.employee, get_first_day(nowdate()), get_last_day(nowdate()))
		self.assertIsNone(pr.commission_row(slip))

	def test_no_component_in_settings_adds_no_row(self):
		self.pay_invoice(1000, h.date_in(0, "04-10"))
		h.set_settings(commission_salary_component=None)
		self.assertIsNone(pr.commission_row(self.april_slip()))

	def test_rows_of_another_company_are_not_counted(self):
		self.pay_invoice(1000, h.date_in(0, "04-10"))
		name = frappe.db.get_value("Commission Ledger", {"employee": self.employee}, "name")
		frappe.db.set_value("Commission Ledger", name, "company", "Wrong Company")
		self.assertIsNone(pr.commission_row(self.april_slip()))
