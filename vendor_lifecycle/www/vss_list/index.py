import frappe
from frappe import _
from frappe.utils import formatdate

from vendor_lifecycle.vendor_lifecycle.permissions import get_supplier_portal_vendors, is_internal_user

no_cache = 1


def get_context(context):
	context.show_sidebar = True
	context.title = _("Satisfaction Surveys")
	if frappe.session.user == "Guest":
		frappe.throw(_("Login to view your surveys."), frappe.PermissionError)
	if not is_internal_user() and not get_supplier_portal_vendors(frappe.session.user):
		frappe.throw(
			_("Your account isn't linked to a vendor yet — contact your account manager."), frappe.PermissionError
		)

	# frappe.get_list() (not get_all) applies the doctype's own
	# permission_query_conditions/has_permission hooks automatically — a
	# portal (Supplier) user only ever gets rows for their own linked
	# vendor, the same scoping every other view of this data relies on.
	surveys = frappe.get_list(
		"Vendor Satisfaction Survey",
		fields=["name", "period", "survey_date", "status"],
		order_by="creation desc",
	)
	for survey in surveys:
		survey.survey_date = formatdate(survey.survey_date)
	context.surveys = surveys
