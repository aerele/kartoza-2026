import frappe
from frappe.utils import flt

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p


def make_user(*roles):
	email = f"{frappe.generate_hash(length=8)}@commission.test"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Tester",
			"send_welcome_email": 0,
			"roles": [{"role": r} for r in roles],
		}
	).insert(ignore_permissions=True)
	return email


class TestSalesTeam(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.first, self.second = h.make_sales_person(), h.make_sales_person()
		self.team = [(self.first, 60), (self.second, 40)]
		self.group = h.make_group()

	def by_person(self, invoice, **filters):
		totals = {}
		for row in h.ledger(invoice.name, **filters):
			totals[row.sales_person] = flt(totals.get(row.sales_person, 0) + flt(row.commission_amount), 2)
		return totals

	def test_commission_is_split_by_allocated_percentage(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		p.pay(invoice, 1000)
		rows = h.ledger(invoice.name)
		self.assertEqual(
			{(r.sales_person, flt(r.allocated_percentage), flt(r.commission_amount)) for r in rows},
			{(self.first, 60, 30), (self.second, 40, 20)},
		)
		self.assertEqual(
			{r.sales_person: flt(r.running_total_after - r.running_total_before) for r in rows}, {self.first: 600, self.second: 400}
		)

	def test_credit_note_takes_back_each_share(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=self.team)
		p.pay(invoice, 10000)
		self.assertEqual(self.by_person(invoice), {self.first: 300, self.second: 200})
		p.make_credit_note(invoice, {0: 4})
		self.assertEqual(self.by_person(invoice), {self.first: 180, self.second: 120})

	def test_each_person_earns_at_their_own_rate(self):
		anna, ben = h.make_sales_person(rate=10), h.make_sales_person(rate=8)
		item = h.make_item(self.group)
		invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=[(anna, 60), (ben, 40)])
		p.pay(invoice, 10000)
		self.assertEqual(self.by_person(invoice), {anna: 600, ben: 320})
		p.make_credit_note(invoice, {0: 4})
		self.assertEqual(self.by_person(invoice), {anna: 360, ben: 192})

	def test_standalone_credit_note_takes_back_each_share(self):
		item = h.make_item(self.group, percent=5)
		customer = h.make_customer()
		h.make_order(customer, [(h.make_item(self.group, percent=5), 1, 100)], team=self.team)
		note = h.make_invoice(customer, [(item, -1, 2760)], team=self.team, is_return=1)
		self.assertEqual(self.by_person(note), {self.first: -82.8, self.second: -55.2})

	def test_every_row_needs_an_employee_also_after_submit(self):
		item = h.make_item(self.group, percent=5)
		nobody = h.make_sales_person(employee=False)
		customer = h.make_customer()
		for doc in (
			h.make_order(customer, [(item, 1, 1000)], team=self.team),
			h.make_invoice(customer, [(item, 1, 1000)], team=self.team),
		):
			doc.append("sales_team", {"sales_person": nobody, "allocated_percentage": 0})
			with self.assertRaises(frappe.ValidationError) as blocked:
				doc.save()
			self.assertIn(f"Sales Person <b>{nobody}</b> has no Employee", str(blocked.exception))

	def test_a_second_row_without_an_employee_is_blocked_on_save(self):
		item = h.make_item(self.group, percent=5)
		nobody = h.make_sales_person(employee=False)
		with self.assertRaises(frappe.ValidationError):
			h.make_order(h.make_customer(), [(item, 1, 1000)], team=[(self.first, 50), (nobody, 50)])


class TestOverride(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		h.set_settings(factors=(1.0, 1.0))
		self.person = h.make_sales_person()
		self.group = h.make_group()
		item = h.make_item(self.group, percent=5)
		self.invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=[(self.person, 100)])
		p.pay(self.invoice, 10000)

	def total(self):
		return flt(sum(r.commission_amount for r in h.ledger(self.invoice.name)), 2)

	def test_only_a_system_manager_changes_the_override(self):
		note = p.make_credit_note(self.invoice, {0: 4})
		self.assertEqual(self.total(), 300)
		clerk = make_user("Accounts User")
		frappe.set_user(clerk)
		note.reload()
		note.override_deduction = 1
		with self.assertRaises(frappe.ValidationError) as blocked:
			note.save()
		self.assertIn("System Manager", str(blocked.exception))

		frappe.set_user(make_user("Accounts User", "System Manager"))
		note.reload()
		note.override_deduction = 1
		note.save()
		self.assertEqual(self.total(), 500)
		self.assertIn("Override Reversal", [r.entry_type for r in h.ledger(self.invoice.name)])

		frappe.set_user(clerk)
		note.reload()
		note.override_deduction = 0
		with self.assertRaises(frappe.ValidationError):
			note.save()

		frappe.set_user(make_user("Accounts User", "System Manager"))
		note.reload()
		note.override_deduction = 0
		note.save()
		self.assertEqual(self.total(), 300)

	def test_a_clerk_can_still_edit_the_reason(self):
		note = p.make_credit_note(self.invoice, {0: 4})
		frappe.set_user(make_user("Accounts User"))
		note.reload()
		note.override_reason = "Noted"
		note.save()
		self.assertEqual(note.reload().override_reason, "Noted")

	def test_override_ticked_before_submit_deducts_nothing_and_needs_no_reason(self):
		note = p.make_credit_note(self.invoice, {0: 4}, submit=False)
		note.override_deduction = 1
		note.save()
		note.submit()
		self.assertEqual(self.total(), 500)

	def test_override_is_cleared_on_an_invoice_that_is_not_a_return(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(
			h.make_customer(), [(item, 1, 100)], submit=False, override_deduction=1, override_reason="Typed"
		)
		invoice.reload()
		self.assertEqual((invoice.override_deduction, invoice.override_reason), (0, None))
