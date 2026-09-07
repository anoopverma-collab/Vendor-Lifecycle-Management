import frappe
from frappe import _

from vendor_lifecycle.vendor_lifecycle.permissions import supplier_portal_has_permission

no_cache = 1


def get_context(context):
	context.show_sidebar = True
	name = frappe.form_dict.name
	doc = frappe.get_doc("Vendor Support Ticket", name)
	if not supplier_portal_has_permission(doc):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	context.doc = doc
	context.title = doc.subject
	# The vendor can edit the Description for as long as the ticket isn't
	# Closed (see validate() — a Closed ticket rejects a changed
	# description server-side too, this is just what drives the UI).
	context.can_edit_description = doc.status != "Closed"
	# frappe.client.save() reconstructs the document purely from whatever
	# dict it's given, so the edit form embeds the full current doc and
	# only ever mutates `description` before sending it back — same
	# pattern as the Satisfaction Survey portal edit page.
	context.doc_json = frappe.as_json(doc.as_dict())
