import frappe
from frappe.utils import getdate, now_datetime

from kartoza_2026.utils.deduction_poster import DeductionPoster
from kartoza_2026.utils.earned_poster import EarnedPoster
from kartoza_2026.utils.entry import CARRIED_FIELDS, Expected
from kartoza_2026.utils.escalator import Escalator
from kartoza_2026.utils.ledger_rows import LedgerRows


class LedgerPoster:
	"""Compares expected amounts with the Commission Ledger of a document and posts the differences."""

	def __init__(self, doctype, name, reference_cancelled=False):
		self.reference_cancelled = reference_cancelled
		self.ledger = LedgerRows(doctype, name)
		settings = frappe.get_cached_doc("Commission Settings")
		args = (doctype, name, self.ledger, Escalator(), settings)
		self.earned = EarnedPoster(*args)
		self.deductions = DeductionPoster(*args)
		self.docstatus_cache = {}

	def post(self, expected, pending=()):
		"""pending: payments whose ledger rows are not written yet, their keys are left alone."""
		entries = self.add_vanished_keys(expected, pending)
		earned = sorted((e for e in entries if not e.credit_note), key=lambda e: e.at)
		deductions = sorted((e for e in entries if e.credit_note), key=lambda e: e.at)
		for entry in earned:
			self.settle(self.earned, entry)
		for entry in deductions:
			self.settle(self.deductions, entry)

	def settle(self, poster, entry):
		if entry.get("cancelled"):
			poster.cancel_key(entry)
		else:
			poster.settle(entry)

	def add_vanished_keys(self, expected, pending):
		"""Keys that have ledger rows but nothing expected now are brought to zero."""
		known = {e.key for e in expected}
		result = list(expected)
		for key in self.ledger.keys():
			row = self.ledger.first_row(key) if key not in known else None
			if not row or (not row.credit_note and (row.payment_doctype, row.payment_name) in pending):
				continue
			probe = Expected({f: row.get(f) for f in CARRIED_FIELDS}, allocated=row.allocated_percentage)
			probe.update(
				earned=0,
				paid=0,
				deduction=0,
				at=(getdate(), now_datetime()),
				date=getdate(),
				cancelled=self.is_cancelled_cause(probe),
			)
			result.append(probe)
		return result

	def is_cancelled_cause(self, probe):
		"""The key vanished because its payment, credit note or the document itself was cancelled."""
		return (
			self.reference_cancelled
			or (not probe.credit_note and self.docstatus(probe.payment_doctype, probe.payment_name) == 2)
			or self.docstatus("Sales Invoice", probe.credit_note) == 2
		)

	def docstatus(self, doctype, name):
		if not name:
			return None
		if (doctype, name) not in self.docstatus_cache:
			self.docstatus_cache[(doctype, name)] = frappe.db.get_value(doctype, name, "docstatus")
		return self.docstatus_cache[(doctype, name)]
