# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.stage_sequencing import enforce_sequential_creation
from vendor_lifecycle.vendor_lifecycle.vendor_creation import maybe_create_vendor, sync_vendor_field

VENDOR_LIFECYCLE_STATUS_SAMPLING_APPROVED = "Sampling Approved"


class VendorSamplingEvaluation(Document):
	def validate(self):
		sync_vendor_field(self)
		enforce_sequential_creation(self)

	@frappe.whitelist()
	def load_evaluation_from_template(self):
		"""Populate evaluation_results from evaluation_template, replacing any existing rows."""
		if not self.evaluation_template:
			frappe.throw(frappe._("Set an Evaluation Template first."))

		template = frappe.get_doc("Sampling Evaluation Template", self.evaluation_template)
		self.evaluation_results = []
		for row in template.criteria:
			self.append("evaluation_results", {
				"criteria": row.criteria,
				"category": row.category,
			})

	def on_submit(self):
		maybe_create_vendor(self)
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_status", VENDOR_LIFECYCLE_STATUS_SAMPLING_APPROVED)
