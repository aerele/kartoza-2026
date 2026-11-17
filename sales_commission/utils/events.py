"""Payments and credit notes of a Sales Order / Sales Invoice, read from the ledgers."""

import frappe
from frappe.query_builder.functions import Abs, Max, Sum
from frappe.utils import flt, getdate

from sales_commission.utils.original_lines import OriginalLines

# Only Payment Entries count as payments (POS, write-off, loyalty and Journal Entries do not)
PAYMENT_VOUCHER = "Payment Entry"


class PaymentSlices:
	"""One slice per Payment Entry still paying the document, read from the two payment ledgers only."""

	def __init__(self, doc):
		self.doc = doc
		self.pending = set()

	def get(self):
		rows = self.order_rows() if self.doc.doctype == "Sales Order" else self.invoice_rows()
		vouchers = {(r.voucher_type, r.voucher_no) for r in rows}
		self.details = self.voucher_details(vouchers)
		# ERPNext writes the Advance Payment Ledger Entry before the payment's Payment Ledger Entry, and
		# deletes the payment's rows while it moves an advance. A payment without its rows is pending:
		# it earns nothing yet and its ledger keys stay as they are until the next sync
		self.pending = {self.key(r) for r in rows if self.key(r) not in self.details}
		rows = [r for r in rows if self.key(r) in self.details]
		self.advances = self.advance_vouchers(set(self.details))
		if self.doc.doctype == "Sales Order":
			return self.order_slices(rows)
		return self.build(rows, lambda r: -flt(r.amount_in_account_currency), lambda r: r.posting_date)

	def invoice_rows(self):
		return frappe.get_all(
			"Payment Ledger Entry",
			filters={
				"against_voucher_type": "Sales Invoice",
				"against_voucher_no": self.doc.name,
				"delinked": 0,
				"voucher_type": PAYMENT_VOUCHER,
			},
			fields=["voucher_type", "voucher_no", "posting_date", "amount_in_account_currency", "creation"],
			order_by="creation asc",
		)

	def order_rows(self):
		return frappe.get_all(
			"Advance Payment Ledger Entry",
			filters={
				"against_voucher_type": "Sales Order",
				"against_voucher_no": self.doc.name,
				"delinked": 0,
				"voucher_type": PAYMENT_VOUCHER,
			},
			fields=["voucher_type", "voucher_no", "amount", "event", "creation"],
			order_by="creation asc",
		)

	def order_slices(self, rows):
		# an Adjustment row is the part of the advance that now pays an invoice
		slices = self.build(
			rows,
			lambda r: -flt(r.amount) if r.event == "Submit" else 0,
			lambda r: self.details[self.key(r)].date,
		)
		for slice_ in slices:
			adjusted = [r.creation for r in rows if self.key(r) == slice_.key and r.event == "Adjustment"]
			slice_.adjusted_on = getdate(max(adjusted)) if adjusted else None
		return slices

	def build(self, rows, amount_of, date_of):
		grouped = {}
		for row in rows:
			key = self.key(row)
			slice_ = grouped.setdefault(
				key,
				frappe._dict(
					key=key,
					payment_doctype=row.voucher_type,
					payment_name=row.voucher_no,
					date=getdate(date_of(row)),
					payment_date=getdate(self.details[key].date),
					creation=row.creation,
					paid=0,
					rate=self.details[key].rate,
					advance=key in self.advances,
					adjusted_on=None,
				),
			)
			slice_.paid += amount_of(row)
		return sorted(grouped.values(), key=lambda s: (s.date, s.creation))

	def key(self, row):
		return (row.voucher_type, row.voucher_no)

	def voucher_details(self, vouchers):
		"""Rate (company currency per payment currency) and date of every payment with ledger rows, in one query."""
		if not vouchers:
			return {}
		ple = frappe.qb.DocType("Payment Ledger Entry")
		rows = (
			frappe.qb.from_(ple)
			.select(
				ple.voucher_type,
				ple.voucher_no,
				Max(ple.posting_date).as_("date"),
				Sum(Abs(ple.amount)).as_("base"),
				Sum(Abs(ple.amount_in_account_currency)).as_("account"),
			)
			.where(ple.voucher_type == PAYMENT_VOUCHER)
			.where(ple.voucher_no.isin([no for _type, no in vouchers]))
			.where(ple.party_type == "Customer")
			.where(ple.amount_in_account_currency != 0)
			.groupby(ple.voucher_type, ple.voucher_no)
			.run(as_dict=True)
		)
		return {
			(r.voucher_type, r.voucher_no): frappe._dict(rate=flt(r.base / r.account, 9), date=r.date)
			for r in rows
		}

	def advance_vouchers(self, vouchers):
		"""Payments that were first made against a Sales Order, when the document is an invoice."""
		if self.doc.doctype != "Sales Invoice" or not vouchers:
			return set()
		advance = frappe.qb.DocType("Advance Payment Ledger Entry")
		rows = (
			frappe.qb.from_(advance)
			.select(advance.voucher_type, advance.voucher_no)
			.distinct()
			.where(advance.voucher_type == PAYMENT_VOUCHER)
			.where(advance.against_voucher_type == "Sales Order")
			.where(advance.delinked == 0)
			.where(advance.voucher_no.isin([no for _type, no in vouchers]))
			.run()
		)
		return {(voucher_type, voucher_no) for voucher_type, voucher_no in rows} & vouchers


class CreditNotes:
	"""Submitted returns against an invoice, with their commission per original line."""

	NOTE_FIELDS = (
		"name",
		"posting_date",
		"creation",
		"override_deduction",
		"grand_total",
		"rounded_total",
		"base_grand_total",
		"base_rounded_total",
	)

	def __init__(self, invoice):
		self.invoice = invoice
		self.originals = OriginalLines(invoice.items)

	def get(self):
		notes = {}
		for row in self.read_rows():
			note = notes.get(row.name) or notes.setdefault(row.name, self.new_note(row))
			original = self.originals.match(row)
			if original:
				line = note.lines.setdefault(
					original[0].get("name"), frappe._dict(commission=0, base_commission=0)
				)
				line.commission += flt(row.commission_amount)
				line.base_commission += flt(row.base_commission_amount)
		return sorted(notes.values(), key=lambda n: (n.date, n.creation))

	def read_rows(self):
		si = frappe.qb.DocType("Sales Invoice")
		item = frappe.qb.DocType("Sales Invoice Item")
		query = frappe.qb.from_(si).join(item).on(item.parent == si.name)
		query = query.select(
			*[getattr(si, name) for name in self.NOTE_FIELDS],
			item.item_code,
			item.item_group,
			item.sales_invoice_item,
			item.commission_amount,
			item.base_commission_amount,
		)
		return (
			query.where(si.return_against == self.invoice.name)
			.where(si.docstatus == 1)
			.where(si.is_return == 1)
			.run(as_dict=True)
		)

	def new_note(self, row):
		return frappe._dict(
			name=row.name,
			date=getdate(row.posting_date),
			creation=row.creation,
			override=bool(row.override_deduction),
			lines={},
			grand_total=flt(row.rounded_total or row.grand_total),
			base_grand_total=flt(row.base_rounded_total or row.base_grand_total),
		)
