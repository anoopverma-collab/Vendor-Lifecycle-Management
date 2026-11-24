# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

"""Roles, Custom Fields, Property Setters, Supplier Connections and ERPNext
permissions this app sets up on a site.

Installed once by after_install. On every migrate only the roles, custom
fields and Supplier connections that have gone missing are re-created -
nothing that already exists is overwritten, so a site's own changes to them
(a role's Desk Access, a field's label, a removed permission) stay as the
site set them. A change the app itself needs later goes out as a patch.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

MODULE = "Vendor Lifecycle"
VENDOR_LIFECYCLE_MANAGER = "Vendor Lifecycle Manager"
VENDOR_LIFECYCLE_USER = "Vendor Lifecycle User"

ROLES = [
	{"role_name": VENDOR_LIFECYCLE_MANAGER, "desk_access": 1},
	{"role_name": VENDOR_LIFECYCLE_USER, "desk_access": 1},
]

CUSTOM_FIELDS = {
	"Supplier": [
		{
			"fieldname": "vendor_lifecycle_status",
			"label": "Vendor Lifecycle Status",
			"fieldtype": "Select",
			"options": "\n".join([
				"",
				"KYC Verified",
				"Background Check In Progress",
				"Background Verified",
				"Background Check Failed",
				"Re-Background Check In Progress",
				"Re-Background Check Approved",
				"Re-Background Check Failed",
				"Compliance Audit In Progress",
				"Audit Verified",
				"Compliance Audit Failed",
				"Re-Compliance Audit In Progress",
				"Re-Compliance Audit Approved",
				"Re-Compliance Audit Failed",
				"Sampling In Progress",
				"Sampling Approved",
				"Sampling Rejected",
				"Re-Sampling In Progress",
				"Re-Sampling Approved",
				"Re-Sampling Rejected",
				"Sign Off In Progress",
				"Sign Off Failed",
				"Re-Sign Off In Progress",
				"Re-Sign Off Failed",
				"Active",
				"Compliance Audit Expired",
				"Contract Expired",
				"Deboarding Initiated",
				"Deboarding Failed",
				"Temporarily Enabled",
				"Disabled",
			]),
			"description": (
				"Set by the Vendor Lifecycle app. Blank if this Supplier wasn't onboarded through"
				" it."
			),
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "supplier_group",
			"no_copy": 1,
			"print_hide": 1,
			"read_only": 1,
			"module": MODULE,
		},
		{
			"fieldname": "vendor_lifecycle_stage",
			"label": "Vendor Lifecycle Stage",
			"fieldtype": "Select",
			"options": "\n".join([
				"",
				"Onboarding in Process",
				"Onboarding Failed",
				"Onboarded",
				"Deboarding in Process",
				"Deboarding Failed",
				"Deboarded",
				"Reboarding in Process",
				"Reboarding Failed",
				"Reboarded",
			]),
			"description": (
				"Set by the Vendor Lifecycle app. The broad phase - Status above is the detailed "
				"view."
			),
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "vendor_lifecycle_status",
			"no_copy": 1,
			"print_hide": 1,
			"read_only": 1,
			"module": MODULE,
		},
		{
			"fieldname": "compliance_audit_valid_until",
			"label": "Compliance Audit Valid Until",
			"fieldtype": "Date",
			"description": "Set by the Vendor Lifecycle app from the latest passed Compliance Audit.",
			"in_standard_filter": 1,
			"insert_after": "alias",
			"no_copy": 1,
			"read_only": 1,
			"module": MODULE,
		},
		{
			"fieldname": "contract_valid_until",
			"label": "Contract Valid Until",
			"fieldtype": "Date",
			"description": "Set by the Vendor Lifecycle app from the latest passed Sign Off.",
			"in_standard_filter": 1,
			"insert_after": "compliance_audit_valid_until",
			"no_copy": 1,
			"read_only": 1,
			"module": MODULE,
		},
		{
			"fieldname": "legal_entity_type",
			"label": "Legal Entity Type",
			"fieldtype": "Select",
			"options": "\n".join([
				"",
				"Sole Proprietorship",
				"Partnership",
				"Limited Liability Partnership",
				"Private Limited Company",
				"Public Limited Company",
				"Corporation",
				"Non-Profit",
				"Government Entity",
				"Other",
			]),
			"insert_after": "vendor_lifecycle_stage",
			"module": MODULE,
		},
		{
			"fieldname": "business_type",
			"label": "Business Type",
			"fieldtype": "Select",
			"options": "\n".join([
				"",
				"Manufacturer",
				"Trader",
				"Service Provider",
				"Contractor / Job Worker",
				"Logistics / Transporter",
				"Freelancer / Consultant",
				"Raw Material Supplier",
				"Equipment / Machinery Supplier",
				"Technology / Software Vendor",
				"Facility / Maintenance Services",
				"Other",
			]),
			"insert_after": "legal_entity_type",
			"module": MODULE,
		},
		{
			"fieldname": "vendor_kyc",
			"label": "Vendor KYC",
			"hidden": 1,
			"insert_after": "website",
			"read_only": 1,
			"module": MODULE,
		},
		{
			# Plain Data, not a Link (same as vendor_kyc above): a Link is
			# re-checked by Frappe on every save, so a Supplier whose request
			# no longer exists (e.g. after uninstall + reinstall) could never
			# be saved again. Only the app sets this field.
			"fieldname": "onboarding_request",
			"label": "Vendor Onboarding Request",
			"fieldtype": "Data",
			"depends_on": "eval:doc.onboarding_request",
			"insert_after": "vendor_kyc",
			"read_only": 1,
			"module": MODULE,
		},
		{
			"fieldname": "rating_template",
			"label": "Satisfaction Rating Template",
			"fieldtype": "Link",
			"options": "Satisfaction Rating Template",
			"description": "Rating Criteria for this vendor's surveys — overrides the default.",
			"insert_after": "onboarding_request",
			"link_filters": "[[\"Satisfaction Rating Template\",\"disabled\",\"=\",0]]",
			"module": MODULE,
		},
		{
			"fieldname": "disable_satisfaction_survey",
			"label": "Disable Satisfaction Survey",
			"fieldtype": "Check",
			"default": "0",
			"description": "Stops auto-created surveys for this vendor.",
			"insert_after": "rating_template",
			"module": MODULE,
		},
		{
			"fieldname": "last_satisfaction_survey_date",
			"label": "Last Satisfaction Survey Date",
			"fieldtype": "Date",
			"description": "Date of this vendor's last survey.",
			"insert_after": "disable_satisfaction_survey",
			"no_copy": 1,
			"read_only": 1,
			"module": MODULE,
		},
	],
	"Purchase Invoice": [
		{
			# Both source fields are plain Data, not Link / Dynamic Link: a
			# link is re-checked on every save, so a draft Purchase Invoice
			# whose source was later cancelled, or an old one whose source
			# was deleted (uninstall + reinstall), could no longer be saved,
			# amended or updated. Only the app's "Bill to External Agency"
			# buttons set them.
			"fieldname": "vendor_lifecycle_source_doctype",
			"label": "Vendor Lifecycle Source Doctype",
			"fieldtype": "Data",
			"depends_on": "eval:doc.vendor_lifecycle_source_name",
			"insert_after": "supplier_name",
			"read_only": 1,
			"module": MODULE,
		},
		{
			"fieldname": "vendor_lifecycle_source_name",
			"label": "Vendor Lifecycle Source",
			"fieldtype": "Data",
			"depends_on": "eval:doc.vendor_lifecycle_source_name",
			"insert_after": "vendor_lifecycle_source_doctype",
			"read_only": 1,
			"module": MODULE,
		},
	],
	"Bank Account": [
		{
			"fieldname": "vendor_kyc",
			"label": "Vendor KYC",
			"hidden": 1,
			"insert_after": "party",
			"read_only": 1,
			"module": MODULE,
		},
	],
	"Address": [
		{
			"fieldname": "vendor_kyc",
			"label": "Vendor KYC",
			"hidden": 1,
			"insert_after": "links",
			"read_only": 1,
			"module": MODULE,
		},
	],
	"Contact": [
		{
			"fieldname": "vendor_kyc",
			"label": "Vendor KYC",
			"hidden": 1,
			"insert_after": "links",
			"read_only": 1,
			"module": MODULE,
		},
	],
	"Communication": [
		{
			"fieldname": "vendor_lifecycle_signoff_document_type",
			"label": "Sign-off Document Type",
			"fieldtype": "Select",
			"options": "\nContract\nCode of Conduct",
			"description": (
				"Set automatically by Vendor Lifecycle. On a Sign-off email this app sends, "
				"records which document that email was about. On a reply received back, records "
				"which document its attachment was matched to and copied onto."
			),
			"in_standard_filter": 1,
			"insert_after": "in_reply_to",
			"no_copy": 1,
			"print_hide": 1,
			"read_only": 1,
			"module": MODULE,
		},
	],
}

# Lock Supplier "Disabled" / "Is Frozen" for a vendor-lifecycle Supplier -
# only the app's own process (failed check, deboarding, ...) changes them.
PROPERTY_SETTERS = [
	{
		"doc_type": "Supplier",
		"field_name": "is_frozen",
		"property": "read_only_depends_on",
		"property_type": "Data",
		"value": "eval:doc.vendor_kyc",
	},
	{
		"doc_type": "Supplier",
		"field_name": "disabled",
		"property": "read_only_depends_on",
		"property_type": "Data",
		"value": "eval:doc.vendor_kyc",
	},
]

# Supplier form's Connections tab: (doctype, its field linking to Supplier).
SUPPLIER_CONNECTIONS = [
	("Vendor KYC", "supplier"),
	("Vendor Background Check", "vendor"),
	("Vendor Compliance Audit", "vendor"),
	("Vendor Sampling Evaluation", "vendor"),
	("Vendor Sign Off", "vendor"),
	("Vendor Satisfaction Survey", "vendor"),
	("Vendor Deboarding Request", "vendor"),
	("Vendor Deboarding Checklist", "supplier"),
	("Vendor Reboarding Request", "vendor"),
]

# Access to the ERPNext records the app creates for a vendor, and to the
# masters picked on a Bank Account. Not granted on purpose: delete anywhere;
# Company (every desk user can already select it); Account and Party Type
# (only used for company accounts / filled in by the app).
ERPNEXT_PERMISSIONS = {
	VENDOR_LIFECYCLE_MANAGER: {
		"Supplier": ("create", "read", "write"),
		"Address": ("create", "read", "write"),
		"Contact": ("create", "read", "write"),
		"Bank Account": ("create", "read", "write"),
		"Bank": ("read", "select"),
		"Bank Account Type": ("read", "select"),
		"Bank Account Subtype": ("read", "select"),
	},
	VENDOR_LIFECYCLE_USER: {
		"Supplier": ("read", "write"),
		"Address": ("read", "write"),
		"Contact": ("read", "write"),
		"Bank Account": ("read", "write"),
		"Bank": ("read", "select"),
		"Bank Account Type": ("read", "select"),
		"Bank Account Subtype": ("read", "select"),
	},
}


def install():
	"""after_install: everything, once."""
	create_missing()
	create_property_setters()
	grant_erpnext_permissions()


def create_missing():
	"""after_migrate: re-create only what has gone missing."""
	create_missing_roles()
	create_custom_fields(CUSTOM_FIELDS, update=False)
	create_missing_supplier_connections()


def create_missing_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role["role_name"]):
			frappe.get_doc({"doctype": "Role", **role}).insert(ignore_permissions=True)


def create_missing_supplier_connections():
	existing = set(
		frappe.get_all(
			"DocType Link",
			filters={"parent": "Supplier", "parenttype": "DocType"},
			fields=["link_doctype", "link_fieldname"],
			as_list=True,
		)
	)
	missing = [link for link in SUPPLIER_CONNECTIONS if link not in existing]
	for link_doctype, link_fieldname in missing:
		frappe.get_doc({
			"doctype": "DocType Link",
			"parent": "Supplier",
			"parenttype": "DocType",
			"parentfield": "links",
			"link_doctype": link_doctype,
			"link_fieldname": link_fieldname,
			"group": MODULE,
			"custom": 1,
		}).insert(ignore_permissions=True)
	if missing:
		frappe.clear_cache(doctype="Supplier")


def create_property_setters():
	for setter in PROPERTY_SETTERS:
		filters = {key: setter[key] for key in ("doc_type", "field_name", "property")}
		if frappe.db.exists("Property Setter", filters):
			continue
		frappe.get_doc({
			"doctype": "Property Setter",
			"doctype_or_field": "DocField",
			"module": MODULE,
			**setter,
		}).insert(ignore_permissions=True)
		frappe.clear_cache(doctype=setter["doc_type"])


# Options the app no longer uses — dropped by add_missing_supplier_status_options
# unless some Supplier still holds that value.
REMOVED_SUPPLIER_STATUS_OPTIONS = ("Signed",)


def add_missing_supplier_status_options():
	# Adds any vendor_lifecycle_status option the app now defines but the
	# site's Custom Field doesn't have yet, in the app's order, and drops
	# REMOVED_SUPPLIER_STATUS_OPTIONS — options the site added itself are
	# kept. For patches: create_custom_fields never updates an existing
	# field here.
	name = frappe.db.get_value("Custom Field", {"dt": "Supplier", "fieldname": "vendor_lifecycle_status"})
	if not name:
		return
	field = frappe.get_doc("Custom Field", name)
	current = (field.options or "").split("\n")
	wanted = next(f for f in CUSTOM_FIELDS["Supplier"] if f["fieldname"] == "vendor_lifecycle_status")
	options = list(current)
	for previous, option in zip(wanted["options"].split("\n"), wanted["options"].split("\n")[1:]):
		if option in options:
			continue
		position = options.index(previous) + 1 if previous in options else len(options)
		options.insert(position, option)
	for option in REMOVED_SUPPLIER_STATUS_OPTIONS:
		if option in options and not frappe.db.exists("Supplier", {"vendor_lifecycle_status": option}):
			options.remove(option)
	if options != current:
		field.options = "\n".join(options)
		field.save()


def grant_erpnext_permissions():
	"""Adds each role's rule only where the doctype has none for it yet, so
	re-running never undoes a site's own edits to an existing rule."""
	for role, doctypes in ERPNEXT_PERMISSIONS.items():
		for doctype, rights in doctypes.items():
			if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				continue
			add_permission(doctype, role, 0, "read")
			for right in rights:
				if right != "read":
					update_permission_property(doctype, role, 0, right, 1)
