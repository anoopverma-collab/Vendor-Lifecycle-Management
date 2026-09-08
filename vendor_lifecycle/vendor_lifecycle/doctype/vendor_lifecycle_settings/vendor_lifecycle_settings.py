# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VendorLifecycleSettings(Document):
	def validate(self):
		self._validate_reference_minimum_average_rating()
		self._validate_email_account_required()

	def _validate_reference_minimum_average_rating(self):
		# Optional (0/0.0 means "not set", falls back to a Rating Criteria
		# Template's own value or blocks at the Reference's own save) — but
		# must be 1-5 if actually set.
		if self.reference_minimum_average_rating and not (1 <= self.reference_minimum_average_rating <= 5):
			frappe.throw(frappe._("Minimum Average Rating (Reference) must be between 1 and 5."))

	def _validate_email_account_required(self):
		# Email Account's own mandatory_depends_on is JS-only — Frappe's
		# framework never enforces mandatory_depends_on server-side, so
		# without this check Settings could be saved with emails on and no
		# account configured. The real send path (require_vendor_lifecycle_
		# email_account) already throws a clear error at send-time either
		# way, but catching the contradiction here, at the point it's
		# actually introduced, is better than only discovering it when the
		# first email tries to go out.
		if self.enable_vendor_lifecycle_emails and not self.vendor_lifecycle_email_account:
			frappe.throw(frappe._("Set an Email Account before enabling Use Emails."))
