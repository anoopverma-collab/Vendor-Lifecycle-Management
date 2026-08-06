# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import today

from vendor_lifecycle.vendor_lifecycle.integrations.dispatch import get_notification_handler


class VendorDeboardingChecklist(Document):
	@frappe.whitelist()
	def load_checklist_from_template(self):
		"""Populate checklist_items from checklist_template, replacing any existing rows."""
		if not self.checklist_template:
			frappe.throw(frappe._("Set a Checklist Template first."))

		template = frappe.get_doc("Vendor Deboarding Checklist Template", self.checklist_template)
		self.checklist_items = []
		for row in template.items:
			self.append("checklist_items", {
				"task": row.task,
				"category": row.category,
			})

	def validate(self):
		is_complete = bool(self.checklist_items) and all(row.is_done for row in self.checklist_items)
		was_complete = bool(self.completed_on)
		self.completed_on = today() if is_complete else None

		if is_complete and not was_complete:
			provider = frappe.db.get_single_value("Vendor Lifecycle Settings", "deboarding_notification_provider") or "Manual"
			get_notification_handler(provider).notify(self)
