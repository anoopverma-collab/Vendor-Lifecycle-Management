# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vendor_lifecycle.vendor_lifecycle.permissions import get_supplier_portal_vendors, is_internal_user


class VendorSupportTicket(Document):
	def before_insert(self):
		if not self.vendor and not is_internal_user():
			vendors = get_supplier_portal_vendors(frappe.session.user)
			if vendors:
				self.vendor = vendors[0]
		self.opened_on = now_datetime()
		self.status = "Open"

	def validate(self):
		if is_internal_user():
			return
		if self.vendor not in get_supplier_portal_vendors(frappe.session.user):
			frappe.throw(frappe._("You can only raise a ticket for your own vendor account."))

	@frappe.whitelist()
	def mark_resolved(self, resolution=None):
		if not is_internal_user():
			frappe.throw(frappe._("Only the internal team can mark a ticket resolved."))
		self.db_set("resolution", resolution or self.resolution)
		self.db_set("status", "Resolved")

	@frappe.whitelist()
	def close_ticket(self, resolution_rating=None):
		if self.status != "Resolved":
			frappe.throw(frappe._("The ticket must be marked Resolved by the team before it can be closed."))
		self.db_set("status", "Closed")
		self.db_set("closed_on", now_datetime())
		if resolution_rating is not None:
			self.db_set("resolution_rating", resolution_rating)
