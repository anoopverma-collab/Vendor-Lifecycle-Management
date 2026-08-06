# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import os

import frappe
from frappe.modules.import_file import import_file_by_path

# Frappe does not auto-import "Workspace Sidebar" / "Desktop Icon" standard
# files the way it does for DocType/Report/Page/Workspace — both are built
# dynamically from Workspace.shortcuts on install instead, which ignores
# these files entirely. Import them explicitly so the sidebar/app-icon this
# app ships are always in place, on install and on every migrate.
STANDARD_FILES = [
	("workspace_sidebar", "vendor_lifecycle.json"),
	("desktop_icon", "vendor_lifecycle.json"),
]


def sync_standard_files():
	for folder, filename in STANDARD_FILES:
		path = frappe.get_app_path("vendor_lifecycle", folder, filename)
		if os.path.exists(path):
			import_file_by_path(path, force=True)


def after_install():
	sync_standard_files()


def after_migrate():
	sync_standard_files()
	backfill_settings_defaults()
	rename_ndc_terminology()
	migrate_converted_request_status()


# "NDC" (No Dues Certificate) was renamed to the region-neutral "Clearance
# Certificate" — a site that already had a value stored under the old option
# names would otherwise be left with a value that no longer matches either
# of the current Select options.
NDC_TO_CLEARANCE = {
	"After NDC": "After Clearance",
	"Before NDC": "Before Clearance",
}


def rename_ndc_terminology():
	current = frappe.db.get_single_value("Vendor Lifecycle Settings", "disable_timing")
	if current in NDC_TO_CLEARANCE:
		frappe.db.set_single_value("Vendor Lifecycle Settings", "disable_timing", NDC_TO_CLEARANCE[current])


def migrate_converted_request_status():
	# Vendor Onboarding Request's status options went from Draft/Submitted/
	# Converted to Draft/Accepted/Rejected, once an explicit Accept/Reject
	# gate was added ahead of Start KYC. A request already past Draft (i.e.
	# it already has a Vendor KYC) is, by definition, one that was accepted.
	frappe.db.set_value(
		"Vendor Onboarding Request", {"status": ["in", ("Submitted", "Converted")]}, "status", "Accepted"
	)


SETTINGS_FIELD_DEFAULTS = {
	"duplicate_request_handling": "Warn",
	"request_edit_override_role": "System Manager",
	"require_onboarding_request_for_kyc": 1,
	"duplicate_kyc_handling": "Stop",
	"create_vendor_at": "Vendor KYC",
	"enforce_sequential_stages": 1,
	"require_complete_bank_details": 1,
	"require_complete_address_details": 1,
	"minimum_average_rating": 3,
}


def backfill_settings_defaults():
	# A Single doctype's field default only applies when that field is first
	# created on a brand new site. On a site that already had "Vendor
	# Lifecycle Settings" before one of these fields existed, migrate adds
	# the column but leaves it empty — so fill it in explicitly, once.
	#
	# Checked by row existence in `tabSingles`, not by truthiness of the
	# value: a Check field backfilled to "1" is legitimately "0" once an
	# admin unchecks it, and a falsy-value check would keep flipping it
	# back to the default on every migrate.
	for fieldname, default in SETTINGS_FIELD_DEFAULTS.items():
		if not frappe.db.exists("Singles", {"doctype": "Vendor Lifecycle Settings", "field": fieldname}):
			frappe.db.set_single_value("Vendor Lifecycle Settings", fieldname, default)
