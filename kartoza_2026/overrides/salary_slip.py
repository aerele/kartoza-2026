import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import flt
from hrms.payroll.doctype.salary_slip.salary_slip import get_salary_component_data
from kartoza.custom_py.salary_slip import CustomSalarySlip

from kartoza_2026.utils.ledger_rows import PRECISION
from kartoza_2026.utils.payroll import get_commission_filters


class CommissionSalarySlip(CustomSalarySlip):
	"""Kartoza's salary slip that also pays the commission the Commission Ledger holds for the period."""

	def add_additional_salary_components(self, component_type):
		super().add_additional_salary_components(component_type)
		if component_type == "earnings":
			self.add_commission_component()

	def add_commission_component(self):
		amount = self.get_commission_amount()
		component = frappe.db.get_single_value("Commission Settings", "commission_salary_component")
		if not component:
			if amount:
				frappe.msgprint(
					_("Commission Salary Component is not set in Commission Settings."),
					indicator="orange",
				)
			return
		if amount <= 0:
			# unpaid rows stay in the ledger for a later slip
			return
		remove_if_zero = frappe.get_cached_value("Salary Component", component, "remove_if_zero_valued")
		self.update_component_row(
			get_salary_component_data(component),
			amount,
			"earnings",
			remove_if_zero_valued=remove_if_zero,
		)

	def get_commission_amount(self):
		ledger = frappe.qb.DocType("Commission Ledger")
		query = frappe.qb.from_(ledger).select(Sum(ledger.commission_amount))
		for condition in get_commission_filters(ledger, self):
			query = query.where(condition)
		return flt(query.run()[0][0], PRECISION)
