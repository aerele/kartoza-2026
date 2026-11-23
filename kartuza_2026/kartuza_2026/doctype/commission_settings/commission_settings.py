# Copyright (c) 2026, Aerele and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CommissionSettings(Document):
	def validate(self):
		self.validate_factors()
		self.validate_escalator_multiplier()

	def validate_factors(self):
		seen = set()
		for row in self.classification_factors:
			if row.classification in seen:
				frappe.throw(
					_("<b>{0}</b> is listed twice in Classification Factors. Keep one row for each.").format(
						row.classification
					),
					title=_("Duplicate Classification"),
				)
			seen.add(row.classification)
			if flt(row.factor) <= 0:
				frappe.throw(
					_("The factor of <b>{0}</b> must be above 0.").format(row.classification),
					title=_("Invalid Factor"),
				)

	def validate_escalator_multiplier(self):
		if flt(self.escalator_multiplier) < 1:
			frappe.throw(
				_("<b>Escalator Multiplier</b> must be 1 or more. Use 1 for no escalator."),
				title=_("Invalid Escalator"),
			)
