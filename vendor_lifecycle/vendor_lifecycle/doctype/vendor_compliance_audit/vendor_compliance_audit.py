# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.stage_sequencing import enforce_sequential_creation
from vendor_lifecycle.vendor_lifecycle.vendor_creation import maybe_create_vendor, sync_vendor_field

VENDOR_LIFECYCLE_STATUS_AUDIT_VERIFIED = "Audit Verified"


class VendorComplianceAudit(Document):
	@frappe.whitelist()
	def load_checklist_from_template(self):
		"""Populate checklist_items from checklist_template, replacing any existing rows."""
		if not self.checklist_template:
			frappe.throw(frappe._("Set a Checklist Template first."))

		template = frappe.get_doc("Audit Checklist Template", self.checklist_template)
		self.checklist_items = []
		for row in template.items:
			self.append("checklist_items", {
				"item": row.item,
				"category": row.category,
			})

	def validate(self):
		self.result = self.compute_result()
		sync_vendor_field(self)
		enforce_sequential_creation(self)

	def compute_result(self):
		if not self.checklist_items:
			return "Needs Review"
		if any(row.response == "Fail" for row in self.checklist_items):
			return "Failed"
		if all(row.response == "Pass" for row in self.checklist_items):
			return "Passed"
		return "Needs Review"

	def on_submit(self):
		maybe_create_vendor(self)
		if self.vendor and self.result == "Passed":
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_status", VENDOR_LIFECYCLE_STATUS_AUDIT_VERIFIED)
