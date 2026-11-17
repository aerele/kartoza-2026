import frappe
from frappe import _

OVERRIDE_ROLE = "System Manager"


def validate_override(doc, method=None):
	"""Sales Invoice: only a System Manager changes the override, and only a credit note keeps one."""
	if not doc.get("is_return"):
		doc.override_deduction = 0
		doc.override_reason = None
		return
	before = doc.get_doc_before_save()
	was_ticked = bool(before and before.get("override_deduction"))
	if bool(doc.override_deduction) != was_ticked and OVERRIDE_ROLE not in frappe.get_roles():
		frappe.throw(
			_(
				"Only a user with the <b>{0}</b> role can change <b>Override Deduction</b> on <b>{1}</b>."
			).format(OVERRIDE_ROLE, doc.name or _("this credit note")),
			title=_("Not Allowed"),
		)
