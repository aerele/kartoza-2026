import frappe

from sales_commission.utils.ledger_rows import row_key

CARRIED_FIELDS = (
	"company",
	"customer",
	"employee",
	"sales_person",
	"reference_row",
	"item_code",
	"item_group",
	"payment_doctype",
	"payment_name",
	"credit_note",
	"classification",
	"commission_percent",
	"currency",
	"sales_date",
	"payment_date",
)


class Expected(frappe._dict):
	"""What one ledger key (line + payment or credit note + sales person) should hold now."""

	@property
	def key(self):
		return row_key(self)
