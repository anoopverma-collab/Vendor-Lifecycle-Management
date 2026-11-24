// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Supplier"] = {
	refresh(listview) {
		// "refresh" (not "onload") because list views are cached per session —
		// onload only fires the first time this list is ever opened, so it'd
		// never notice the setting being toggled after that. refresh() runs
		// on every visit, so this stays correct either direction. This only
		// hides the list view's own "Add Supplier" button — the real
		// enforcement (blocking a brand-new Supplier with no Vendor KYC,
		// regardless of how it's being created) is the server-side check.
		if (frappe.boot.vendor_lifecycle_settings?.restrict_supplier_creation_to_vendor_lifecycle) {
			listview.can_create = false;
			listview.page.clear_primary_action();
		} else if (!listview.can_create) {
			listview.can_create = frappe.model.can_create(listview.doctype);
			listview.set_primary_action();
		}
	},
};
