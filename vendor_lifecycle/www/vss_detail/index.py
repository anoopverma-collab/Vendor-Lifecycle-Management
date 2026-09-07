import frappe
from frappe import _

from vendor_lifecycle.vendor_lifecycle.permissions import supplier_portal_has_permission

no_cache = 1


def get_context(context):
	context.show_sidebar = True
	name = frappe.form_dict.name
	doc = frappe.get_doc("Vendor Satisfaction Survey", name)
	if not supplier_portal_has_permission(doc):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	context.doc = doc
	context.title = _("Satisfaction Survey")
	# Editable for as long as it's a draft — filling in a rating no longer
	# flips this to a read-only view on its own; the survey only becomes
	# read-only once it's actually Submitted (see status/docstatus), by
	# either the vendor or an internal user hitting Submit.
	context.is_pending = doc.docstatus == 0
	# Fed to the edit form's JS as the base object frappe.client.save merges
	# changes into — frappe.client.save() reconstructs the document purely
	# from whatever dict it's given (it does not fetch-then-patch), so
	# omitting a field here would silently blank it out rather than leave
	# it alone. Embedding the full current doc up front is what keeps
	# vendor/survey_date/each rating row's own Criteria/etc. intact
	# through an edit — the JS only ever touches score/issues/remarks.
	context.doc_json = frappe.as_json(doc.as_dict())
