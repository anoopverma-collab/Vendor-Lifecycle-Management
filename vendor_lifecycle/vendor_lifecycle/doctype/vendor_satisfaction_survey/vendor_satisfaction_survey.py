# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.permissions import get_supplier_portal_vendors, is_internal_user


class VendorSatisfactionSurvey(Document):
	def before_insert(self):
		if not self.vendor and not is_internal_user():
			# Portal (Supplier-role) users submit via the web form, which has
			# no vendor field — infer it from their own Portal User link.
			vendors = get_supplier_portal_vendors(frappe.session.user)
			if vendors:
				self.vendor = vendors[0]

	def validate(self):
		if is_internal_user():
			return
		# Portal (Supplier-role) users can only ever file a survey against
		# their own linked vendor — belt-and-suspenders alongside the
		# permission_query_conditions hook, which only filters list views.
		if self.vendor not in get_supplier_portal_vendors(frappe.session.user):
			frappe.throw(frappe._("You can only submit a survey for your own vendor account."))
