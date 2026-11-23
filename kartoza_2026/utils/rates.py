import frappe
from frappe import _
from frappe.utils import flt


def validate_item_percent(doc, method=None):
	"""Item validate: the Commission % must be a valid percentage."""
	if not 0 <= flt(doc.get("commission_percent")) <= 100:
		frappe.throw(
			_("<b>Commission %</b> must be between 0 and 100."),
			title=_("Invalid Commission Rate"),
		)


def item_percent(item_code):
	"""The Item's own Commission %, 0 when it has none."""
	if not item_code:
		return 0.0
	return flt(frappe.get_cached_value("Item", item_code, "commission_percent"))


def person_percent(sales_person):
	"""The Sales Person's own ERPNext commission rate (a Data field)."""
	if not sales_person:
		return 0.0
	return flt(frappe.get_cached_value("Sales Person", sales_person, "commission_rate"))


def person_ratio(row, sales_person):
	"""Share of a line's stored commission that a salesperson's own rate earns."""
	# only lines whose % came from the Sales Person differ per person, item % is the same for all
	if not sales_person or item_percent(row.get("item_code")) > 0:
		return 1.0
	line_percent = flt(row.get("commission_percent"))
	return person_percent(sales_person) / line_percent if line_percent else 0.0


class RateResolver:
	"""Works out the Commission % and the New / Renewal factor of an item line."""

	def __init__(self, team=None):
		settings = frappe.get_cached_doc("Commission Settings")
		self.factors = {row.classification: flt(row.factor) for row in settings.classification_factors}
		self.team = team or []
		self.item_cache = {}

	def get_factor(self, classification):
		"""The factor of a classification, 0 when Settings has no row for it."""
		return self.factors.get(classification, 0.0)

	def get_percent(self, item_code):
		"""The Item's % wins, else the first Sales Team row that has a rate."""
		if item_code not in self.item_cache:
			self.item_cache[item_code] = item_percent(item_code)
		if self.item_cache[item_code] > 0:
			return self.item_cache[item_code]
		for row in self.team:
			percent = person_percent(row.sales_person)
			if percent > 0:
				return percent
		return 0.0
