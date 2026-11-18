import frappe

DEDUCTION_TYPES = ("Credit Note Deduction", "Reversal")


def stamp_refund_on_payment(doc, method=None):
	"""Payment Entry on_submit / on_cancel: a refund paid against a credit note is shown on its rows."""
	if doc.payment_type != "Pay":
		return
	notes = {r.reference_name for r in doc.get("references") or [] if r.reference_doctype == "Sales Invoice"}
	stamp_refunds(notes)


def stamp_refund_on_journal(doc, method=None):
	"""Journal Entry on_submit / on_cancel: a credit note settled by a journal is shown on its rows."""
	notes = {r.reference_name for r in doc.get("accounts") or [] if r.reference_type == "Sales Invoice"}
	stamp_refunds(notes)


def stamp_refunds(credit_notes):
	"""Sets the latest refund of each credit note on its ledger rows. A failure is logged and undone."""
	savepoint = "commission_refund"
	frappe.db.savepoint(savepoint)
	try:
		for note in credit_notes:
			stamp_note(note)
		frappe.db.release_savepoint(savepoint)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title="Commission refund stamp failed")


def stamp_note(note):
	"""The ledger rows of a credit note get its latest submitted refund, or none when there is none."""
	rows = frappe.get_all(
		"Commission Ledger",
		filters={"credit_note": note, "is_cancelled": 0, "entry_type": ["in", DEDUCTION_TYPES]},
		fields=["name", "payment_doctype", "payment_name", "payment_date"],
	)
	if not rows:
		return
	doctype, name, date = latest_refund(note)
	for row in rows:
		if (row.payment_doctype or None, row.payment_name or None, row.payment_date) != (doctype, name, date):
			frappe.db.set_value(
				"Commission Ledger",
				row.name,
				{"payment_doctype": doctype, "payment_name": name, "payment_date": date},
				update_modified=False,
			)


def latest_refund(note):
	"""(doctype, name, posting date) of the last submitted refund Payment Entry or Journal Entry of a note."""
	entry = frappe.qb.DocType("Payment Entry")
	reference = frappe.qb.DocType("Payment Entry Reference")
	payments = (
		frappe.qb.from_(entry)
		.join(reference)
		.on(reference.parent == entry.name)
		.select(entry.name, entry.posting_date, entry.creation)
		.where(
			(entry.docstatus == 1)
			& (entry.payment_type == "Pay")
			& (reference.reference_doctype == "Sales Invoice")
			& (reference.reference_name == note)
		)
		.run()
	)
	journal = frappe.qb.DocType("Journal Entry")
	account = frappe.qb.DocType("Journal Entry Account")
	journals = (
		frappe.qb.from_(journal)
		.join(account)
		.on(account.parent == journal.name)
		.select(journal.name, journal.posting_date, journal.creation)
		.where(
			(journal.docstatus == 1)
			& (account.reference_type == "Sales Invoice")
			& (account.reference_name == note)
		)
		.run()
	)
	found = [("Payment Entry", *r) for r in payments] + [("Journal Entry", *r) for r in journals]
	if not found:
		return None, None, None
	doctype, name, date, _creation = max(found, key=lambda r: (r[2], r[3]))
	return doctype, name, date
