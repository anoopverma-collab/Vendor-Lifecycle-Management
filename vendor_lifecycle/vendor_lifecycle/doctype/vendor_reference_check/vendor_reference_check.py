# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.stage_sequencing import enforce_sequential_creation
from vendor_lifecycle.vendor_lifecycle.vendor_creation import maybe_create_vendor, sync_vendor_field

VENDOR_LIFECYCLE_STATUS_REFERENCE_VERIFIED = "Reference Verified"


class VendorReferenceCheck(Document):
	@frappe.whitelist()
	def load_ratings_from_template(self):
		"""Populate ratings from rating_template, replacing any existing rows."""
		if not self.rating_template:
			frappe.throw(frappe._("Set a Rating Criteria Template first."))

		template = frappe.get_doc("Rating Criteria Template", self.rating_template)
		self.ratings = []
		for row in template.criteria:
			self.append("ratings", {"criteria": row.criteria})

	def validate(self):
		self._validate_scores()
		self.average_rating = self._compute_average_score()
		self.result = self._compute_result()
		sync_vendor_field(self)
		enforce_sequential_creation(self)

	def _validate_scores(self):
		for row in self.ratings:
			if row.score is not None and not (1 <= row.score <= 5):
				frappe.throw(frappe._("Row #{0}: Score must be between 1 and 5.").format(row.idx))

	def _compute_average_score(self):
		scores = [row.score for row in self.ratings if row.score]
		if not scores:
			return None
		return sum(scores) / len(scores)

	def _compute_result(self):
		if self.average_rating is None:
			return "Needs Review"
		threshold = self._resolve_minimum_rating()
		return "Passed" if self.average_rating >= threshold else "Failed"

	def _resolve_minimum_rating(self):
		if self.rating_template:
			template_value = frappe.db.get_value("Rating Criteria Template", self.rating_template, "minimum_average_rating")
			if template_value:
				return template_value

		settings_value = frappe.db.get_single_value("Vendor Lifecycle Settings", "minimum_average_rating")
		if settings_value:
			return settings_value

		frappe.throw(
			frappe._(
				"Set a Minimum Average Rating on the Rating Criteria Template or in Vendor Lifecycle Settings before saving a Reference Check with ratings filled in."
			)
		)

	def on_submit(self):
		if self.result == "Failed":
			self._handle_failed_result()
			return

		maybe_create_vendor(self)
		if self.vendor and self.result == "Passed":
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_status", VENDOR_LIFECYCLE_STATUS_REFERENCE_VERIFIED)

	def _handle_failed_result(self):
		# A Supplier already created at an earlier stage gets disabled. One
		# that would've been created at *this* stage (per "Create Vendor At"
		# in Settings) simply never gets created — maybe_create_vendor() is
		# never called on this path. Either way, enforce_sequential_creation()
		# already blocks Vendor Sign Off from starting without a "Passed"
		# Reference Check, so no separate stage-blocking logic is needed here.
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 1)
