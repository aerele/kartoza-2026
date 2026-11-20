from frappe.utils import flt

from sales_commission.utils.history import SalesHistory
from sales_commission.utils.line_fields import (
	clear_line,
	set_line_amounts,
	unrounded_commission,
)
from sales_commission.utils.order_links import OrderLinks
from sales_commission.utils.rates import RateResolver
from sales_commission.utils.target_estimate import TargetMultipliers


class LineCalculator:
	"""Classification, %, factor and amount of the lines of an order or invoice."""

	# keep_stored: lines that already have a classification keep it and their %, only amounts follow.
	# plain: lines of a credit note without an original, worked out like any invoice line.

	def __init__(self, doc, rows=None, keep_stored=False, plain=False, multipliers=None):
		self.doc = doc
		self.rows = doc.items if rows is None else rows
		self.keep_stored = keep_stored
		self.plain = plain
		self.rates = RateResolver(doc.get("sales_team"))
		self.history = SalesHistory(doc)
		self.links = OrderLinks(doc)
		self.multipliers = multipliers or TargetMultipliers(doc, enabled=not plain)
		self.kept = []

	def apply(self):
		self.classify_lines()
		for row in self.rows:
			factor = self.multipliers.for_line(row)
			if any(row is kept for kept in self.kept):
				self.follow_net_amount(row, factor)
			else:
				set_line_amounts(row, factor)
			row.target_multiplier = self.multipliers.describe(row)

	def classify_lines(self):
		pending = []
		for row in self.rows:
			if self.keep_stored and row.commission_classification:
				self.kept.append(row)
				continue
			clear_line(row)
			if not self.is_excluded(row) and not self.copy_from_order(row):
				pending.append(row)
		earlier = self.history.earlier_groups({r.item_group for r in pending})
		for row in pending:
			row.commission_classification = "Renewal" if row.item_group in earlier else "New"
			self.set_rate(row)

	def set_rate(self, row):
		row.commission_percent = self.rates.get_percent(row.item_code)
		row.commission_factor = self.rates.get_factor(row.commission_classification)

	def follow_net_amount(self, row, factor):
		"""A stored line keeps its amount when only a cent fix separates it from the new value."""
		for field, base in (("commission_amount", False), ("base_commission_amount", True)):
			tolerance = 10 ** -row.precision(field) * max(len(self.rows), 1)
			estimate = unrounded_commission(row, base) * factor
			if abs(estimate - flt(row.get(field))) > tolerance:
				row.set(field, flt(estimate, row.precision(field)))

	def is_excluded(self, row):
		"""Lines without Grant Commission and Shopping Cart lines earn nothing."""
		if not row.get("grant_commission"):
			return True
		return not self.plain and (self.links.is_cart_doc() or self.links.is_cart_line(row))

	def copy_from_order(self, row):
		"""An invoice line keeps the classification and % of its order line."""
		line = None if self.plain else self.links.order_line(row)
		if not line:
			return False
		row.commission_classification = line.commission_classification
		row.commission_percent = line.commission_percent
		row.commission_factor = line.commission_factor
		return True
