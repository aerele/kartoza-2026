app_name = "sales_commission"
app_title = "Sales Commission"
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
# 		"name": "sales_commission",
# 		"logo": "/assets/sales_commission/logo.png",
# 		"title": "Sales Commission",
# 		"route": "/sales_commission",
# 		"has_permission": "sales_commission.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/sales_commission/css/sales_commission.css"
# app_include_js = "/assets/sales_commission/js/sales_commission.js"

# include js, css files in header of web template
# web_include_css = "/assets/sales_commission/css/sales_commission.css"
# web_include_js = "/assets/sales_commission/js/sales_commission.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "sales_commission/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_js = {
	"Sales Order": "public/js/commission.js",
	"Sales Invoice": "public/js/commission.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "sales_commission/public/icons.svg"

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
# 	"methods": "sales_commission.utils.jinja_methods",
# 	"filters": "sales_commission.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "sales_commission.install.before_install"
# after_install = "sales_commission.install.after_install"
after_install = "sales_commission.setup.install.after_install"
after_migrate = "sales_commission.setup.install.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "sales_commission.uninstall.before_uninstall"
# after_uninstall = "sales_commission.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "sales_commission.utils.before_app_install"
# after_app_install = "sales_commission.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "sales_commission.utils.before_app_uninstall"
# after_app_uninstall = "sales_commission.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "sales_commission.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "sales_commission.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "sales_commission.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["sales_commission.search.awesomebar_results"]

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
	"Item": {"validate": "sales_commission.utils.rates.validate_item_percent"},
	"Advance Payment Ledger Entry": {"on_update": "sales_commission.utils.sync.sync_on_advance"},
	"Payment Entry": {
		"on_submit": "sales_commission.utils.sync.sync_on_payment",
		"on_cancel": "sales_commission.utils.sync.sync_on_payment",
	},
	"Journal Entry": {
		"on_submit": "sales_commission.utils.sync.sync_on_reconciliation",
		"on_cancel": "sales_commission.utils.sync.sync_on_reconciliation",
	},
	"Salary Slip": {
		"on_submit": "sales_commission.utils.payroll.stamp_ledger_on_submit",
		"on_cancel": "sales_commission.utils.payroll.release_ledger_on_cancel",
	},
	"Sales Order": {
		"validate": [
			"sales_commission.utils.sales_team.validate_sales_person_employee",
			"sales_commission.utils.validation.calculate_commission",
		],
		"before_update_after_submit": [
			"sales_commission.utils.sales_team.validate_sales_person_employee",
			"sales_commission.utils.validation.update_commission_after_submit",
		],
		"on_change": "sales_commission.utils.sync.sync_on_change",
	},
	"Sales Invoice": {
		"validate": [
			"sales_commission.utils.override.validate_override",
			"sales_commission.utils.sales_team.validate_sales_person_employee",
			"sales_commission.utils.validation.calculate_commission",
		],
		"before_update_after_submit": [
			"sales_commission.utils.override.validate_override",
			"sales_commission.utils.sales_team.validate_sales_person_employee",
			"sales_commission.utils.validation.update_commission_after_submit",
		],
		"on_change": "sales_commission.utils.sync.sync_on_change",
		"on_submit": "sales_commission.utils.sync.sync_on_invoice",
		"on_cancel": "sales_commission.utils.sync.sync_on_invoice",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"sales_commission.tasks.all"
# 	],
# 	"daily": [
# 		"sales_commission.tasks.daily"
# 	],
# 	"hourly": [
# 		"sales_commission.tasks.hourly"
# 	],
# 	"weekly": [
# 		"sales_commission.tasks.weekly"
# 	],
# 	"monthly": [
# 		"sales_commission.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "sales_commission.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "sales_commission.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "sales_commission.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

override_doctype_class = {
	"Salary Slip": "sales_commission.overrides.salary_slip.CommissionSalarySlip",
}

ignore_links_on_delete = ["Commission Ledger"]

# Request Events
# ----------------
# before_request = ["sales_commission.utils.before_request"]
# after_request = ["sales_commission.utils.after_request"]

# Job Events
# ----------
# before_job = ["sales_commission.utils.before_job"]
# after_job = ["sales_commission.utils.after_job"]

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
# 	"sales_commission.auth.validate"
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

