"""Extra builders for the scenario tests (reuse helpers.py, payment_helpers.py, payroll_helpers.py)."""

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from frappe.utils import flt, nowdate

from sales_commission.tests import helpers as h
from sales_commission.tests.payment_helpers import make_bank


def person_total(doc_name, person=None, **filters):
	"""Sum of live commission amounts of a document, optionally for one sales person."""
	if person:
		filters["sales_person"] = person
	return flt(sum(r.commission_amount for r in h.ledger(doc_name, **filters)), 2)


def make_person_for_employee(employee, rate=None):
	"""A second Sales Person linked to an existing Employee."""
	return (
		frappe.get_doc(
			{
				"doctype": "Sales Person",
				"sales_person_name": h.unique("SP"),
				"parent_sales_person": "Sales Team",
				"is_group": 0,
				"employee": employee,
				"commission_rate": str(rate) if rate is not None else "",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def overpay(invoice, allocated, paid, date=None):
	"""A Payment Entry that pays more than it allocates to the invoice (the rest stays unallocated)."""
	entry = get_payment_entry(invoice.doctype, invoice.name, party_amount=allocated, bank_account=make_bank())
	entry.posting_date = date or nowdate()
	entry.reference_no = h.unique("REF")
	entry.reference_date = entry.posting_date
	entry.paid_amount = entry.received_amount = paid
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry


def change_team(doc, team):
	"""Replace the Sales Team of a submitted order / invoice (the table is editable after submit)."""
	doc.reload()
	doc.set("sales_team", [])
	for person, pct in team:
		doc.append("sales_team", {"sales_person": person, "allocated_percentage": pct})
	doc.save()
	return doc
