"""Standalone credit notes settled against an invoice with the Payment Reconciliation tool."""

from collections import defaultdict

import frappe
from frappe.query_builder.functions import Abs, Max, Sum
from frappe.utils import flt, getdate

from sales_commission.utils.line_fields import plain_amount
from sales_commission.utils.original_lines import OriginalLines

NOTE_FIELDS = [
	"name",
	"creation",
	"currency",
	"party_account_currency",
	"grand_total",
	"rounded_total",
	"base_grand_total",
	"base_rounded_total",
]
LINE_FIELDS = [
	"name",
	"parent",
	"item_code",
	"item_group",
	"commission_amount",
	"base_commission_amount",
	"net_amount",
	"base_net_amount",
	"commission_percent",
	"commission_factor",
]


class Reconciliations:
	"""Credit Note Journal Entries between a standalone credit note and an invoice."""

	# The Payment Reconciliation tool makes one Journal Entry per pair, with a row against each of them.

	def __init__(self, name):
		self.name = name

	def get(self):
		journals = self.journals()
		if not journals:
			return []
		references = self.references(journals)
		invoices = self.invoice_types({n for names in references.values() for n in names})
		pairs = []
		for journal, names in references.items():
			note = [
				n
				for n in names
				if invoices.get(n) and invoices[n].is_return and not invoices[n].return_against
			]
			invoice = [n for n in names if invoices.get(n) and not invoices[n].is_return]
			if len(note) == 1 and len(invoice) == 1:
				pairs.append(frappe._dict(journal=journal, note=note[0], invoice=invoice[0]))
		return self.with_amounts(pairs)

	def journals(self):
		"""Credit Note type Journal Entries that settle this invoice or credit note."""
		ple = frappe.get_all(
			"Payment Ledger Entry",
			filters={
				"against_voucher_type": "Sales Invoice",
				"against_voucher_no": self.name,
				"voucher_type": "Journal Entry",
				"delinked": 0,
			},
			pluck="voucher_no",
		)
		if not ple:
			return []
		return frappe.get_all(
			"Journal Entry",
			filters={"name": ["in", list(set(ple))], "voucher_type": "Credit Note", "docstatus": 1},
			pluck="name",
		)

	def references(self, journals):
		rows = frappe.get_all(
			"Journal Entry Account",
			filters={"parent": ["in", journals], "reference_type": "Sales Invoice"},
			fields=["parent", "reference_name"],
			parent_doctype="Journal Entry",
		)
		names = defaultdict(set)
		for row in rows:
			names[row.parent].add(row.reference_name)
		return names

	def invoice_types(self, names):
		rows = frappe.get_all(
			"Sales Invoice",
			filters={"name": ["in", list(names)], "docstatus": 1},
			fields=["name", "is_return", "return_against"],
		)
		return {row.name: row for row in rows}

	def with_amounts(self, pairs):
		"""Amount settled (party currency) and date per credit note and invoice, journals added up."""
		if not pairs:
			return []
		ple = frappe.qb.DocType("Payment Ledger Entry")
		rows = (
			frappe.qb.from_(ple)
			.select(
				ple.voucher_no,
				ple.against_voucher_no,
				Sum(Abs(ple.amount_in_account_currency)).as_("amount"),
				Max(ple.posting_date).as_("date"),
				Max(ple.creation).as_("creation"),
			)
			.where(ple.voucher_type == "Journal Entry")
			.where(ple.voucher_no.isin([p.journal for p in pairs]))
			.where(ple.against_voucher_type == "Sales Invoice")
			.where(ple.delinked == 0)
			.groupby(ple.voucher_no, ple.against_voucher_no)
			.run(as_dict=True)
		)
		by_row = {(r.voucher_no, r.against_voucher_no): r for r in rows}
		merged = {}
		for pair in pairs:
			row = by_row.get((pair.journal, pair.invoice))
			if not row:
				continue
			total = merged.setdefault(
				(pair.note, pair.invoice),
				frappe._dict(note=pair.note, invoice=pair.invoice, amount=0, date=None, creation=None),
			)
			total.amount += flt(row.amount)
			total.date = max(filter(None, [total.date, getdate(row.date)]))
			total.creation = max(filter(None, [total.creation, row.creation]))
		return list(merged.values())


def note_total(note):
	"""Grand total of a credit note in the currency the customer pays in."""
	if note.party_account_currency and note.party_account_currency != note.currency:
		return abs(flt(note.base_rounded_total or note.base_grand_total))
	return abs(flt(note.rounded_total or note.grand_total))


def read_notes(names):
	rows = frappe.get_all(
		"Sales Invoice", filters={"name": ["in", list(names)], "docstatus": 1}, fields=NOTE_FIELDS
	)
	lines = defaultdict(list)
	for line in frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": ["in", list(names)]},
		fields=LINE_FIELDS,
		order_by="idx asc",
		parent_doctype="Sales Invoice",
	):
		lines[line.parent].append(line)
	return {row.name: frappe._dict(row, lines=lines[row.name]) for row in rows}


class ReconciledNotes:
	"""Standalone credit notes settled against an invoice, seen as returns of that invoice."""

	def __init__(self, invoice):
		self.invoice = invoice

	def get(self):
		settled = [r for r in Reconciliations(self.invoice.name).get() if r.invoice == self.invoice.name]
		if not settled:
			return []
		notes = read_notes({r.note for r in settled})
		originals = OriginalLines(self.invoice.items)
		result = []
		for entry in settled:
			note = notes.get(entry.note)
			if note and note_total(note):
				result.append(self.as_note(note, entry, originals))
		return sorted(result, key=lambda n: (n.date, n.creation))

	def as_note(self, note, entry, originals):
		share = min(entry.amount / note_total(note), 1)
		lines = {}
		for row in note.lines:
			original = originals.match(row)
			if original:
				line = lines.setdefault(
					original[0].get("name"), frappe._dict(commission=0, base_commission=0)
				)
				line.commission += plain_amount(row) * share
				line.base_commission += plain_amount(row, base=True) * share
		return frappe._dict(
			name=note.name,
			date=entry.date,
			creation=entry.creation,
			lines=lines,
			grand_total=-entry.amount,
			base_grand_total=-entry.amount,
		)


class SettledShares:
	"""Share of every line of a standalone credit note already taken back from an invoice it settles."""

	def __init__(self, note):
		self.note = note

	def get(self):
		"""{credit note line name: share between 0 and 1}"""
		settled = [r for r in Reconciliations(self.note.name).get() if r.note == self.note.name]
		if not settled:
			return {}
		invoice_lines = defaultdict(list)
		for line in frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": ["in", [r.invoice for r in settled]]},
			fields=["name", "parent", "idx", "item_code", "item_group"],
			parent_doctype="Sales Invoice",
		):
			invoice_lines[line.parent].append(line)
		total = note_total(self.note)
		shares = defaultdict(float)
		for entry in settled:
			share = min(entry.amount / total, 1) if total else 0
			originals = OriginalLines(invoice_lines[entry.invoice])
			for row in self.note.items:
				if originals.match(row):
					shares[row.name] += share
		return {name: min(share, 1) for name, share in shares.items()}
