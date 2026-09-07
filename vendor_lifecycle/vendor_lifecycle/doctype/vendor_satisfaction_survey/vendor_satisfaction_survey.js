// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Satisfaction Survey", {
	refresh(frm) {
		// cannot_add_rows/cannot_delete_rows aren't real DocField
		// properties (no such column on DocField) — they're grid options
		// that only take effect when set on the live grid object, same as
		// core's doctype_layout.js does for its own locked table.
		const grid = frm.fields_dict.ratings.grid;
		grid.cannot_add_rows = true;
		grid.cannot_delete_rows = true;
		grid.refresh();

		// Suppress Frappe's own default "Submit this document to confirm"
		// draft banner (form.js show_submit_message(), always shown for a
		// submittable doctype's draft) — this handler runs after
		// refresh_header() sets it (script_manager fires "refresh" after
		// refresh_header in render_form()), so clearing it here reliably
		// removes it every time the form refreshes. before_submit() on the
		// server still enforces the all-ratings-scored requirement either way.
		if (frm.doc.docstatus === 0) {
			frm.dashboard.clear_headline();
		}
	},
});
