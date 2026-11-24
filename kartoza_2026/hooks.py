app_name = "kartoza_2026"
app_title = "Kartoza 2026"
app_publisher = "Aerele"
app_description = "Sales commission calculation on Sales Orders and Invoices"
app_email = "sowmya@aerele.in"
app_license = "mit"

# Apps
# ------------------

# required_apps = []
required_apps = ["erpnext", "hrms", "kartoza"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "kartoza_2026",
# 		"logo": "/assets/kartoza_2026/logo.png",
# 		"title": "Kartoza 2026",
# 		"route": "/kartoza_2026",
# 		"has_permission": "kartoza_2026.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/kartoza_2026/css/kartoza_2026.css"
# app_include_js = "/assets/kartoza_2026/js/kartoza_2026.js"

# include js, css files in header of web template
# web_include_css = "/assets/kartoza_2026/css/kartoza_2026.css"
# web_include_js = "/assets/kartoza_2026/js/kartoza_2026.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "kartoza_2026/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_js = {
	"Customer": ["public/js/sales_team.js", "public/js/customer.js"],
	"Sales Order": ["public/js/sales_team.js", "public/js/commission.js"],
	"Sales Invoice": ["public/js/sales_team.js", "public/js/commission.js"],
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "kartoza_2026/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "kartoza_2026.utils.jinja_methods",
# 	"filters": "kartoza_2026.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "kartoza_2026.install.before_install"
# after_install = "kartoza_2026.install.after_install"
after_install = "kartoza_2026.setup.install.after_install"
after_migrate = "kartoza_2026.setup.install.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "kartoza_2026.uninstall.before_uninstall"
# after_uninstall = "kartoza_2026.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "kartoza_2026.utils.before_app_install"
# after_app_install = "kartoza_2026.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "kartoza_2026.utils.before_app_uninstall"
# after_app_uninstall = "kartoza_2026.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "kartoza_2026.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "kartoza_2026.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "kartoza_2026.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["kartoza_2026.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

doc_events = {
	"Item": {"validate": "kartoza_2026.utils.rates.validate_item_percent"},
	"Customer": {"validate": "kartoza_2026.utils.sales_team.validate_sales_person_not_group"},
	"Advance Payment Ledger Entry": {"on_update": "kartoza_2026.utils.sync.sync_on_advance"},
	"Payment Entry": {
		"on_submit": [
			"kartoza_2026.utils.sync.sync_on_payment",
			"kartoza_2026.utils.refunds.stamp_refund_on_payment",
		],
		"on_cancel": [
			"kartoza_2026.utils.sync.sync_on_payment",
			"kartoza_2026.utils.refunds.stamp_refund_on_payment",
		],
	},
	"Journal Entry": {
		"on_submit": [
			"kartoza_2026.utils.sync.sync_on_reconciliation",
			"kartoza_2026.utils.refunds.stamp_refund_on_journal",
		],
		"on_cancel": [
			"kartoza_2026.utils.sync.sync_on_reconciliation",
			"kartoza_2026.utils.refunds.stamp_refund_on_journal",
		],
	},
	"Salary Slip": {
		"on_submit": "kartoza_2026.utils.payroll.stamp_ledger_on_submit",
		"on_cancel": "kartoza_2026.utils.payroll.release_ledger_on_cancel",
	},
	"Sales Order": {
		"before_submit": "kartoza_2026.utils.target_sales.freeze_split",
		"validate": [
			"kartoza_2026.utils.sales_team.validate_sales_person_employee",
			"kartoza_2026.utils.validation.calculate_commission",
		],
		"before_update_after_submit": [
			"kartoza_2026.utils.sales_team.validate_sales_person_employee",
			"kartoza_2026.utils.validation.update_commission_after_submit",
		],
		"on_change": "kartoza_2026.utils.sync.sync_on_change",
	},
	"Sales Invoice": {
		"before_submit": "kartoza_2026.utils.target_sales.freeze_split",
		"validate": [
			"kartoza_2026.utils.sales_team.validate_sales_person_employee",
			"kartoza_2026.utils.validation.calculate_commission",
		],
		"before_update_after_submit": [
			"kartoza_2026.utils.sales_team.validate_sales_person_employee",
			"kartoza_2026.utils.validation.update_commission_after_submit",
		],
		"on_change": "kartoza_2026.utils.sync.sync_on_change",
		"on_submit": "kartoza_2026.utils.sync.sync_on_invoice",
		"on_cancel": "kartoza_2026.utils.sync.sync_on_invoice",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"kartoza_2026.tasks.all"
# 	],
# 	"daily": [
# 		"kartoza_2026.tasks.daily"
# 	],
# 	"hourly": [
# 		"kartoza_2026.tasks.hourly"
# 	],
# 	"weekly": [
# 		"kartoza_2026.tasks.weekly"
# 	],
# 	"monthly": [
# 		"kartoza_2026.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "kartoza_2026.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "kartoza_2026.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "kartoza_2026.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

override_doctype_dashboards = {
	"Sales Order": "kartoza_2026.utils.dashboards.order_or_invoice",
	"Sales Invoice": "kartoza_2026.utils.dashboards.order_or_invoice",
	"Payment Entry": "kartoza_2026.utils.dashboards.payment",
	"Journal Entry": "kartoza_2026.utils.dashboards.payment",
	"Salary Slip": "kartoza_2026.utils.dashboards.salary_slip",
}

override_doctype_class = {
	"Salary Slip": "kartoza_2026.overrides.salary_slip.CommissionSalarySlip",
}

ignore_links_on_delete = ["Commission Ledger"]

# Request Events
# ----------------
# before_request = ["kartoza_2026.utils.before_request"]
# after_request = ["kartoza_2026.utils.after_request"]

# Job Events
# ----------
# before_job = ["kartoza_2026.utils.before_job"]
# after_job = ["kartoza_2026.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"kartoza_2026.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
