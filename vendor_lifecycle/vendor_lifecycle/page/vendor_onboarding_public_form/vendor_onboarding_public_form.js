// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.pages["vendor-onboarding-public-form"].on_page_load = function (wrapper) {
	// Workspace Card links only support DocType/Page/Report (no arbitrary
	// URL), so this Page exists solely to give the public, guest-facing
	// web form a real Desk route it can be linked to from inside a Card's
	// own link list. Opens in a new tab, then sends this tab straight back
	// to the workspace so clicking the link doesn't lose the user's place.
	window.open("/vendor-onboarding-request", "_blank");
	frappe.set_route("vendor-lifecycle");
};
