# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

OPEN_PO_STATUSES = ["Draft", "To Receive and Bill", "To Bill", "To Receive"]


class VendorDeboardingRequest(Document):
	def before_submit(self):
		if not self.clearance_attachment and not self.clearance_exception_reason:
			frappe.throw(
				frappe._("Attach the clearance certificate, or give a reason it's missing, before submitting.")
			)

	def after_insert(self):
		if self._disable_timing() == "Before Clearance":
			self.disable_vendor_now()

	def on_submit(self):
		if self._disable_timing() == "After Clearance":
			self.disable_vendor_now()
		else:
			# Already disabled on insert for the "Before Clearance" setting —
			# submit just finalizes the paperwork.
			self.db_set("status", "Vendor Disabled")

	def _disable_timing(self):
		return frappe.db.get_single_value("Vendor Lifecycle Settings", "disable_timing") or "After Clearance"

	@frappe.whitelist()
	def disable_vendor_now(self):
		"""Manual override: disable the vendor immediately, regardless of the
		site's default disable_timing — exposed as a button on the form."""
		frappe.db.set_value("Supplier", self.vendor, {
			"disabled": 1,
			"vendor_lifecycle_status": "Disabled",
		})
		self.db_set("status", "Vendor Disabled")

	@frappe.whitelist()
	def get_open_transactions(self):
		open_purchase_orders = frappe.get_all(
			"Purchase Order",
			filters={"supplier": self.vendor, "docstatus": 1, "status": ["in", OPEN_PO_STATUSES]},
			fields=["name", "status", "grand_total"],
		)
		unpaid_purchase_invoices = frappe.get_all(
			"Purchase Invoice",
			filters={"supplier": self.vendor, "docstatus": 1, "outstanding_amount": [">", 0]},
			fields=["name", "outstanding_amount"],
		)
		return {
			"open_purchase_orders": open_purchase_orders,
			"unpaid_purchase_invoices": unpaid_purchase_invoices,
		}
