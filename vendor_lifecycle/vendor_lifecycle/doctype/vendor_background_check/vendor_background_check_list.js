// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Vendor Background Check"] = {
	refresh(listview) {
		// The only sanctioned way to create one is the "Create" dropdown
		// on Vendor KYC (gated by get_available_stages(), which enforces
		// one active Background Check per vendor) — the list view's own
		// "Add" button bypasses that check entirely, so it's hidden.
		listview.can_create = false;
		listview.page.clear_primary_action();
	},
};
