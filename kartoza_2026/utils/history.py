import frappe
from frappe.utils import get_datetime, now_datetime

from kartoza_2026.utils.order_links import SHOPPING_CART

EARLIER_GROUPS_SQL = """
	select line.item_group
	from `tabSales Order Item` line
	join `tabSales Order` so on so.name = line.parent
	where so.docstatus = 1
		and so.customer = %(customer)s
		and so.company = %(company)s
		and ifnull(so.order_type, '') != %(cart)s
		and line.grant_commission = 1
		and line.net_amount > 0
		and line.item_group in %(groups)s
		and so.name not in %(excluded)s
		and (so.transaction_date < %(date)s
			or (so.transaction_date = %(date)s and so.creation < %(creation)s))
	union
	select line.item_group
	from `tabSales Invoice Item` line
	join `tabSales Invoice` si on si.name = line.parent
	left join `tabSales Order` origin on origin.name = line.sales_order
	where si.docstatus = 1
		and si.is_return = 0
		and si.customer = %(customer)s
		and si.company = %(company)s
		and ifnull(origin.order_type, '') != %(cart)s
		and line.grant_commission = 1
		and line.net_amount > 0
		and line.item_group in %(groups)s
		and si.name != %(name)s
		and (si.posting_date < %(date)s
			or (si.posting_date = %(date)s and si.creation < %(creation)s))
"""


class SalesHistory:
	"""Finds which Item Groups a customer bought before in this company."""

	def __init__(self, doc):
		self.doc = doc

	def earlier_groups(self, item_groups):
		"""The given Item Groups that have an earlier qualifying sale, in one query."""
		groups = {g for g in item_groups if g}
		if not groups:
			return set()
		doc = self.doc
		# an invoice never counts the orders it was made from
		excluded = {r.sales_order for r in doc.items if r.get("sales_order")} | {doc.name or ""}
		rows = frappe.db.sql(
			EARLIER_GROUPS_SQL,
			{
				"customer": doc.customer,
				"company": doc.company,
				"cart": SHOPPING_CART,
				"groups": tuple(groups),
				"excluded": tuple(excluded),
				"name": doc.name or "",
				"date": doc.get("transaction_date") or doc.get("posting_date"),
				"creation": get_datetime(doc.get("creation") or now_datetime()),
			},
		)
		return {row[0] for row in rows}
