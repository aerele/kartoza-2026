# Copyright (c) 2026, Aerele and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sales_commission.utils.ledger_status import status_of


class CommissionLedger(Document):
	def before_insert(self):
		self.status = status_of(self)

	def validate(self):
		if not self.is_new():
			self.block_change()

	def on_trash(self):
		self.block_change()

	def block_change(self):
		frappe.throw(
			_("Commission Ledger entries cannot be changed. Cancel or correct the source document instead."),
			title=_("Ledger Entry Locked"),
		)


def on_doctype_update():
	frappe.db.add_index("Commission Ledger", ["reference_doctype", "reference_name"])
	frappe.db.add_index("Commission Ledger", ["sales_person", "fiscal_year"])
