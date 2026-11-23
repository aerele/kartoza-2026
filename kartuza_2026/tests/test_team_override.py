import frappe
from frappe.utils import flt

from kartuza_2026.tests import helpers as h
from kartuza_2026.tests import payment_helpers as p


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

	def make_group_person(self):
		return (
			frappe.get_doc(
				{
					"doctype": "Sales Person",
					"sales_person_name": h.unique("SPG"),
					"parent_sales_person": "Sales Team",
					"is_group": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def team_incentives(self, doc):
		return [flt(row.incentives) for row in doc.sales_team]

	def test_incentives_split_an_item_percent_line_by_allocated_percentage(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=self.team)
		self.assertEqual(self.team_incentives(invoice), [30, 20])

	def test_incentives_follow_each_persons_own_rate_and_match_the_ledger(self):
		anna, ben = h.make_sales_person(rate=8), h.make_sales_person(rate=5)
		item = h.make_item(self.group)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=[(anna, 60), (ben, 40)])
		# net 1000: Anna 60% of 1000 at 8% = 48, Ben 40% of 1000 at 5% = 20
		self.assertEqual(self.team_incentives(invoice), [48, 20])
		p.pay(invoice, 1000)
		self.assertEqual(self.by_person(invoice), {anna: 48, ben: 20})

	def test_incentives_are_recomputed_when_the_team_changes_after_submit(self):
		item = h.make_item(self.group, percent=5)
		order = h.make_order(h.make_customer(), [(item, 1, 1000)], team=self.team)
		order.sales_team[0].allocated_percentage = 50
		order.sales_team[1].allocated_percentage = 50
		order.flags.ignore_permissions = True
		order.save()
		self.assertEqual(self.team_incentives(frappe.get_doc("Sales Order", order.name)), [25, 25])

	def test_a_credit_note_has_negative_incentives(self):
		item = h.make_item(self.group, percent=5)
		invoice = h.make_invoice(h.make_customer(), [(item, 10, 1000)], team=self.team)
		p.pay(invoice, 10000)
		note = p.make_credit_note(invoice, {0: 4})
		self.assertEqual(self.team_incentives(note), [-120, -80])

	def test_the_sales_team_commission_rate_is_hidden(self):
		for prop, value in (("hidden", "1"), ("in_list_view", "0")):
			name = f"Sales Team-commission_rate-{prop}"
			self.assertEqual(frappe.db.get_value("Property Setter", name, "value"), value)

	def test_group_sales_person_is_blocked_on_order_invoice_and_customer(self):
		group_person = self.make_group_person()
		item = h.make_item(self.group, percent=5)
		with self.assertRaisesRegex(frappe.ValidationError, "is a group"):
			h.make_order(h.make_customer(), [(item, 1, 1000)], team=[(group_person, 100)])
		with self.assertRaisesRegex(frappe.ValidationError, "is a group"):
			h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=[(group_person, 100)])
		customer = frappe.get_doc("Customer", h.make_customer())
		customer.append("sales_team", {"sales_person": group_person, "allocated_percentage": 100})
		with self.assertRaisesRegex(frappe.ValidationError, "is a group"):
			customer.save()

	def test_customer_form_refuses_a_group_sales_person(self):
		group_person = self.make_group_person()
		customer = frappe.get_doc("Customer", h.make_customer())
		customer.append("sales_team", {"sales_person": group_person, "allocated_percentage": 100})
		with self.assertRaisesRegex(frappe.ValidationError, "is a group") as error:
			customer.save()
		self.assertIn(group_person, str(error.exception))
		self.assertEqual(
			frappe.db.count("Sales Team", {"parent": customer.name, "parenttype": "Customer"}), 0
		)
		customer.reload()
		customer.append("sales_team", {"sales_person": self.first, "allocated_percentage": 100})
		customer.save()
		self.assertEqual(
			frappe.db.get_value("Sales Team", {"parent": customer.name}, "sales_person"), self.first
		)

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
			{r.sales_person: flt(r.running_total_after - r.running_total_before) for r in rows},
			{self.first: 600, self.second: 400},
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
