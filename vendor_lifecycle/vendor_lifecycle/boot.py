# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def set_bootinfo(bootinfo):
	settings = frappe.db.get_singles_dict("Vendor Lifecycle Settings")
	bootinfo.vendor_lifecycle_settings = {
		"require_onboarding_request_for_kyc": settings.get("require_onboarding_request_for_kyc"),
		"duplicate_kyc_handling": settings.get("duplicate_kyc_handling") or "Stop",
	}
