// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Vendor Sign Off"] = {
	refresh(listview) {
		// The only sanctioned ways to create one are Vendor KYC's own
		// "Create" dropdown and the "Sign-off" convenience button on
		// Vendor Sampling Evaluation (both gated by get_available_stages(),
		// which enforces one active Sign Off per vendor, correct stage
		// sequencing, and the hard stops on a Failed Background Check /
		// Failed Compliance Audit / Rejected Sampling Evaluation) — the
		// list view's own "Add" button bypasses that check entirely, so
		// it's hidden.
		listview.can_create = false;
		listview.page.clear_primary_action();
	},
};
