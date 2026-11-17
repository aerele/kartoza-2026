import frappe
from frappe.utils import flt


class LineWalk:
	"""Walks the payments and credit notes of one line in time order and tells what it has earned."""

	# Amounts are in the currency the customer pays in (the party account currency) and are not rounded.
	# Each note has note_commission (its commission on this line) and note_grand_total, both negative.

	def __init__(self, commission, grand_total, slices, notes):
		self.commission = flt(commission)
		self.grand_total = flt(grand_total)
		self.slices = slices
		self.notes = notes
		self.reset()

	def reset(self):
		self.effective_commission = self.commission
		self.effective_grand_total = self.grand_total
		self.paid = 0.0
		self.earned_net = 0.0
		self.paid_earned = 0.0
		self.before = []
		self.entries = []

	def run(self):
		self.reset()
		for event in self.events():
			if event.kind == "payment":
				self.add_payment(event.data)
			else:
				self.add_credit_note(event.data)
		return self.entries

	def events(self):
		payments = [frappe._dict(kind="payment", data=s, at=(s.date, s.creation)) for s in self.slices]
		notes = [frappe._dict(kind="note", data=n, at=(n.date, n.creation)) for n in self.notes]
		return sorted(payments + notes, key=lambda e: e.at)

	def add_payment(self, slice_):
		share = max(flt(slice_.paid), 0)
		open_amount = self.effective_grand_total - self.paid
		earned = 0.0
		if share and open_amount > 0:
			unearned = self.effective_commission - self.earned_net
			earned = unearned * share / open_amount
			earned = min(max(earned, 0), max(unearned, 0))
		self.paid += share
		self.earned_net += earned
		self.paid_earned += earned
		self.before.append((slice_.payment_doctype, slice_.payment_name))
		self.entries.append(frappe._dict(kind="earned", slice=slice_, earned=earned, paid=share))

	def add_credit_note(self, note):
		note_commission = flt(note.note_commission)
		deduction = 0.0
		if note_commission and self.commission and not note.override:
			deduction = -self.paid_earned * abs(note_commission) / self.commission
		self.earned_net += deduction
		self.entries.append(
			frappe._dict(
				kind="deduction",
				note=note,
				deduction=deduction,
				ratio=abs(note_commission) / self.commission if self.commission else 0,
				before=list(self.before),
			)
		)
		self.effective_commission += note_commission
		self.effective_grand_total += flt(note.note_grand_total)
