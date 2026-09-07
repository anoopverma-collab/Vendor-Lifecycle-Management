# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import cint


def set_bootinfo(bootinfo):
	settings = frappe.db.get_singles_dict("Vendor Lifecycle Settings")
	bootinfo.vendor_lifecycle_settings = {
		# Singles values come back from the database as strings — "0" is
		# truthy in JS, so anything checking this straight off boot (both
		# here and in the "Vendor KYC Direct Create Notice" client script)
		# would treat the setting as always-on unless it's cast to a real
		# number first.
		"require_onboarding_request_for_kyc": cint(settings.get("require_onboarding_request_for_kyc")),
		"duplicate_kyc_handling": settings.get("duplicate_kyc_handling") or "Stop",
		"restrict_supplier_creation_to_vendor_lifecycle": cint(
			settings.get("restrict_supplier_creation_to_vendor_lifecycle")
		),
	}
