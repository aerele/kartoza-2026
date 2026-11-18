import frappe

from sales_commission.utils.payroll import latest_slip


class AdvanceSource:
	"""The Sales Order rows that earned an advance, which the invoice row moving it copies."""

	def __init__(self, entry):
		filters = {
			"reference_doctype": "Sales Order",
			"payment_name": entry.payment_name,
			"sales_person": entry.sales_person,
			"entry_type": "Earned",
			"is_cancelled": 0,
		}
		if entry.get("source_row"):
			filters["reference_row"] = entry.source_row
		self.rows = frappe.get_all(
			"Commission Ledger",
			filters=filters,
			fields=[
				"multiplier",
				"fiscal_year",
				"salary_slip",
				"running_total_before",
				"running_total_after",
			],
			order_by="creation asc, name asc",
		)

	def copy_to(self, row):
		"""Moving an advance keeps its year (the multipliers via shares_of) and its slip when the order's row was paid."""
		row.fiscal_year = self.rows[-1].fiscal_year
		row.salary_slip = latest_slip(r.salary_slip for r in self.rows)
