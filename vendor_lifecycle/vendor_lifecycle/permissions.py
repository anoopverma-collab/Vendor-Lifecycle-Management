# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import functools

import frappe

INTERNAL_ROLES = ("Vendor Lifecycle Manager", "Vendor Lifecycle User", "System Manager")


def is_internal_user(user=None):
	roles = frappe.get_roles(user or frappe.session.user)
	return any(role in roles for role in INTERNAL_ROLES)


def check_app_permission():
	# Shows the app on the Apps screen (hooks.py add_to_apps_screen) to staff
	# only, never to Supplier portal users.
	return frappe.session.user == "Administrator" or is_internal_user()


def require_internal_user():
	# For whitelisted methods that return site-wide or other vendors' data —
	# a Supplier portal user can call any whitelisted method directly.
	if not is_internal_user():
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)


def internal_only(fn):
	"""Decorator form of require_internal_user — goes ABOVE @cache_source on
	a dashboard chart source so the check runs even when the cached result
	is returned."""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		require_internal_user()
		return fn(*args, **kwargs)

	return wrapper


def get_supplier_portal_vendors(user):
	"""Suppliers the given user is a Portal User for — same lookup ERPNext's
	own supplier/customer portal permission check uses (Supplier's child
	table `portal_users`)."""
	portal_user = frappe.qb.DocType("Portal User")
	return (
		frappe.qb.from_(portal_user)
		.select(portal_user.parent)
		.where(portal_user.user == user)
		.where(portal_user.parenttype == "Supplier")
	).run(pluck="name")


def get_active_supplier_portal_vendors(user):
	"""Same as get_supplier_portal_vendors, filtered down to Suppliers that
	are currently enabled and not on hold — a portal user shouldn't be able
	to file anything new against a vendor account that's since been
	disabled/frozen, even though the Portal User link itself is still
	there. Used for creation-time vendor resolution only (which vendor a
	new record gets attached to) — not for read-scoping existing records,
	so a vendor doesn't lose visibility into their own history just because
	the account was disabled later."""
	vendors = get_supplier_portal_vendors(user)
	if not vendors:
		return []
	return frappe.get_all(
		"Supplier",
		filters={"name": ["in", vendors], "disabled": 0, "on_hold": 0},
		pluck="name",
	)


def supplier_portal_permission_query_conditions(user, doctype):
	if not user:
		user = frappe.session.user
	if is_internal_user(user):
		return ""

	vendors = get_supplier_portal_vendors(user)
	if not vendors:
		return "1=0"

	quoted = ", ".join(frappe.db.escape(v) for v in vendors)
	return f"`tab{doctype}`.vendor in ({quoted})"


def supplier_portal_has_permission(doc, user=None):
	if not user:
		user = frappe.session.user
	if is_internal_user(user):
		return True

	vendors = get_supplier_portal_vendors(user)
	if not vendors:
		return False
	if not doc.vendor:
		# New, unsaved doc from a portal user — before_insert on the
		# controller fills `vendor` in from this same lookup before the row
		# is written, and validate() re-checks it afterwards. At this point
		# (permission check on the empty draft) just confirm the user has
		# *some* linked vendor.
		return True
	return doc.vendor in vendors


def vendor_satisfaction_survey_query_conditions(user):
	return supplier_portal_permission_query_conditions(user, "Vendor Satisfaction Survey")


def vendor_satisfaction_survey_has_permission(doc, ptype=None, user=None):
	return supplier_portal_has_permission(doc, user)
