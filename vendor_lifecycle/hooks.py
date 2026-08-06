app_name = "vendor_lifecycle"
app_title = "Vendor Lifecycle"
app_publisher = "Auriga IT"
app_description = "Vendor onboarding and deboarding lifecycle management for Frappe/ERPNext"
app_email = "rahul.chaudhary@aurigait.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext"]

# Exposes Vendor Lifecycle Settings values the Desk UI needs synchronously
# (e.g. to hide a button or toggle a field as mandatory) to every logged-in
# user regardless of their read permission on the Settings doctype itself —
# a plain client-side frappe.db.get_single_value() call would silently fail
# for roles (like Vendor Lifecycle User) that can't read Settings directly.
extend_bootinfo = "vendor_lifecycle.vendor_lifecycle.boot.set_bootinfo"

# Registry of e-sign providers for Vendor Sign Off, resolved in
# vendor_lifecycle.integrations.dispatch.get_esign_handler. Another app can add
# a provider (e.g. Digio) by declaring the same hook key with its own handler
# path — no changes to this app are needed.
vendor_lifecycle_esign_providers = {
	"Manual": "vendor_lifecycle.vendor_lifecycle.integrations.esign.manual.ManualESignHandler"
}

# Same registry pattern, for the deboarding-checklist-completion notification slot.
vendor_lifecycle_notification_providers = {
	"Manual": "vendor_lifecycle.vendor_lifecycle.integrations.notification.manual.ManualNotificationHandler"
}

fixtures = [
	{
		"doctype": "Role",
		"filters": [["role_name", "in", ["Vendor Lifecycle Manager", "Vendor Lifecycle User"]]]
	},
	{
		"doctype": "Custom Field",
		"filters": [["module", "=", "Vendor Lifecycle"]]
	},
	{
		"doctype": "Property Setter",
		"filters": [["module", "=", "Vendor Lifecycle"]]
	},
	{
		"doctype": "Client Script",
		"filters": [["module", "=", "Vendor Lifecycle"]]
	},
]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "vendor_lifecycle",
# 		"logo": "/assets/vendor_lifecycle/logo.png",
# 		"title": "Vendor Lifecycle",
# 		"route": "/vendor_lifecycle",
# 		"has_permission": "vendor_lifecycle.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/vendor_lifecycle/css/vendor_lifecycle.css"
# app_include_js = "/assets/vendor_lifecycle/js/vendor_lifecycle.js"

# include js, css files in header of web template
# web_include_css = "/assets/vendor_lifecycle/css/vendor_lifecycle.css"
# web_include_js = "/assets/vendor_lifecycle/js/vendor_lifecycle.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "vendor_lifecycle/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "vendor_lifecycle/public/icons.svg"

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

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "vendor_lifecycle.utils.jinja_methods",
# 	"filters": "vendor_lifecycle.utils.jinja_filters"
# }

# Installation
# ------------

after_install = "vendor_lifecycle.vendor_lifecycle.install.after_install"

# Runs on every `bench migrate`, not just install — keeps the shipped
# Workspace Sidebar / Desktop Icon in sync even after later edits to those
# files (Frappe doesn't auto-import them the way it does other standard
# doctypes). See vendor_lifecycle/install.py for why this is needed.
after_migrate = "vendor_lifecycle.vendor_lifecycle.install.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "vendor_lifecycle.uninstall.before_uninstall"
# after_uninstall = "vendor_lifecycle.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "vendor_lifecycle.utils.before_app_install"
# after_app_install = "vendor_lifecycle.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "vendor_lifecycle.utils.before_app_uninstall"
# after_app_uninstall = "vendor_lifecycle.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "vendor_lifecycle.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "vendor_lifecycle.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"Vendor Satisfaction Survey": "vendor_lifecycle.vendor_lifecycle.permissions.vendor_satisfaction_survey_query_conditions",
	"Vendor Support Ticket": "vendor_lifecycle.vendor_lifecycle.permissions.vendor_support_ticket_query_conditions",
}

has_permission = {
	"Vendor Satisfaction Survey": "vendor_lifecycle.vendor_lifecycle.permissions.vendor_satisfaction_survey_has_permission",
	"Vendor Support Ticket": "vendor_lifecycle.vendor_lifecycle.permissions.vendor_support_ticket_has_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Purchase Order": {
		"validate": "vendor_lifecycle.vendor_lifecycle.deboarding_guard.block_disabled_supplier_on_order",
	},
	"Request for Quotation": {
		"validate": "vendor_lifecycle.vendor_lifecycle.deboarding_guard.block_disabled_supplier_on_order",
	},
	"Purchase Invoice": {
		"validate": "vendor_lifecycle.vendor_lifecycle.deboarding_guard.warn_disabled_supplier_on_transaction",
	},
	"Purchase Receipt": {
		"validate": "vendor_lifecycle.vendor_lifecycle.deboarding_guard.warn_disabled_supplier_on_transaction",
	},
	"Journal Entry": {
		"validate": "vendor_lifecycle.vendor_lifecycle.deboarding_guard.warn_disabled_supplier_on_transaction",
	},
	"Payment Entry": {
		"validate": "vendor_lifecycle.vendor_lifecycle.deboarding_guard.warn_disabled_supplier_on_transaction",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"vendor_lifecycle.vendor_lifecycle.tasks.create_pending_satisfaction_surveys",
	],
}

# Testing
# -------

# before_tests = "vendor_lifecycle.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "vendor_lifecycle.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "vendor_lifecycle.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "vendor_lifecycle.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["vendor_lifecycle.utils.before_request"]
# after_request = ["vendor_lifecycle.utils.after_request"]

# Job Events
# ----------
# before_job = ["vendor_lifecycle.utils.before_job"]
# after_job = ["vendor_lifecycle.utils.after_job"]

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
# 	"vendor_lifecycle.auth.validate"
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

