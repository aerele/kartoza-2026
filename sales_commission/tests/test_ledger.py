import json

import frappe
from frappe.utils import flt

from sales_commission.tests import helpers as h
from sales_commission.tests import payment_helpers as p
from sales_commission.utils.preview import get_line_commission


class TestLedgerAndSafety(h.CommissionTestCase):
	def paid_invoice(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=[(h.make_sales_person(), 100)])
		p.pay(invoice, 1000)
		return invoice

	def test_ledger_rows_cannot_be_changed_or_deleted(self):
		row = frappe.get_doc("Commission Ledger", h.ledger(self.paid_invoice().name)[0].name)
		row.commission_amount = 1
		with self.assertRaises(frappe.ValidationError) as changed:
			row.save(ignore_permissions=True)
		self.assertIn("Commission Ledger entries cannot be changed", str(changed.exception))
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Commission Ledger", row.name, force=True)
		self.assertEqual(flt(frappe.db.get_value("Commission Ledger", row.name, "commission_amount")), 100)

	def test_cancelled_order_can_be_deleted_and_keeps_its_history(self):
		item = h.make_item(h.make_group(), percent=10)
		order = h.make_order(h.make_customer(), [(item, 1, 1000)], team=[(h.make_sales_person(), 100)])
		payment = p.pay(order, 1000)
		payment.cancel()
		order.reload().cancel()
		# ERPNext's own advance rows would block the delete, our ledger must not
		frappe.db.delete("Advance Payment Ledger Entry", {"against_voucher_no": order.name})
		frappe.delete_doc("Sales Order", order.name)
		self.assertEqual(len(h.ledger(order.name, is_cancelled=1)), 2)

	def test_failed_sync_is_logged_and_never_blocks_the_save(self):
		person = h.make_sales_person()
		# a Sales Person pointing at a missing Employee makes every ledger insert fail
		frappe.db.set_value("Sales Person", person, "employee", "NO-SUCH-EMPLOYEE")
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 1, 1000)], team=[(person, 100)])
		payment = p.pay(invoice, 1000)
		self.assertEqual(payment.docstatus, 1)
		self.assertEqual(h.ledger(invoice.name), [])
		title = f"Commission sync failed for Sales Invoice {invoice.name}"
		self.assertTrue(frappe.db.exists("Error Log", {"method": title}))

	def test_sales_and_payment_dates_on_earned_row(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(
			h.make_customer(),
			[(item, 1, 1000)],
			date=h.date_in(0, "03-10"),
			team=[(h.make_sales_person(), 100)],
		)
		p.pay(invoice, 1000, date=h.date_in(0, "04-02"))
		row = h.ledger(invoice.name)[0]
		self.assertEqual(str(row.sales_date), h.date_in(0, "03-10"))
		self.assertEqual(str(row.payment_date), h.date_in(0, "04-02"))
		self.assertEqual(str(row.posting_date), h.date_in(0, "04-02"))

	def test_sales_and_payment_dates_on_advance_row(self):
		item = h.make_item(h.make_group(), percent=10)
		order = h.make_order(
			h.make_customer(),
			[(item, 1, 1000)],
			date=h.date_in(0, "03-10"),
			team=[(h.make_sales_person(), 100)],
		)
		p.pay(order, 1000, date=h.date_in(0, "03-12"))
		invoice = p.invoice_from_order(order, date=h.date_in(0, "03-20"), submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		row = next(r for r in h.ledger(invoice.name) if r.entry_type == "Advance Adjustment")
		self.assertEqual(str(row.sales_date), h.date_in(0, "03-20"))
		self.assertEqual(str(row.payment_date), h.date_in(0, "03-12"))

	def test_credit_note_deduction_has_no_payment_date(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(
			h.make_customer(),
			[(item, 2, 1000)],
			date=h.date_in(0, "03-10"),
			team=[(h.make_sales_person(), 100)],
		)
		p.pay(invoice, 2000, date=h.date_in(0, "03-15"))
		p.make_credit_note(invoice, {0: 1}, date=h.date_in(0, "03-25"))
		row = next(r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction")
		self.assertEqual(str(row.sales_date), h.date_in(0, "03-25"))
		self.assertIsNone(row.payment_date)

	def test_paid_invoice_row_holds_the_line_net_amount(self):
		invoice = self.paid_invoice()
		line = invoice.items[0]
		row = h.ledger(invoice.name)[0]
		self.assertEqual(flt(row.net_amount), 1000)
		self.assertEqual(flt(row.net_amount), flt(line.net_amount))
		self.assertEqual(flt(row.base_net_amount), flt(line.base_net_amount))

	def test_foreign_invoice_row_holds_net_amount_in_both_currencies(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(
			h.make_customer("USD"),
			[(item, 1, 1000)],
			team=[(h.make_sales_person(), 100)],
			currency="USD",
			rate=17,
		)
		p.pay(invoice, 1000, rate=17)
		row = h.ledger(invoice.name)[0]
		self.assertEqual(flt(row.net_amount), 1000)
		self.assertEqual(flt(row.base_net_amount), 17000)

	def test_credit_note_deduction_row_holds_the_note_line_net_amount(self):
		item = h.make_item(h.make_group(), percent=10)
		invoice = h.make_invoice(h.make_customer(), [(item, 2, 1000)], team=[(h.make_sales_person(), 100)])
		p.pay(invoice, 2000)
		note = p.make_credit_note(invoice, {0: 1})
		row = next(r for r in h.ledger(invoice.name) if r.entry_type == "Credit Note Deduction")
		self.assertEqual(row.credit_note, note.name)
		self.assertEqual(flt(row.net_amount), -1000)
		self.assertEqual(flt(row.base_net_amount), flt(note.items[0].base_net_amount))

	def test_advance_row_holds_the_invoice_line_net_amount(self):
		item = h.make_item(h.make_group(), percent=10)
		order = h.make_order(h.make_customer(), [(item, 1, 1000)], team=[(h.make_sales_person(), 100)])
		p.pay(order, 1000)
		advance = next(r for r in h.ledger(order.name))
		self.assertEqual(flt(advance.net_amount), flt(order.items[0].net_amount))
		self.assertEqual(flt(advance.base_net_amount), flt(order.items[0].base_net_amount))
		invoice = p.invoice_from_order(order, submit=False)
		invoice.set_advances()
		invoice.save()
		invoice.submit()
		row = next(r for r in h.ledger(invoice.name) if r.entry_type == "Advance Adjustment")
		self.assertEqual(flt(row.net_amount), 1000)

	def test_sales_person_without_employee_is_blocked(self):
		item = h.make_item(h.make_group(), percent=10)
		person = h.make_sales_person(employee=False)
		for make in (h.make_order, h.make_invoice):
			with self.assertRaises(frappe.ValidationError) as blocked:
				make(h.make_customer(), [(item, 1, 1000)], team=[(person, 100)], submit=False)
			self.assertIn(f"Sales Person <b>{person}</b> has no Employee", str(blocked.exception))

	def preview_args(self, customer, doctype="Sales Order"):
		item = h.make_item(h.make_group(), percent=10)
		doc = {
			"doctype": doctype,
			"customer": customer,
			"company": h.COMPANY,
			"transaction_date": h.ANCHOR,
			"items": [
				{
					"doctype": f"{doctype} Item",
					"name": "row1",
					"item_code": item,
					"item_group": frappe.db.get_value("Item", item, "item_group"),
					"grant_commission": 1,
					"net_amount": 100,
					"base_net_amount": 100,
				}
			],
			"sales_team": [{"sales_person": h.make_sales_person(), "allocated_percentage": 100}],
		}
		return json.dumps(doc)

	def test_preview_without_a_salesperson_works_out_nothing(self):
		args = json.loads(self.preview_args(h.make_customer()))
		args["sales_team"] = []
		result = get_line_commission(json.dumps(args))
		line = result["lines"][0]
		self.assertEqual((line["commission_classification"], line["commission_amount"]), ("", 0))
		self.assertEqual(result["total_commission_amount"], 0)

	def test_preview_returns_lines_and_totals_worked_out_by_the_server(self):
		result = get_line_commission(self.preview_args(h.make_customer()))
		line = result["lines"][0]
		self.assertEqual((line["commission_classification"], line["commission_percent"]), ("New", 10))
		self.assertEqual((line["commission_amount"], line["base_commission_amount"]), (10, 10))
		self.assertEqual(
			(result["total_commission_amount"], result["base_total_commission_amount"]), (10, 10)
		)

	def test_preview_takes_the_item_group_from_the_item(self):
		args = json.loads(self.preview_args(h.make_customer()))
		args["items"][0]["item_group"] = "All Item Groups"
		line = get_line_commission(json.dumps(args))["lines"][0]
		self.assertEqual(line["commission_percent"], 10)

	def test_preview_rejects_customer_and_company_that_are_not_text(self):
		for field in ("customer", "company"):
			args = json.loads(self.preview_args(h.make_customer()))
			args[field] = ["a", "b"]
			with self.assertRaises(frappe.ValidationError):
				get_line_commission(json.dumps(args))

	def test_preview_line_without_grant_commission_earns_nothing(self):
		args = json.loads(self.preview_args(h.make_customer()))
		args["items"][0]["grant_commission"] = 0
		line = get_line_commission(json.dumps(args))["lines"][0]
		self.assertEqual((line["commission_classification"], line["commission_amount"]), ("", 0))

	def test_preview_only_for_orders_and_invoices(self):
		for bad in (self.preview_args(h.make_customer(), "Quotation"), json.dumps([1, 2])):
			with self.assertRaises(frappe.ValidationError):
				get_line_commission(bad)

	def test_preview_checks_access_to_the_customer(self):
		allowed, other = h.make_customer(), h.make_customer()
		email = f"{frappe.generate_hash(length=8)}@commission.test"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Seller",
				"send_welcome_email": 0,
				"roles": [{"role": "Sales User"}],
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "User Permission", "user": email, "allow": "Customer", "for_value": allowed}
		).insert(ignore_permissions=True)
		frappe.set_user(email)
		self.assertTrue(get_line_commission(self.preview_args(allowed))["lines"])
		with self.assertRaises(frappe.PermissionError):
			get_line_commission(self.preview_args(other))

	def test_preview_checks_access_to_the_company(self):
		other = next(c for c in frappe.get_all("Company", pluck="name") if c != h.COMPANY)
		email = f"{frappe.generate_hash(length=8)}@commission.test"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Seller",
				"send_welcome_email": 0,
				"roles": [{"role": "Sales User"}],
			}
		).insert(ignore_permissions=True)
		permission = {"doctype": "User Permission", "user": email, "allow": "Company"}
		frappe.get_doc({**permission, "for_value": h.COMPANY}).insert(ignore_permissions=True)
		frappe.set_user(email)
		self.assertTrue(get_line_commission(self.preview_args(h.make_customer()))["lines"])
		frappe.set_user("Administrator")
		frappe.db.delete("User Permission", {"user": email})
		frappe.get_doc({**permission, "for_value": other}).insert(ignore_permissions=True)
		frappe.set_user(email)
		with self.assertRaises(frappe.PermissionError):
			get_line_commission(self.preview_args(h.make_customer()))
