import frappe

from kartuza_2026.setup.custom_fields import (
	create_commission_fields,
	hide_sales_team_commission_rate,
	hide_standard_commission_section,
	make_target_distribution_optional,
	remove_old_fields,
)


def after_install():
	create_commission_fields()
	hide_standard_commission_section()
	make_target_distribution_optional()
	hide_sales_team_commission_rate()
	save_default_settings()


def after_migrate():
	create_commission_fields()
	remove_old_fields()
	hide_standard_commission_section()
	make_target_distribution_optional()
	hide_sales_team_commission_rate()
	warn_if_app_order_wrong()


def warn_if_app_order_wrong():
	"""Frappe uses only the last app's Salary Slip override, so ours must come after kartoza."""
	apps = frappe.get_installed_apps()
	if "kartoza" in apps and apps.index("kartoza") > apps.index("kartuza_2026"):
		print("Move kartuza_2026 after kartoza in installed apps, or commission will not reach Salary Slips.")


def save_default_settings():
	"""Save the settings once, so the target in company currency is worked out."""
	frappe.get_single("Commission Settings").save(ignore_permissions=True)
