import frappe
from frappe import _

from vendor_lifecycle.vendor_lifecycle.permissions import get_active_supplier_portal_vendors, is_internal_user

no_cache = 1


def get_context(context):
	context.show_sidebar = True
	context.title = _("New Support Ticket")
	if frappe.session.user == "Guest":
		frappe.throw(_("Login to raise a support ticket."), frappe.PermissionError)

	# Only an active (enabled, not on hold) vendor can have a new ticket
	# filed against it — same rule Satisfaction Survey's scheduler uses.
	# Most portal users are only ever linked to one vendor, so the common
	# case never shows a picker at all: before_insert auto-fills it. A
	# picker only appears here when there's a genuine choice to make.
	context.vendors = [] if is_internal_user() else get_active_supplier_portal_vendors(frappe.session.user)
	if not is_internal_user() and not context.vendors:
		frappe.throw(
			_("Your account isn't linked to any active vendor — contact your account manager."),
			frappe.PermissionError,
		)
