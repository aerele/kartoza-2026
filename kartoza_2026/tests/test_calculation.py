import json

import frappe
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import add_days, flt

from kartoza_2026.tests import helpers as h
from kartoza_2026.tests import payment_helpers as p


class TestCalculation(h.CommissionTestCase):
	def setUp(self):
		super().setUp()
		# commission is only worked out with a salesperson; no rate of their own, so the item % decides
		self.person = h.make_sales_person()

	def make_order(self, customer, lines, team=None, **extra):
		return h.make_order(customer, lines, team=team or [(self.person, 100)], **extra)

	def make_invoice(self, customer, lines, team=None, **extra):
		return h.make_invoice(customer, lines, team=team or [(self.person, 100)], **extra)

	def test_shopping_cart_order_earns_nothing(self):
		group = h.make_group()
		item, customer = h.make_item(group, percent=10), h.make_customer()
		order = self.make_order(customer, [(item, 1, 3000)], order_type="Shopping Cart")
		invoice = p.invoice_from_order(order)
		p.pay(invoice, invoice.grand_total)
		for doc in (order, invoice):
			line = frappe.get_doc(doc.doctype, doc.name).items[0]
			self.assertFalse(line.commission_classification)
			self.assertEqual(flt(line.commission_amount), 0)
			self.assertEqual(h.ledger(doc.name), [])

	def test_unticked_line_is_blank_and_ignored_in_history(self):
		group = h.make_group()
		off, on = h.make_item(group, percent=10, grant=0), h.make_item(group, percent=10)
		customer = h.make_customer()
		first = self.make_order(customer, [(off, 1, 1000)])
		self.assertFalse(first.items[0].commission_classification)
		self.assertEqual(flt(first.items[0].commission_amount), 0)
		second = self.make_order(customer, [(on, 1, 1000)])
		self.assertEqual(second.items[0].commission_classification, "New")

	def test_four_bookings_in_one_group(self):
		group = h.make_group()
		customer = h.make_customer()
		seen = []
		for rate in (2760, 2760, 2824, 320):
			order = self.make_order(customer, [(h.make_item(group, percent=10), 1, rate)])
			line = order.items[0]
			seen.append((line.commission_classification, flt(line.commission_amount, 2)))
		self.assertEqual(seen, [("New", 276.0), ("Renewal", 138.0), ("Renewal", 141.2), ("Renewal", 16.0)])

	def test_same_group_lines_on_first_document_are_all_new(self):
		group = h.make_group()
		customer = h.make_customer()
		order = self.make_order(
			customer, [(h.make_item(group, percent=10), 1, 100), (h.make_item(group, percent=10), 1, 200)]
		)
		self.assertEqual([r.commission_classification for r in order.items], ["New", "New"])
		self.assertEqual(flt(order.total_commission_amount), 30)

	def test_partial_invoices_keep_the_order_percent(self):
		group = h.make_group()
		item, customer = h.make_item(group, percent=10), h.make_customer()
		order = self.make_order(customer, [(item, 10, 276)])
		first = p.invoice_from_order(order, {item: 4})
		second = p.invoice_from_order(order, {item: 6})
		for invoice, expected in ((first, 110.4), (second, 165.6)):
			line = invoice.items[0]
			self.assertEqual(line.commission_classification, "New")
			self.assertEqual(flt(line.commission_percent), 10)
			self.assertEqual(flt(line.commission_amount, 2), expected)

	def test_item_percent_beats_the_salesperson_rate(self):
		anna = h.make_sales_person(rate=8)
		item = h.make_item(h.make_group(), percent=10)
		order = self.make_order(h.make_customer(), [(item, 1, 1000)], team=[(anna, 100)])
		line = order.items[0]
		self.assertEqual(flt(line.commission_percent), 10)
		self.assertEqual(flt(line.commission_amount, 2), 100)

	def test_no_item_percent_uses_the_salesperson_rate(self):
		anna, ben = h.make_sales_person(rate=10), h.make_sales_person(rate=8)
		item = h.make_item(h.make_group())
		order = self.make_order(h.make_customer(), [(item, 1, 1000)], team=[(anna, 60), (ben, 40)])
		line = order.items[0]
		self.assertEqual(flt(line.commission_percent), 10)
		self.assertEqual(flt(line.commission_amount, 2), 100)

	def test_no_item_percent_and_no_salesperson_rate(self):
		item = h.make_item(h.make_group())
		for team in (None, [(h.make_sales_person(), 100)]):
			line = self.make_order(h.make_customer(), [(item, 1, 1000)], team=team).items[0]
			self.assertEqual((flt(line.commission_percent), flt(line.commission_amount)), (0, 0))

	def test_factor_change_affects_new_documents_only(self):
		item, customer = h.make_item(h.make_group(), percent=10), h.make_customer()
		self.make_order(customer, [(item, 1, 1000)])
		old = self.make_order(customer, [(item, 1, 1000)])
		self.assertEqual(flt(old.items[0].commission_amount, 2), 50)
		h.set_settings(factors=(1.0, 0.25))
		stored = frappe.get_doc("Sales Order", old.name).items[0]
		self.assertEqual((flt(stored.commission_factor), flt(stored.commission_amount, 2)), (0.5, 50))
		new = self.make_order(customer, [(item, 1, 1000)])
		self.assertEqual(flt(new.items[0].commission_amount, 2), 25)

	def test_missing_factor_row_earns_nothing(self):
		item = h.make_item(h.make_group(), percent=10)
		settings = frappe.get_single("Commission Settings")
		settings.set("classification_factors", [])
		settings.save()
		frappe.clear_document_cache("Commission Settings", "Commission Settings")
		line = self.make_order(h.make_customer(), [(item, 1, 1000)]).items[0]
		self.assertEqual((flt(line.commission_factor), flt(line.commission_amount)), (0, 0))

	def test_duplicate_classification_row_is_rejected(self):
		settings = frappe.get_single("Commission Settings")
		settings.append("classification_factors", {"classification": "New", "factor": 2})
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_update_items_keeps_stored_lines_and_works_out_new_ones(self):
		group = h.make_group()
		customer = h.make_customer()
		self.make_order(customer, [(h.make_item(group, percent=10), 1, 1000)])
		training, qgis = h.make_item(group, percent=10), h.make_item(group, percent=10)
		order = self.make_order(customer, [(training, 1, 2760)])
		line = order.items[0]
		self.assertEqual((line.commission_classification, flt(line.commission_amount)), ("Renewal", 138))
		frappe.db.set_value("Item", training, "commission_percent", 50)
		frappe.db.set_value("Item", qgis, "commission_percent", 100)
		changes = [
			{"docname": line.name, "item_code": training, "qty": 3, "rate": 2760},
			{"item_code": qgis, "qty": 1, "rate": 2824, "warehouse": h.WAREHOUSE},
		]
		update_child_qty_rate("Sales Order", json.dumps(changes), order.name)
		order.reload()
		first, second = order.items
		self.assertEqual((first.commission_classification, flt(first.commission_percent)), ("Renewal", 10))
		self.assertEqual(flt(first.commission_amount, 2), 414)
		self.assertEqual(second.commission_classification, "Renewal")
		self.assertEqual(flt(second.commission_percent), 100)
		self.assertEqual(flt(second.commission_amount, 2), 1412)
		self.assertEqual(flt(order.total_commission_amount, 2), 1826)

	def test_update_items_with_the_rates_unchanged(self):
		group = h.make_group()
		customer = h.make_customer()
		self.make_order(customer, [(h.make_item(group, percent=10), 1, 1000)])
		training, qgis = h.make_item(group, percent=10), h.make_item(group, percent=10)
		order = self.make_order(customer, [(training, 1, 2760)])
		changes = [
			{"docname": order.items[0].name, "item_code": training, "qty": 3, "rate": 2760},
			{"item_code": qgis, "qty": 1, "rate": 2824, "warehouse": h.WAREHOUSE},
		]
		update_child_qty_rate("Sales Order", json.dumps(changes), order.name)
		order.reload()
		self.assertEqual([flt(r.commission_amount, 2) for r in order.items], [414, 141.2])
		self.assertEqual(flt(order.total_commission_amount, 2), 555.2)

	def test_cart_order_is_not_history_for_a_normal_order(self):
		group = h.make_group()
		customer = h.make_customer()
		cart = self.make_order(
			customer, [(h.make_item(group, percent=10), 1, 1000)], order_type="Shopping Cart"
		)
		self.assertFalse(cart.items[0].commission_classification)
		normal = self.make_order(customer, [(h.make_item(group, percent=10), 1, 1000)])
		self.assertEqual(normal.items[0].commission_classification, "New")

	def test_invoice_of_a_cart_order_is_not_history(self):
		group = h.make_group()
		for make in (self.make_order, self.make_invoice):
			customer = h.make_customer()
			cart = self.make_order(
				customer, [(h.make_item(group, percent=10), 1, 1000)], order_type="Shopping Cart"
			)
			p.invoice_from_order(cart)
			later = make(customer, [(h.make_item(group, percent=10), 1, 1000)], submit=False)
			self.assertEqual(later.items[0].commission_classification, "New")

	def test_free_lines_are_not_history(self):
		group = h.make_group()
		customer = h.make_customer()
		self.make_order(customer, [(h.make_item(group, percent=10), 1, 0)])
		order = self.make_order(customer, [(h.make_item(group, percent=10), 1, 1000)])
		self.assertEqual(order.items[0].commission_classification, "New")

	def test_lines_that_earn_nothing_stay_blank(self):
		group = h.make_group()
		customer = h.make_customer()
		cart = self.make_order(
			customer, [(h.make_item(group, percent=10), 1, 1000)], order_type="Shopping Cart"
		)
		off = self.make_order(customer, [(h.make_item(group, percent=10, grant=0), 1, 1000)])
		for line in (cart.items[0], off.items[0]):
			self.assertEqual((line.commission_classification, flt(line.commission_amount)), ("", 0))
		no_rate = self.make_order(customer, [(h.make_item(h.make_group()), 1, 1000)])
		self.assertEqual(flt(no_rate.items[0].commission_amount), 0)
		paying = self.make_order(customer, [(h.make_item(group, percent=10), 1, 1000)])
		self.assertEqual(flt(paying.items[0].commission_amount), 100)

	def clear_commission(self, doc):
		"""Make a submitted document look like one made before the app was installed."""
		for row in doc.items:
			frappe.db.set_value(
				row.doctype,
				row.name,
				{
					"commission_classification": "",
					"commission_percent": 0,
					"commission_factor": 0,
					"commission_amount": 0,
					"base_commission_amount": 0,
				},
			)
		frappe.db.set_value(
			doc.doctype, doc.name, {"total_commission_amount": 0, "base_total_commission_amount": 0}
		)
		return doc.reload()

	def test_documents_made_before_the_app_never_earn(self):
		item, customer = h.make_item(h.make_group(), percent=10), h.make_customer()
		team = [(h.make_sales_person(), 100)]
		order = self.clear_commission(self.make_order(customer, [(item, 1, 1000)], team=team))
		order.delivery_date = add_days(order.delivery_date, 5)
		order.save()
		changes = [{"docname": order.items[0].name, "item_code": item, "qty": 3, "rate": 1000}]
		update_child_qty_rate("Sales Order", json.dumps(changes), order.name)
		order.reload()
		self.assertEqual(
			(order.items[0].commission_classification, flt(order.total_commission_amount)), ("", 0)
		)
		invoice = self.clear_commission(self.make_invoice(customer, [(item, 1, 1000)], team=team))
		p.pay(invoice, 1000)
		p.pay(order, 1000)
		self.assertEqual(h.ledger(invoice.name), [])
		self.assertEqual(h.ledger(order.name), [])

	def test_item_commission_percent_must_be_between_0_and_100(self):
		item = h.make_item(h.make_group(), percent=10)
		for bad in (-1, -0.01, 100.01, 250):
			doc = frappe.get_doc("Item", item)
			doc.commission_percent = bad
			with self.assertRaisesRegex(frappe.ValidationError, "must be between 0 and 100"):
				doc.save()
		for good in (0, 100):
			doc = frappe.get_doc("Item", item)
			doc.commission_percent = good
			doc.save()
			self.assertEqual(flt(frappe.db.get_value("Item", item, "commission_percent")), good)
		with self.assertRaisesRegex(frappe.ValidationError, "must be between 0 and 100"):
			h.make_item(h.make_group(), percent=-5)
