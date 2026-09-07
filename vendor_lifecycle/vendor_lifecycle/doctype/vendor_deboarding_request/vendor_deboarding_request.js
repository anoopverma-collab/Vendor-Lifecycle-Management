// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Deboarding Request", {
	refresh(frm) {
		// The Ratings table is meant to be a fixed set of criteria resolved
		// from the site's Default Deboarding Rating Template — staff score
		// the rows, they don't add/remove them.
		if (frm.fields_dict.ratings) {
			const grid = frm.fields_dict.ratings.grid;
			// cannot_add_rows is read from either the grid itself or grid.df;
			// cannot_delete_rows (grid_row.js) is only ever read from grid.df
			// — setting it on the grid object alone silently does nothing.
			grid.cannot_add_rows = true;
			grid.df.cannot_add_rows = true;
			grid.df.cannot_delete_rows = true;
			grid.refresh();
		}

		if (frm.doc.docstatus === 1 && frm.doc.status === "Approved") {
			frm.add_custom_button(__("Checklist"), () => {
				frappe.new_doc("Vendor Deboarding Checklist", { deboarding_request: frm.doc.name });
			}, __("Create"));
		}

		// Real-time, not only-after-save: a brand new request gets its
		// Ratings rows populated as soon as the form opens, so the user can
		// score them before ever hitting Save (Save is blocked server-side
		// until every row is scored).
		if (frm.is_new() && !frm.doc.ratings.length) {
			// A plain module-level whitelisted function, not a doc method —
			// frm.call() always routes through run_doc_method (getattr on
			// the document instance), which only works for actual methods.
			frappe.call({
				method:
					"vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_request.vendor_deboarding_request.get_default_ratings",
				callback: (r) => {
					(r.message || []).forEach((row) => frm.add_child("ratings", row));
					frm.refresh_field("ratings");
				},
			});
		}

		if (!frm.doc.vendor || frm.is_new()) return;

		frm.call("get_open_transactions").then((r) => {
			const data = r.message || {};
			const cards = [
				{ label: __("Open Purchase Orders"), value: data.open_po_count || 0, color: "#e74c3c" },
				{ label: __("Unpaid Purchase Invoices"), value: data.unpaid_pi_count || 0, color: "#f39c12" },
				{ label: __("Advance Payments"), value: data.advance_count || 0, color: "#2980b9" },
			];

			let html = `<div style="display:flex; gap:12px; flex-wrap:wrap; align-items:stretch;">`;
			cards.forEach((card) => {
				html += `
					<div style="flex:1 1 0; min-width:150px; border-radius:8px; padding:14px; background:${card.color}; color:#fff; display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center;">
						<div style="font-size:26px; font-weight:700;">${card.value}</div>
						<div style="font-size:12px; opacity:0.9;">${card.label}</div>
					</div>`;
			});
			html += `</div>
				<div class="text-muted" style="margin-top:8px;">
					${__("Full itemized details are on the linked Vendor Deboarding Checklist.")}
				</div>`;

			frm.set_df_property("open_transactions_summary", "options", html);
			frm.refresh_field("open_transactions_summary");
		});

		if (frm.doc.docstatus === 0 && frm.doc.status !== "Rejected") {
			frm.add_custom_button(__("Reject"), () => {
				frappe.confirm(
					__("This rejects the deboarding request and locks it from further edits. Continue?"),
					() => frm.call("reject").then(() => frm.reload_doc())
				);
			});
		}

		// Only offered once the request itself is Approved — before that,
		// submitting the request has no automatic effect on the vendor at
		// all, so freezing shouldn't be possible from here either. Same
		// underlying mechanism as Vendor KYC's own Freeze/Unfreeze Supplier
		// button — the server checks the same role again inside
		// toggle_supplier_freeze() itself, in case of a stale page or a
		// direct API call.
		if (frm.doc.docstatus === 1) {
			frm.call("get_supplier_freeze_button_info").then((r) => {
				const info = r.message || {};
				if (!info.show) return;

				const label = info.is_frozen ? __("Unfreeze Supplier") : __("Freeze Supplier");
				frm.add_custom_button(label, () => {
					frappe.confirm(
						info.is_frozen
							? __("Unfreeze this Supplier? It will be usable in transactions again immediately.")
							: __("Freeze this Supplier? No transactions can be created against it until it's unfrozen."),
						() => {
							frm.call("toggle_supplier_freeze").then(() => frm.refresh());
						}
					);
				});
			});
		}
	},
});
