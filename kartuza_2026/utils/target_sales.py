import json

import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.query_builder.functions import Coalesce, Sum
from frappe.utils import flt, getdate, now_datetime

from kartuza_2026.utils.escalator import annual_target, split_at_target

SPLIT_FIELD = "commission_target_split"
NO_SPLIT = [(1.0, 1.0)]


def freeze_split(doc, method=None):
	"""Sales Order / Sales Invoice before_submit: keep the x1 / escalator split of each salesperson."""
	if doc.get("is_return"):
		return
	people = dict.fromkeys(r.sales_person for r in doc.get("sales_team") or [] if r.sales_person)
	doc.set(SPLIT_FIELD, json.dumps({person: document_split(doc, person) for person in people}))


def stored_split(doc, sales_person):
	"""The split frozen on a submitted document, or None when it has none (submitted before the field)."""
	return read_split(doc.get(SPLIT_FIELD), sales_person)


def order_split(sales_order, sales_person):
	"""The split of a Sales Order, which its invoice lines and advances follow."""
	split = read_split(frappe.db.get_value("Sales Order", sales_order, SPLIT_FIELD), sales_person)
	if split is None:
		split = document_split(frappe.get_doc("Sales Order", sales_order), sales_person)
	return split


def read_split(value, sales_person):
	if not value:
		return None
	parts = json.loads(value).get(sales_person)
	if not parts:
		return NO_SPLIT
	return [(flt(multiplier), flt(share)) for multiplier, share in parts]


def document_split(doc, sales_person):
	"""(multiplier, share) of the document's sales: within the annual target at 1, above it at the escalator."""
	year = fiscal_year(doc)
	if not year or not sales_person:
		return NO_SPLIT
	target = annual_target(sales_person, year[0])
	multiplier = flt(frappe.get_cached_doc("Commission Settings").escalator_multiplier) or 1.0
	if not target or multiplier == 1:
		return NO_SPLIT
	before = sales_before(doc, sales_person, year)
	parts = split_at_target(before, document_sales(doc, sales_person), target, multiplier)
	total = sum(amount for _m, amount in parts)
	if total <= 0:
		return [(parts[0][0], 1.0)]
	return [(m, amount / total) for m, amount in parts]


def document_sales(doc, sales_person):
	"""Net sales in company currency the document counts towards the person's annual target."""
	percent = sum(
		flt(r.allocated_percentage) for r in doc.get("sales_team") or [] if r.sales_person == sales_person
	)
	lines = doc.get("items") or []
	if doc.get("is_return") and not credit_notes_count():
		lines = []
	elif doc.doctype == "Sales Invoice" and not doc.get("is_return"):
		# invoice lines made from an order are counted on that order
		lines = [r for r in lines if not r.get("sales_order")]
	amount = sum(flt(r.get("base_net_amount")) for r in lines)
	return amount * percent / 100


def sales_before(doc, sales_person, year):
	"""Net sales of the person's submitted orders and invoices dated before the document in its fiscal year."""
	date = getdate(document_date(doc))
	creation = doc.get("creation") or now_datetime()
	window = (year[1], date, creation, doc.name or "")
	total = order_sales(sales_person, window) + invoice_sales(sales_person, window)
	if credit_notes_count():
		total += return_sales(sales_person, window)
	return flt(total, 2)


def order_sales(sales_person, window):
	order, team = frappe.qb.DocType("Sales Order"), frappe.qb.DocType("Sales Team")
	query = (
		frappe.qb.from_(order)
		.join(team)
		.on((team.parent == order.name) & (team.parenttype == "Sales Order"))
		.select(Sum(order.base_net_total * team.allocated_percentage / 100))
		.where(order.docstatus == 1)
		.where(team.sales_person == sales_person)
	)
	return total_of(earlier(query, order, order.transaction_date, window))


def invoice_sales(sales_person, window):
	invoice, item = frappe.qb.DocType("Sales Invoice"), frappe.qb.DocType("Sales Invoice Item")
	team = frappe.qb.DocType("Sales Team")
	query = (
		frappe.qb.from_(invoice)
		.join(item)
		.on(item.parent == invoice.name)
		.join(team)
		.on((team.parent == invoice.name) & (team.parenttype == "Sales Invoice"))
		.select(Sum(item.base_net_amount * team.allocated_percentage / 100))
		.where(invoice.docstatus == 1)
		.where(invoice.is_return == 0)
		.where(Coalesce(item.sales_order, "") == "")
		.where(team.sales_person == sales_person)
	)
	return total_of(earlier(query, invoice, invoice.posting_date, window))


def return_sales(sales_person, window):
	invoice, team = frappe.qb.DocType("Sales Invoice"), frappe.qb.DocType("Sales Team")
	query = (
		frappe.qb.from_(invoice)
		.join(team)
		.on((team.parent == invoice.name) & (team.parenttype == "Sales Invoice"))
		.select(Sum(invoice.base_net_total * team.allocated_percentage / 100))
		.where(invoice.docstatus == 1)
		.where(invoice.is_return == 1)
		.where(team.sales_person == sales_person)
	)
	return total_of(earlier(query, invoice, invoice.posting_date, window))


def earlier(query, table, date_field, window):
	"""Documents of the fiscal year dated before the document, or on its date and created before it."""
	start, date, creation, name = window
	return (
		query.where(date_field >= start)
		.where((date_field < date) | ((date_field == date) & (table.creation < creation)))
		.where(table.name != name)
	)


def total_of(query):
	result = query.run()
	return flt(result[0][0] if result else 0)


def fiscal_year(doc):
	"""(name, start date) of the document's fiscal year, or None when its date has none."""
	try:
		year = get_fiscal_year(getdate(document_date(doc)), company=doc.get("company"))
	except frappe.ValidationError:
		return None
	return (year[0], year[1])


def document_date(doc):
	return doc.get("transaction_date") or doc.get("posting_date")


def credit_notes_count():
	return bool(frappe.get_cached_doc("Commission Settings").include_commission_for_credit_note)


class DocumentSplits:
	"""The split a document's payments follow: its own, or its order's for invoice lines made from one."""

	def __init__(self, doctype, name):
		self.doctype = doctype
		self.name = name
		self.doc = None
		self.cache = {}

	def shares(self, entry):
		if not entry.sales_person:
			return NO_SPLIT
		order = self.order_of(entry)
		key = (order, entry.sales_person)
		if key not in self.cache:
			self.cache[key] = (
				order_split(order, entry.sales_person) if order else self.own(entry.sales_person)
			)
		return self.cache[key]

	def own(self, sales_person):
		if self.doc is None:
			self.doc = frappe.get_doc(self.doctype, self.name)
		split = stored_split(self.doc, sales_person)
		return document_split(self.doc, sales_person) if split is None else split

	def order_of(self, entry):
		if self.doctype != "Sales Invoice" or not entry.get("source_row"):
			return None
		return frappe.db.get_value("Sales Order Item", entry.source_row, "parent")
