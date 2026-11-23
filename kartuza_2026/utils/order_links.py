import frappe
from frappe.utils import flt

# Order types that never earn commission
SHOPPING_CART = "Shopping Cart"


class OrderLinks:
	"""Orders and order lines an invoice was made from, read once per document."""

	def __init__(self, doc):
		self.doc = doc
		self.cart_orders = self.find_cart_orders()
		self.order_lines = self.find_order_lines()

	def is_cart_doc(self):
		return self.doc.doctype == "Sales Order" and self.doc.order_type == SHOPPING_CART

	def is_cart_line(self, row):
		return row.get("sales_order") in self.cart_orders

	def order_line(self, row):
		"""Classification, %, factor and source of the order line an invoice line was made from."""
		line = self.order_lines.get(row.get("so_detail"))
		return line if line and line.commission_classification else None

	def find_cart_orders(self):
		if self.doc.doctype != "Sales Invoice":
			return set()
		orders = {r.sales_order for r in self.doc.items if r.get("sales_order")}
		if not orders:
			return set()
		return set(
			frappe.get_all(
				"Sales Order",
				filters={"name": ["in", list(orders)], "order_type": SHOPPING_CART},
				pluck="name",
			)
		)

	def find_order_lines(self):
		if self.doc.doctype != "Sales Invoice":
			return {}
		names = {r.so_detail for r in self.doc.items if r.get("so_detail")}
		if not names:
			return {}
		lines = frappe.get_all(
			"Sales Order Item",
			filters={"name": ["in", list(names)]},
			fields=[
				"name",
				"commission_classification",
				"commission_percent",
				"commission_factor",
			],
		)
		for line in lines:
			line.commission_percent = flt(line.commission_percent)
			line.commission_factor = flt(line.commission_factor)
		return {line.name: line for line in lines}
