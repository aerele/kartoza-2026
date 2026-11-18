import frappe
from frappe.utils import flt, getdate

from sales_commission.utils.entry import Expected
from sales_commission.utils.line_fields import plain_amount
from sales_commission.utils.original_lines import OriginalLines
from sales_commission.utils.rates import person_ratio
from sales_commission.utils.reconciled_notes import SettledShares
from sales_commission.utils.sales_team import sales_team


class StandaloneNoteEntries:
	"""A credit note takes back its own line commission when no original line holds it."""

	# That is every line of a note without an original invoice and the lines that match nothing on it.
	# The part settled against an invoice with the Payment Reconciliation tool is taken back from that
	# invoice instead (see ReconciledNotes), so it is not taken back twice.

	def __init__(self, note):
		self.note = note
		self.team = sales_team(note)
		self.pending = set()

	def build(self):
		originals = OriginalLines.for_note(self.note)
		settled = SettledShares(self.note).get()
		entries = []
		for row in self.note.items:
			if originals.match(row) or not flt(row.commission_amount):
				continue
			remaining = 1 - settled.get(row.name, 0)
			for person, allocated in self.team:
				entries.append(self.entry(row, person, allocated, remaining))
		return entries

	def entry(self, row, person, allocated, remaining):
		note = self.note
		date = getdate(note.posting_date)
		share = allocated / 100 * remaining
		ratio = person_ratio(row, person)
		return Expected(
			company=note.company,
			customer=note.customer,
			reference_row=row.name,
			item_code=row.item_code,
			item_group=row.item_group,
			classification=row.commission_classification,
			commission_percent=row.commission_percent,
			line_commission=plain_amount(row) * ratio,
			currency=note.currency,
			sales_person=person,
			employee=person and frappe.get_cached_value("Sales Person", person, "employee"),
			allocated=allocated,
			payment_doctype=None,
			payment_name=None,
			credit_note=note.name,
			sales_date=date,
			payment_date=None,
			date=date,
			at=(date, note.creation),
			standalone=True,
			rate=flt(note.conversion_rate),
			deduction=plain_amount(row) * share * ratio,
			zar=plain_amount(row, base=True) * share * ratio,
			recognised=flt(row.base_net_amount) * share,
		)
