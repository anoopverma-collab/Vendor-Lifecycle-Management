app_name = "vendor_lifecycle"
app_title = "Vendor Lifecycle"
app_publisher = "Auriga IT"
app_description = "Vendor onboarding and deboarding lifecycle management for Frappe/ERPNext"
app_email = "rahul.chaudhary@aurigait.com"
app_license = "mit"
# "?v=" makes browsers fetch the logo again after it changes (assets are cached for a year).
app_logo_url = "/assets/vendor_lifecycle/icons/desktop_icons/subtle/vendor_lifecycle.svg?v=1"
app_home = "/desk/vendor-lifecycle"

# Apps
# ------------------

required_apps = ["erpnext"]

# Exposes Vendor Lifecycle Settings values the Desk UI needs synchronously
# (e.g. to hide a button or toggle a field as mandatory) to every logged-in
# user regardless of their read permission on the Settings doctype itself —
# a plain client-side frappe.db.get_single_value() call would silently fail
# for roles (like Vendor Lifecycle User) that can't read Settings directly.
extend_bootinfo = "vendor_lifecycle.vendor_lifecycle.boot.set_bootinfo"

# Roles, Custom Fields, Property Setters and Supplier Connections are not
# fixtures (those are force-imported on every migrate, undoing a site's own
# changes) - see vendor_lifecycle/setup.py.

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": app_logo_url,
		"title": app_title,
		"route": app_home,
		"has_permission": "vendor_lifecycle.vendor_lifecycle.permissions.check_app_permission",
	}
]

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
# Supplier is a core ERPNext doctype this app doesn't own — these two hooks
# are Frappe's own sanctioned way to add extra client-side behavior to a
# doctype from another app, without touching ERPNext's own source files.
doctype_js = {"Supplier": "public/js/supplier.js"}
doctype_list_js = {"Supplier": "public/js/supplier_list.js"}
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

# Vendor-facing Supplier Portal pages — Vendor Satisfaction Survey,
# deliberately built as plain www/ pages (not Web Forms) so they live
# inside the normal portal (sidebar-visible, alongside a vendor's other
# records) rather than as a standalone form. The list is hand-built
# (www/vss_list) for a proper server-rendered table
# with a Status column — Frappe's generic doctype-name list route is
# AJAX-driven and renders no data on first load, which is why it looked
# broken/empty. Satisfaction Survey has no vendor-facing create page — a
# Supplier can only view/fill/submit a survey the scheduler or an internal
# user already created (no "create" permission on the doctype at all for
# that role). "new" and "<name>" detail/edit pages are hand-built (Frappe
# has no generic create/edit page for a plain doctype outside Web Form),
# mirroring the same pattern ERPNext's own Request for Quotation portal page
# uses.
website_route_rules = [
	{"from_route": "/vendor-satisfaction-surveys", "to_route": "vss_list"},
	{
		"from_route": "/vendor-satisfaction-surveys/<path:name>",
		"to_route": "vss_detail",
		"defaults": {"doctype": "Vendor Satisfaction Survey"},
	},
]

standard_portal_menu_items = [
	{
		"title": "Satisfaction Surveys",
		"route": "/vendor-satisfaction-surveys",
		"reference_doctype": "Vendor Satisfaction Survey",
		"role": "Supplier",
	},
]

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

before_uninstall = "vendor_lifecycle.vendor_lifecycle.uninstall.before_uninstall"
after_uninstall = "vendor_lifecycle.vendor_lifecycle.uninstall.after_uninstall"

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
}

has_permission = {
	"Vendor Satisfaction Survey": "vendor_lifecycle.vendor_lifecycle.permissions.vendor_satisfaction_survey_has_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

# No doc_events on Purchase Order / RFQ / Purchase Invoice / Purchase Receipt /
# Journal Entry / Payment Entry: a disabled (deboarded) Supplier is already
# fully blocked on all of them by ERPNext's own
# erpnext.accounts.party.validate_party_frozen_disabled (at validate for the
# buying documents, at GL Entry for JE/PE). Final dues are settled through the
# Deboarding Checklist's "Temporarily Enable Supplier" instead.
doc_events = {
	"Supplier": {
		"validate": "vendor_lifecycle.vendor_lifecycle.vendor_creation.validate_supplier_vendor_kyc",
		"onload": [
			"vendor_lifecycle.vendor_lifecycle.vendor_creation.set_supplier_disable_reason_onload",
			"vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_checklist.vendor_deboarding_checklist.set_supplier_temporary_enable_onload",
		],
	},
	"Communication": {
		"on_update": [
			"vendor_lifecycle.vendor_lifecycle.signoff_reply.handle_signoff_reply",
			"vendor_lifecycle.vendor_lifecycle.checklist_clearance_reply.handle_checklist_clearance_reply",
		],
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"vendor_lifecycle.vendor_lifecycle.tasks.create_pending_satisfaction_surveys",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_satisfaction_survey_reminders",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_checklist_assignment_reminders",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_deboarding_checklist_followups",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_signoff_followups",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_compliance_audit_renewal_notices",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_compliance_audit_renewal_draft_reminders",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_signoff_renewal_notices",
		"vendor_lifecycle.vendor_lifecycle.tasks.send_signoff_renewal_draft_reminders",
	],
	# Needs a specific time (2 AM), unlike the "daily" bucket above which
	# just runs sometime during Frappe's own daily scheduler window.
	"cron": {
		"0 2 * * *": [
			"vendor_lifecycle.vendor_lifecycle.tasks.auto_disable_expired_temporary_enables",
		],
	},
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

