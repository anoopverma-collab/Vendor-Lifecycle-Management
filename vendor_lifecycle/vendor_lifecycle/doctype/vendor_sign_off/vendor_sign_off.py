# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vendor_lifecycle.vendor_lifecycle.integrations.dispatch import get_esign_handler
from vendor_lifecycle.vendor_lifecycle.stage_sequencing import enforce_sequential_creation
from vendor_lifecycle.vendor_lifecycle.vendor_creation import maybe_create_vendor, sync_vendor_field

VENDOR_LIFECYCLE_STATUS_ACTIVE = "Active"


class VendorSignOff(Document):
	def validate(self):
		sync_vendor_field(self)
		enforce_sequential_creation(self)

	def before_insert(self):
		self.requested_on = now_datetime()

	def after_insert(self):
		get_esign_handler(self.provider).request(self)

	def before_submit(self):
		self._enforce_mandatory_stages()

	def _enforce_mandatory_stages(self):
		settings = frappe.get_single("Vendor Lifecycle Settings")
		missing = []

		if settings.audit_mandatory and not frappe.db.exists(
			"Vendor Compliance Audit", {"kyc": self.kyc, "docstatus": 1, "result": "Passed"}
		):
			missing.append(frappe._("a passed Compliance Audit"))

		if settings.sampling_mandatory and not frappe.db.exists(
			"Vendor Sampling Evaluation", {"kyc": self.kyc, "docstatus": 1}
		):
			missing.append(frappe._("a submitted Sampling Evaluation"))

		if settings.reference_check_mandatory and not frappe.db.exists(
			"Vendor Reference Check", {"kyc": self.kyc, "docstatus": 1}
		):
			missing.append(frappe._("a submitted Reference Check"))

		if missing:
			frappe.throw(frappe._("Sign-off is blocked until this vendor has: {0}.").format(", ".join(missing)))

	def on_submit(self):
		get_esign_handler(self.provider).confirm_signed(self)
		self.signed_on = now_datetime()
		self.db_set("signed_on", self.signed_on)

		maybe_create_vendor(self)
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, {
				"vendor_lifecycle_status": VENDOR_LIFECYCLE_STATUS_ACTIVE,
				"on_hold": 0,
				"hold_type": "",
			})
