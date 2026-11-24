# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AuditChecklistTemplate(Document):
	def validate(self):
		self._validate_no_duplicate_item()

	def _validate_no_duplicate_item(self):
		# A checklist item listed twice on this template would create two
		# identical rows on every Compliance Audit that loads from it, with
		# no clear meaning for having the same item appear twice.
		seen = set()
		for row in self.items:
			if not row.item:
				continue
			if row.item in seen:
				frappe.throw(
					frappe._("Row #{0}: {1} is already in this template — each Checklist Item can only appear once.").format(
						row.idx, frappe.bold(row.item)
					)
				)
			seen.add(row.item)
