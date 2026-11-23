import frappe
from frappe.utils import flt

PRECISION = 2


def net_amounts(row, factor):
	"""(net_amount, base_net_amount): the part of the line the row's commission is calculated on."""
	divisor = flt(row.commission_percent) / 100 * flt(factor)
	multiplier = flt(row.multiplier)
	if not divisor:
		return 0, 0
	net = flt(row.earned_amount) / divisor
	base_net = flt(row.commission_amount) / divisor / multiplier if multiplier else 0
	return flt(net, PRECISION), flt(base_net, PRECISION)


class LineFactors:
	"""Classification factors of the lines of one document."""

	def __init__(self, doctype, name):
		self.doctype = doctype
		self.name = name
		self.factors = None

	def get(self, reference_row):
		if self.factors is None:
			rows = frappe.get_all(
				f"{self.doctype} Item",
				filters={"parent": self.name},
				fields=["name", "commission_factor"],
				parent_doctype=self.doctype,
			)
			self.factors = {row.name: flt(row.commission_factor) for row in rows}
		return self.factors.get(reference_row, 0)

	def set_net_amounts(self, row):
		row.commission_factor = self.get(row.reference_row)
		row.net_amount, row.base_net_amount = net_amounts(row, row.commission_factor)
