# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VendorLifecycleSettings(Document):
	def validate(self):
		self._validate_reference_minimum_average_rating()

	def _validate_reference_minimum_average_rating(self):
		# Optional (0/0.0 means "not set", falls back to a Rating Criteria
		# Template's own value or blocks at the Reference's own save) — but
		# must be 1-5 if actually set.
		if self.reference_minimum_average_rating and not (1 <= self.reference_minimum_average_rating <= 5):
			frappe.throw(frappe._("Minimum Average Rating (Reference) must be between 1 and 5."))
