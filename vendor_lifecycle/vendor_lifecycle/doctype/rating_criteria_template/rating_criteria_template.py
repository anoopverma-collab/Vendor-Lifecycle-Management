# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class RatingCriteriaTemplate(Document):
	def validate(self):
		self._reset_minimum_average_rating_if_method_blank()
		self._validate_minimum_average_rating()
		self._validate_no_duplicate_criteria()

	def _validate_no_duplicate_criteria(self):
		# A Criteria listed twice on this template would silently
		# double-weight it in the average (and double-count its vote for
		# the pass-count methods) on every Reference that loads from it.
		seen = set()
		for row in self.criteria:
			if not row.criteria:
				continue
			if row.criteria in seen:
				frappe.throw(
					frappe._("Row #{0}: Criteria {1} is already in this template — each Criteria can only appear once.").format(
						row.idx, frappe.bold(row.criteria)
					)
				)
			seen.add(row.criteria)

	def _reset_minimum_average_rating_if_method_blank(self):
		# Leaving Result Method blank means "defer to Vendor Lifecycle
		# Settings for both Result Method and Minimum Average Rating" — a
		# leftover/custom rating on this template must not silently keep
		# applying once the method itself is deferred, and it's hidden on
		# the form in that case too (see the field's depends_on).
		if not self.result_method:
			self.minimum_average_rating = 0

	def _validate_minimum_average_rating(self):
		# 0/0.0 means "not set" (falls back to Vendor Lifecycle Settings),
		# not an out-of-range value — same treatment as a Reference's own
		# blank/0 Score.
		if self.minimum_average_rating and not (1 <= self.minimum_average_rating <= 5):
			frappe.throw(frappe._("Minimum Average Rating must be between 1 and 5."))
