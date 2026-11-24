# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ComplianceCheckTemplate(Document):
	def validate(self):
		self._validate_no_duplicate_check_type()

	def _validate_no_duplicate_check_type(self):
		# A Check Type listed twice on this template would create two
		# identical rows on every Background Check that loads from it,
		# with no clear meaning for having the same check appear twice.
		seen = set()
		for row in self.items:
			if not row.check_type:
				continue
			if row.check_type in seen:
				frappe.throw(
					frappe._("Row #{0}: Check Type {1} is already in this template — each Check Type can only appear once.").format(
						row.idx, frappe.bold(row.check_type)
					)
				)
			seen.add(row.check_type)
