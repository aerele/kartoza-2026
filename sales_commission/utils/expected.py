import frappe
from frappe.utils import flt, getdate

from sales_commission.utils.entry import Expected
from sales_commission.utils.events import CreditNotes, PaymentSlices
from sales_commission.utils.line_fields import exact_commission
from sales_commission.utils.rates import person_ratio
from sales_commission.utils.reconciled_notes import ReconciledNotes
from sales_commission.utils.sales_team import sales_team
from sales_commission.utils.walk import LineWalk


class ExpectedEntries:
	"""What a submitted Sales Order / Sales Invoice should have earned by now, per line and payment."""

	def __init__(self, doc):
		self.doc = doc
		self.currency = doc.get("party_account_currency") or doc.currency
		# the customer pays in the party currency, which can differ from the document's
		self.use_base = self.currency != doc.currency
		self.team = sales_team(doc)
		self.pending = set()

	def build(self):
		doc = self.doc
		payments = PaymentSlices(doc)
		slices = payments.get()
		self.pending = payments.pending
		notes = self.credit_notes()
		grand_total = flt(
			(doc.base_rounded_total or doc.base_grand_total)
			if self.use_base
			else (doc.rounded_total or doc.grand_total)
		)
		entries = []
		for row in doc.items:
			line = self.line_commission(row)
			for person, allocated in self.team:
				ratio = person_ratio(row, person)
				if not line * ratio:
					continue
				context = self.line_context(row, line * ratio, grand_total)
				notes_of_line = self.notes_for_line(notes, row, ratio)
				for step in LineWalk(line * ratio, grand_total, slices, notes_of_line).run():
					entries.append(self.entry(step, context, person, allocated))
		return entries

	def credit_notes(self):
		if self.doc.doctype != "Sales Invoice":
			return []
		return [*CreditNotes(self.doc).get(), *ReconciledNotes(self.doc).get()]

	def line_commission(self, row):
		return exact_commission(row, base=self.use_base)

	def line_context(self, row, commission, grand_total):
		doc = self.doc
		# sales count without tax: the line's net amount over the grand total the customer pays in
		net = flt(row.base_net_amount if self.use_base else row.net_amount)
		share = net / grand_total if grand_total else 0
		return frappe._dict(
			company=doc.company,
			customer=doc.customer,
			sales_date=getdate(doc.get("transaction_date") or doc.get("posting_date")),
			reference_row=row.name,
			source_row=row.get("so_detail"),
			item_code=row.item_code,
			item_group=row.item_group,
			classification=row.commission_classification,
			commission_percent=row.commission_percent,
			line_commission=commission,
			currency=self.currency,
			line_share=share,
		)

	def notes_for_line(self, notes, row, ratio):
		return [
			frappe._dict(
				name=n.name,
				date=n.date,
				creation=n.creation,
				note_commission=self.note_commission(n, row) * ratio,
				note_grand_total=n.base_grand_total if self.use_base else n.grand_total,
			)
			for n in notes
		]

	def note_commission(self, note, row):
		line = note.lines.get(row.name)
		if not line:
			return 0
		return line.base_commission if self.use_base else line.commission

	def entry(self, step, context, person, allocated):
		entry = Expected(context)
		entry.sales_person = person
		entry.employee = person and frappe.get_cached_value("Sales Person", person, "employee")
		entry.allocated = allocated
		if step.kind == "earned":
			self.fill_earned(entry, step, allocated)
		else:
			self.fill_deduction(entry, step, allocated)
		return entry

	def fill_earned(self, entry, step, allocated):
		slice_ = step.slice
		entry.update(
			payment_doctype=slice_.payment_doctype,
			payment_name=slice_.payment_name,
			credit_note=None,
			payment_date=slice_.payment_date,
			date=slice_.date,
			at=(slice_.date, slice_.creation),
			rate=slice_.rate,
			advance=slice_.advance,
			adjusted_on=slice_.adjusted_on,
			earned=step.earned * allocated / 100,
			paid=step.paid * allocated / 100,
			counted=step.counted * allocated / 100,
			parts=[self.part(part, allocated) for part in step.parts],
		)

	def part(self, part, allocated):
		return frappe._dict(
			part,
			earned=part.earned * allocated / 100,
			paid=part.paid * allocated / 100,
			counted=part.counted * allocated / 100,
		)

	def before_shares(self, before):
		"""{payment: (share of its commission, share of its sales)} earned before the credit note."""
		shares = {}
		for entry, earned, counted in before:
			key = (entry.slice.payment_doctype, entry.slice.payment_name)
			shares[key] = (
				earned / entry.earned if entry.earned else 1,
				counted / entry.counted if entry.counted else 1,
			)
		return shares

	def fill_deduction(self, entry, step, allocated):
		note = step.note
		entry.update(
			payment_doctype=None,
			payment_name=None,
			credit_note=note.name,
			sales_date=note.date,
			payment_date=None,
			date=note.date,
			at=(note.date, note.creation),
			deduction=step.deduction * allocated / 100,
			ratio=step.ratio,
			before=self.before_shares(step.before),
		)
