// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Vendor Deboarding Checklist"] = {
	refresh(listview) {
		// The only sanctioned way to create one is the "Create" dropdown
		// on Vendor Deboarding Request — the list view's own "Add" button
		// bypasses that (and leaves deboarding_request to be typed in by
		// hand), so it's hidden.
		listview.can_create = false;
		listview.page.clear_primary_action();
	},
};
