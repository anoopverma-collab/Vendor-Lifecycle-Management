// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Vendor Compliance Audit"] = {
	refresh(listview) {
		// The only sanctioned ways to create one are Vendor KYC's own
		// "Create" dropdown and the "Compliance Audit" convenience button
		// on Vendor Background Check (both gated by get_available_stages(),
		// which enforces one active Compliance Audit per vendor and correct
		// stage sequencing) — the list view's own "Add" button bypasses
		// that check entirely, so it's hidden.
		listview.can_create = false;
		listview.page.clear_primary_action();
	},
};
