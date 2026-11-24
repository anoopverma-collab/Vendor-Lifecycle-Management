# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def before_uninstall():
	"""Remove the Connections links this app added to the Supplier form.

	They are DocType Link rows with no module, so Frappe's own uninstall
	leaves them behind, and the Supplier form's Connections tab then fails
	with "DocType ... not found" for the app's deleted doctypes. Install and
	every migrate add them back (setup.create_missing_supplier_connections)."""
	from vendor_lifecycle.setup import SUPPLIER_CONNECTIONS

	for link_doctype, link_fieldname in SUPPLIER_CONNECTIONS:
		frappe.db.delete(
			"DocType Link",
			{
				"parent": "Supplier",
				"parenttype": "DocType",
				"link_doctype": link_doctype,
				"link_fieldname": link_fieldname,
				"custom": 1,
			},
		)
	frappe.clear_cache(doctype="Supplier")


def after_uninstall():
	"""Delete the Workspace Sidebar that install.sync_standard_files imports.

	Its "module" field is plain text (not a Module Def link), so Frappe's own
	uninstall doesn't remove it with the module and it would be left behind.
	A reinstall imports it again."""
	# `bench uninstall-app --dry-run` runs this hook too but never removes the
	# app from installed apps - change nothing then.
	if "vendor_lifecycle" in frappe.get_installed_apps():
		return
	if not frappe.db.exists("DocType", "Workspace Sidebar"):
		return
	for name in frappe.get_all(
		"Workspace Sidebar", or_filters={"app": "vendor_lifecycle", "module": "Vendor Lifecycle"}, pluck="name"
	):
		frappe.delete_doc("Workspace Sidebar", name, ignore_permissions=True, force=True)
	# after_uninstall runs after uninstall's own commit, so persist this here.
	frappe.db.commit()  # nosemgrep
