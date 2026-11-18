import frappe
from frappe.utils import flt

from sales_commission.utils.expected import ExpectedEntries
from sales_commission.utils.line_fields import has_stored_commission
from sales_commission.utils.order_links import SHOPPING_CART
from sales_commission.utils.poster import LedgerPoster
from sales_commission.utils.refunds import stamp_refunds
from sales_commission.utils.standalone import StandaloneNoteEntries

SYNCED_DOCTYPES = ("Sales Order", "Sales Invoice")


def sync_on_change(doc, method=None):
	"""Sales Order / Sales Invoice on_change: refresh the commission ledger in the same request."""
	if doc.docstatus == 0:
		return
	if doc.doctype == "Sales Invoice" and doc.is_return:
		if doc.return_against and (
			has_commission(doc) or has_ledger_rows("Sales Invoice", doc.return_against)
		):
			sync_document("Sales Invoice", doc.return_against)
		if has_commission(doc) or has_ledger_rows("Sales Invoice", doc.name):
			sync_document("Sales Invoice", doc.name, doc)
	elif has_commission(doc) or has_ledger_rows(doc.doctype, doc.name):
		sync_document(doc.doctype, doc.name, doc)
		if doc.doctype == "Sales Invoice":
			# an advance reconciled later moves off the order without any event on the order
			sync_on_invoice(doc)


def sync_on_advance(doc, method=None):
	"""Advance Payment Ledger Entry on_update: an advance moved or changed on a Sales Order."""
	if doc.against_voucher_type == "Sales Order" and has_ledger_rows("Sales Order", doc.against_voucher_no):
		sync_document("Sales Order", doc.against_voucher_no)


def sync_on_payment(doc, method=None):
	"""Payment Entry on_submit / on_cancel: all its ledger rows exist now, so the commission is final."""
	for row in doc.get("references") or []:
		if row.reference_doctype in SYNCED_DOCTYPES:
			sync_named(row.reference_doctype, row.reference_name)


def sync_on_invoice(doc, method=None):
	"""Sales Invoice on_submit / on_cancel: advances moved between its orders and the invoice."""
	for order in {row.sales_order for row in doc.items if row.get("sales_order")}:
		sync_named("Sales Order", order)


def sync_on_reconciliation(doc, method=None):
	"""Journal Entry (Credit Note type) on_submit / on_cancel: a credit note settled against an invoice."""
	if doc.voucher_type != "Credit Note":
		return
	for row in doc.get("accounts") or []:
		if row.reference_type == "Sales Invoice":
			sync_named("Sales Invoice", row.reference_name)


def sync_named(doctype, name):
	if flt(frappe.db.get_value(doctype, name, "total_commission_amount")) or has_ledger_rows(doctype, name):
		sync_document(doctype, name)


def has_commission(doc):
	return doc.total_commission_amount or 0


def has_ledger_rows(doctype, name):
	return bool(frappe.db.exists("Commission Ledger", {"reference_doctype": doctype, "reference_name": name}))


def sync_document(doctype, name, doc=None):
	"""Post what the document has earned now. A failure is logged and undone, so the save goes on."""
	savepoint = "commission_sync"
	frappe.db.savepoint(savepoint)
	try:
		DocumentSync(doctype, name, doc).run()
		frappe.db.release_savepoint(savepoint)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=f"Commission sync failed for {doctype} {name}")


class DocumentSync:
	def __init__(self, doctype, name, doc=None):
		self.doctype = doctype
		self.name = name
		self.doc = doc

	def run(self):
		doc = self.doc or frappe.get_doc(self.doctype, self.name)
		if doc.docstatus == 0 or self.is_cart(doc) or not has_stored_commission(doc):
			return
		poster = LedgerPoster(self.doctype, self.name, reference_cancelled=doc.docstatus == 2)
		if doc.docstatus != 1:
			poster.post([])
			return
		entries = StandaloneNoteEntries(doc) if doc.get("is_return") else ExpectedEntries(doc)
		poster.post(entries.build(), entries.pending)
		stamp_refunds(poster.ledger.credit_notes())

	def is_cart(self, doc):
		return doc.doctype == "Sales Order" and doc.order_type == SHOPPING_CART
