# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LicensesandPermitsTemplate(Document):
	def validate(self):
		self._validate_no_duplicate_item()

	def _validate_no_duplicate_item(self):
		# A license type listed twice on this template would create two
		# identical rows on every Compliance Audit that loads from it, with
		# no clear meaning for having the same type appear twice.
		seen = set()
		for row in self.items:
			if not row.license_type:
				continue
			if row.license_type in seen:
				frappe.throw(
					frappe._(
						"Row #{0}: {1} is already in this template — each License / Permit Type can only"
						" appear once."
					).format(row.idx, frappe.bold(row.license_type))
				)
			seen.add(row.license_type)
