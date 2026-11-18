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
		self.counted_paid = 0.0
		self.counted_net = 0.0
		self.earned_net = 0.0
		self.paid_earned = 0.0
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
		counted = self.counted_share(share)
		self.counted_paid += counted
		self.counted_net += counted
		part = frappe._dict(kind="payment", date=slice_.date, at=(slice_.date, slice_.creation))
		part.update(earned=earned, paid=share, counted=counted)
		entry = frappe._dict(kind="earned", slice=slice_, earned=earned, paid=share, own_paid=share)
		self.entries.append(entry.update(counted=counted, parts=[part]))

	def counted_share(self, share):
		"""Sales a payment counts for: the money is spread over what is still owed, never more than the line."""
		if not share or self.effective_grand_total <= 0:
			return share
		return min(
			share * self.grand_total / self.effective_grand_total * self.kept_share(),
			max(self.grand_total * self.kept_share() - self.counted_net, 0),
		)

	def kept_share(self):
		"""Share of the line that no credit note has taken back."""
		return self.effective_commission / self.commission if self.commission else 1

	def add_credit_note(self, note):
		note_commission = flt(note.note_commission)
		deduction = 0.0
		if note_commission and self.commission:
			deduction = -self.paid_earned * abs(note_commission) / self.commission
		self.earned_net += deduction
		if note_commission and self.commission:
			self.counted_net -= self.counted_paid * abs(note_commission) / self.commission
		self.entries.append(
			frappe._dict(
				kind="deduction",
				note=note,
				deduction=deduction,
				ratio=abs(note_commission) / self.commission if self.commission else 0,
				before=[(e, e.earned, e.counted) for e in self.entries if e.kind == "earned"],
			)
		)
		self.effective_commission += note_commission
		self.effective_grand_total += flt(note.note_grand_total)
		self.catch_up(note)

	def catch_up(self, note):
		"""Money paid is spread over what is still owed, so a return of another line earns the rest here."""
		if self.effective_grand_total <= 0 or not self.paid:
			return
		share = min(self.paid / self.effective_grand_total, 1)
		extra = flt(self.effective_commission * share - self.earned_net, 9)
		if extra <= 0:
			return
		extra_counted = max(share * self.grand_total * self.kept_share() - self.counted_net, 0)
		self.earned_net += extra
		self.paid_earned += extra
		self.counted_paid += extra_counted
		self.counted_net += extra_counted
		# every payment takes its part of the catch-up in proportion to the money it paid (exact, no cent lost)
		for entry in self.entries:
			if entry.kind != "earned" or not entry.own_paid:
				continue
			weight = entry.own_paid / self.paid
			part = frappe._dict(kind="catch_up", date=note.date, at=(note.date, note.creation))
			# the catch-up counts as paid sales and adds no money, so its paid share is the same amount
			part.update(earned=extra * weight, paid=extra_counted * weight, counted=extra_counted * weight)
			entry.parts.append(part)
			entry.earned += part.earned
			entry.paid += part.paid
			entry.counted += part.counted
