# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DeboardingRatingTemplate(Document):
	def validate(self):
		self._validate_no_duplicate_criteria()

	def _validate_no_duplicate_criteria(self):
		# A Criteria listed twice would show up twice on every deboarding
		# request that resolves to this template — confusing, not just
		# redundant.
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
