// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Vendor KYC"] = {
	refresh(listview) {
		// "refresh" (not "onload") because list views are cached per session —
		// onload only fires the first time this list is ever opened, so it'd
		// never notice the setting being toggled after that. refresh() runs
		// on every visit, so this stays correct either direction.
		if (frappe.boot.vendor_lifecycle_settings?.require_onboarding_request_for_kyc) {
			listview.can_create = false;
			listview.page.clear_primary_action();
		} else if (!listview.can_create) {
			listview.can_create = frappe.model.can_create(listview.doctype);
			listview.set_primary_action();
		}
	},
};
