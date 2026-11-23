"""Invoicing, payments and returns of the commission flow tests."""

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from frappe.utils import flt

from kartoza_2026.tests.helpers import ABBR, ANCHOR, BANK, COMPANY, make_receivable, unique


def make_bank():
	name = f"Test Bank - {ABBR}"
	if not frappe.db.exists("Account", name):
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "Test Bank",
				"parent_account": BANK,
				"company": COMPANY,
				"account_type": "Bank",
				"account_currency": "ZAR",
			}
		).insert(ignore_permissions=True)
	return name


def pay(doc, amount, date=None, rate=None):
	"""Payment Entry against a Sales Invoice or Sales Order. rate: payment currency to ZAR."""
	entry = get_payment_entry(doc.doctype, doc.name, party_amount=amount, bank_account=make_bank())
	entry.posting_date = date or ANCHOR
	entry.reference_no = unique("REF")
	entry.reference_date = entry.posting_date
	if rate:
		entry.source_exchange_rate = rate
		entry.target_exchange_rate = 1
		entry.paid_amount = amount
		entry.received_amount = flt(amount * rate, 2)
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry


def pay_many(allocations, date=None):
	"""One ZAR Payment Entry paying several invoices of one customer. allocations: [(invoice, amount)]."""
	first, amount = allocations[0]
	entry = get_payment_entry(first.doctype, first.name, party_amount=amount, bank_account=make_bank())
	for invoice, part in allocations[1:]:
		entry.append(
			"references",
			{
				"reference_doctype": invoice.doctype,
				"reference_name": invoice.name,
				"total_amount": invoice.grand_total,
				"outstanding_amount": invoice.outstanding_amount,
				"allocated_amount": part,
				"exchange_rate": 1,
			},
		)
	entry.paid_amount = entry.received_amount = sum(part for _invoice, part in allocations)
	entry.posting_date = date or ANCHOR
	entry.reference_no = unique("REF")
	entry.reference_date = entry.posting_date
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry


def unallocated_payment(customer, amount, currency="ZAR", rate=1, date=None):
	"""A customer payment that is not against any invoice or order."""
	entry = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"company": COMPANY,
			"posting_date": date or ANCHOR,
			"party_type": "Customer",
			"party": customer,
			"paid_from": receivable_of(currency),
			"paid_to": make_bank(),
			"paid_from_account_currency": currency,
			"paid_to_account_currency": "ZAR",
			"paid_amount": amount,
			"received_amount": flt(amount * rate, 2),
			"source_exchange_rate": rate,
			"target_exchange_rate": 1,
			"reference_no": unique("REF"),
			"reference_date": date or ANCHOR,
		}
	)
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry


def receivable_of(currency):
	return f"Debtors - {ABBR}" if currency == "ZAR" else make_receivable(currency)


def journal_payment(invoice, amount, date=None):
	"""A Bank Entry Journal Entry that settles an invoice, which is not a payment for commission."""
	journal = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Bank Entry",
			"company": COMPANY,
			"posting_date": date or ANCHOR,
			"cheque_no": unique("CHQ"),
			"cheque_date": date or ANCHOR,
			"accounts": [
				{"account": make_bank(), "debit_in_account_currency": amount},
				{
					"account": invoice.debit_to,
					"party_type": "Customer",
					"party": invoice.customer,
					"credit_in_account_currency": amount,
					"reference_type": "Sales Invoice",
					"reference_name": invoice.name,
				},
			],
		}
	)
	journal.insert(ignore_permissions=True)
	journal.submit()
	return journal


def reconcile(customer, currency="ZAR"):
	"""Match every open payment and credit note of the customer with its open invoices."""
	tool = frappe.get_doc("Payment Reconciliation")
	tool.company = COMPANY
	tool.party_type = "Customer"
	tool.party = customer
	tool.receivable_payable_account = receivable_of(currency)
	tool.get_unreconciled_entries()
	tool.allocate_entries(
		frappe._dict(
			{
				"invoices": [row.as_dict() for row in tool.invoices],
				"payments": [row.as_dict() for row in tool.payments],
			}
		)
	)
	tool.reconcile()


def invoice_from_order(order, quantities=None, date=None, submit=True):
	"""Invoice an order the way the Create button does. quantities: {item code: qty}."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(order.name)
	invoice.posting_date = date or ANCHOR
	invoice.set_posting_time = 1
	invoice.due_date = invoice.posting_date
	if quantities:
		invoice.items = [row for row in invoice.items if row.item_code in quantities]
		for row in invoice.items:
			row.qty = quantities[row.item_code]
	invoice.insert(ignore_permissions=True)
	if submit:
		invoice.submit()
	return invoice


def refund(note, amount=None, date=None):
	"""A Pay Payment Entry that refunds a credit note, all of it or the amount."""
	entry = get_payment_entry(
		"Sales Invoice", note.name, party_amount=-abs(amount) if amount else None, bank_account=make_bank()
	)
	entry.posting_date = date or ANCHOR
	entry.reference_no = unique("REF")
	entry.reference_date = entry.posting_date
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry


def make_credit_note(invoice, lines=None, date=None, submit=True):
	"""Return against an invoice. lines: {row index: qty to return}, default all."""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

	note = make_sales_return(invoice.name)
	note.posting_date = date or ANCHOR
	note.set_posting_time = 1
	if lines:
		note.items = [row for i, row in enumerate(note.items) if i in lines]
		for i, row in zip(sorted(lines), note.items, strict=True):
			row.qty = -lines[i]
	note.insert(ignore_permissions=True)
	if submit:
		note.submit()
	return note
